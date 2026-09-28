"""Grading a single submission: compile, run every test case, record a verdict.

This is the orchestration layer for one submission. It owns the order of the
steps and the retry policy; the individual steps live in the sibling modules
so that each can be tested on its own.
"""

import json
import logging as log

from django.conf import settings

from judging import state, verdicts
from judging.checkers import compile_checker
from judging.compilers import compile_submission
from judging.runner import get_cmd_for_language_safeexec, run_grader
from judging.sandbox import (
    check_problem_folder,
    create_submission_folder,
    get_submission_folder,
    list_case_files,
    remove_submission_folder,
)
from judging.utils import compress_output_lines, get_exitcode_stdout_stderr


def grade_claimed_submission(submission, number_of_executions=None):
    """Grade a submission a worker has already claimed.

    The submission must be in compiling: the claim is what stops a second
    worker from grading the same work. Both the Celery task and the legacy
    ``manage.py grader`` command come through here, so that the steps and
    their order only exist once.
    """
    if number_of_executions is None:
        number_of_executions = settings.GRADER_NUMBER_OF_EXECUTIONS

    create_submission_folder(submission)
    if check_problem_folder(submission.problem):
        if compile_submission(submission):
            grade_submission(submission, number_of_executions)
    else:
        log.error(
            "There was a problem with the problem folder %s for submission #%d",
            get_submission_folder(submission),
            submission.id,
        )
        state.set_internal_error(submission, "internal error, problem not ready")

    if not settings.DEBUG:
        # In DEBUG the folder is left behind to make debugging easier.
        remove_submission_folder(submission)


def grade_submission(submission, number_of_executions):
    """Judge a submission and record its verdict.

    ``number_of_executions`` is how many times each test case is run. Only
    timing verdicts are retried: they depend on the machine being momentarily
    busy, so a second run settles it, whereas re-running a wrong answer would
    just waste time.
    """
    log.info("Grading submission: %d", submission.id)
    state.mark_as_running(submission)

    # Extract the required data
    problem = submission.problem
    checker = problem.checker
    compiler = submission.compiler
    language = compiler.language.lower()
    submission_folder = get_submission_folder(submission)

    # The checker
    checker_command = compile_checker(checker, submission_folder)
    if not checker_command:
        log.error(
            "Could not compile checker (checker=%s, folder=%s, submission id=%d)",
            str(checker),
            submission_folder,
            submission.id,
        )
        state.set_internal_error(submission, "internal error compiling checker")
        return

    # The memory limits
    time_limit = problem.time_limit_for_compiler(compiler)
    memory_limit = problem.memory_limit_for_compiler(compiler)

    # Build the command
    cmd = get_cmd_for_language_safeexec(
        submission, compiler, language, time_limit, memory_limit
    )
    log.debug("Run cmd: %s", cmd)

    # Input & output folders
    i_files, o_files = list_case_files(problem)

    current_test = 0
    maximum_execution_time, maximum_consumed_memory = 0, 0
    judgement_details = ""
    result = verdicts.ACCEPTED

    for input_file, answer_file in zip(i_files, o_files):
        log.debug("Running test cases: in=%s, out=%s", input_file, answer_file)
        try:
            current_test += 1

            for _ in range(number_of_executions):
                # NOTE: Setting result to accepted here is needed in
                # case we retry after a TLE/ILE judgment. As a follow
                # up, we need to revisit the logic of this section and
                # refactor to make it more readable.
                result = verdicts.ACCEPTED
                data, _, out, err = run_grader(cmd, input_file, submission_folder)
                invocation_verdict = data["invocation_verdict"]
                exit_code = data["exit_code"]
                consumed_memory = data["consumed_memory"]
                execution_time = data["execution_time"]

                if invocation_verdict in [
                    "TIME_LIMIT_EXCEEDED",
                    "IDLENESS_LIMIT_EXCEEDED",
                ]:
                    execution_time = time_limit * 1000

                if invocation_verdict != "SUCCESS":
                    comment = result = verdicts.verdict_for_invocation(
                        invocation_verdict
                    )
                    if invocation_verdict in ["CRASH", "FAIL"]:
                        comment = "internal error, executing submission"
                elif exit_code != 0:
                    result = verdicts.RUNTIME_ERROR
                    compressed_error = compress_output_lines(err)
                    comment = ("runtime error\n\n" + compressed_error).strip()
                else:
                    rc, out, err = get_exitcode_stdout_stderr(
                        cmd=checker_command % (input_file, "output.txt", answer_file),
                        cwd=submission_folder,
                    )
                    out = out.strip()
                    err = err.strip()
                    comment = out or err
                    if rc != 0:
                        result = verdicts.WRONG_ANSWER
                if result == verdicts.INTERNAL_ERROR:
                    # Log the raw safeexec stderr (`err`). When safeexec can't
                    # run the submission (e.g. it isn't setuid-root so it fails
                    # to setgid/setuid into the `judge` user) its output isn't
                    # the expected 4-line format, parse_safeexec_output falls
                    # back to FAIL, and we land here. Without `err` this is
                    # silent and impossible to diagnose from the grader output.
                    log.error(
                        "Internal error grading submission #%d on input %s: "
                        "parsed=%s, safeexec stderr=%r",
                        submission.id,
                        input_file,
                        json.dumps(data),
                        err,
                    )
                if result not in verdicts.RETRYABLE_VERDICTS:
                    break  # abort retry of the test if is not time related

            maximum_execution_time = max(maximum_execution_time, execution_time)
            maximum_consumed_memory = max(maximum_consumed_memory, consumed_memory)
            judgement_details += "Case#%d [%d bytes][%d ms]: %s\n" % (
                current_test,
                consumed_memory,
                execution_time,
                comment,
            )
        except Exception as e:
            log.error("Unexpected error running test case: %s", str(e))
            result = verdicts.INTERNAL_ERROR
        if result != verdicts.ACCEPTED:
            break
    state.set_verdict(
        submission,
        result,
        execution_time=maximum_execution_time,
        memory_used=maximum_consumed_memory,
        judgement_details=judgement_details,
    )

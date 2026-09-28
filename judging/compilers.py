"""Turning the source of a submission into something executable."""

import json
import logging as log
import os

from judging import state
from judging.sandbox import get_submission_folder
from judging.utils import get_exitcode_stdout_stderr


def write_source_file(submission, compiler, submission_folder) -> str:
    """Write the submission source under the name the compiler expects.

    Java and Kotlin are special: the grader runs a fixed entry point, so the
    file has to be named after the class rather than after the submission.
    """
    src_file = "%d.%s" % (submission.id, compiler.file_extension)
    if compiler.language.lower() == "java":
        src_file = "Main.java"
    elif compiler.language.lower() == "kotlin":
        src_file = "Main.kt"

    with open(os.path.join(submission_folder, src_file), "wb") as f:
        f.write(submission.source.encode("utf8"))
    return src_file


def get_executable_name(submission, compiler) -> str:
    exe_file = "%d.%s" % (submission.id, compiler.exec_extension)
    if compiler.language.lower() == "java":
        exe_file = "Main.class"
    elif compiler.language.lower() == "kotlin":
        exe_file = "MainKt.class"
    return exe_file


def compile_submission(submission) -> bool:
    """Compile a submission, returning whether it produced an executable.

    Interpreted languages are not compiled at all, so they always succeed and
    the source is simply run later. A compiler that exits non-zero is not
    treated as a failure on its own: what matters is whether the executable
    exists afterwards.
    """
    log.debug("Compiling submission #%d", submission.id)
    compiler = submission.compiler

    state.mark_as_compiling(submission)

    submission_folder = get_submission_folder(submission)
    src_file = write_source_file(submission, compiler, submission_folder)
    exe_file = get_executable_name(submission, compiler)

    if compiler.language.lower() in ["python", "javascript"]:
        return True

    try:
        env = json.loads(compiler.env) if compiler.env else None
        code, out, err = get_exitcode_stdout_stderr(
            cmd='"%s" %s'
            % (compiler.path, compiler.arguments.format(src_file, exe_file)),
            cwd=submission_folder,
            env=env,
        )

        if code != 0:
            # Some error ocurred
            log.warning(
                "Compiler exited with non-zero code (%d), stdout: %s, stderr: %s",
                code,
                out,
                err,
            )

        if os.path.exists(os.path.join(submission_folder, exe_file)):
            return True

        details = ""
        details = details + out if out else details
        details = details + err if err else details
        state.set_compilation_error(submission, details)
    except Exception as e:
        log.error(
            "Internal error during compilation, submission: #%d, error: %s",
            submission.id,
            str(e),
        )
        state.set_internal_error(submission, "internal error during compilation phase")
    return False

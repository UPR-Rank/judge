"""Running a submission under safeexec, and reading back what happened.

This module owns the two decisions that define the sandbox: the command line
that safeexec is given (where the time and memory limits are enforced) and the
interpretation of the 4-line report it writes back.
"""

import json
import logging as log
import os
import re
from math import ceil

from judging.utils import get_exitcode_stdout_stderr

# https://github.com/UPR-Rank/safeexec/blob/22cd436f2d384d2a933428c5f5f8240c406f08db/safeexec.c#L38C20-L38C27
LARGECONST = 4194304  # 4GiB


def get_cmd_for_language_safeexec(
    submission,
    compiler,
    lang: str,
    time_limit: int,
    memory_limit: int,
) -> str:
    """Get language-specific command, using safeexec"""
    # note that safeexec should be in PATH, see the docker/common/make_safeexec.sh script
    # note2:we need to pipe the data directly, patching safeexec to accept --stdin/--stdout
    #   (like i did a time ago :p ) may lead to some unwanted security issues (RCE/Privilege
    #    escalation/Information diclosure) all because it uses the SUID bit
    # note3:Shall we consider only CPU seconds and ignore the delay caused by the syscalls?
    if lang == "java":
        cmd = f"safeexec --stack {LARGECONST} --nproc 20 --mem {memory_limit*1024} --cpu {time_limit} --vmrss --exec /usr/bin/java -Dfile.encoding=UTF-8 -XX:+UseSerialGC -Xms32m -Xmx{memory_limit}M -Xss64m -DMOG=true Main"
        return cmd
    elif lang == "kotlin":
        return f"safeexec --stack {LARGECONST} --nproc 20 --mem {memory_limit*1024} --cpu {time_limit} --vmrss --exec /opt/kotlin-1.7.21/bin/kotlin -Dfile.encoding=UTF-8 -J-XX:+UseSerialGC -J-Xms32M -J-Xmx{memory_limit*1024}M -J-Xss64m -J-DMOG=true MainKt"
    elif lang == "csharp":
        return f"safeexec --stack {LARGECONST} --nproc 6 --mem {memory_limit*1024} --cpu {time_limit} --vmrss --exec /usr/local/bin/mono ./{submission.id}.{compiler.exec_extension}"
    elif lang in ["python", "javascript", "python2", "python3"]:
        fmt_args = compiler.arguments.format(
            "%d.%s" % (submission.id, compiler.file_extension)
        )
        return f'safeexec --stack {LARGECONST} --mem {memory_limit*1024} --cpu {time_limit} --clock {time_limit} --exec "{compiler.path}" {fmt_args}'
    else:
        # Compiled binary
        return f"safeexec --stack {LARGECONST} --mem {memory_limit*1024} --cpu {time_limit} --clock {time_limit} --exec ./{submission.id}.{compiler.exec_extension}"


def parse_safeexec_output(out: str) -> dict:
    """Read the 4-line usage report safeexec writes to stderr.

    The report looks like::

        <message>
        elapsed time: <n> seconds
        memory usage: <n> kbytes
        cpu usage: <n.nnn> seconds

    Anything else (an empty report, or safeexec failing outright because its
    setuid bit is missing) yields FAIL, which the caller reports as an
    internal error.
    """
    invocation_verdict = "FAIL"
    exit_code = 1
    processor_user_mode_time = 0
    processor_kernel_mode_time = 0
    passed_time = 0
    consumed_memory = 0
    comment = None
    execution_time = 0

    lines = out.splitlines()
    if len(lines) == 4:
        message = lines[0].strip()

        memory_match = re.match(r"memory usage: (\d+) kbytes", lines[2].strip())
        cpu_match = re.match(r"cpu usage: (\d+(\.\d+)?) seconds", lines[3].strip())

        if "Internal Error" == message:
            invocation_verdict = "INTERNAL_ERROR"
        elif "Invalid Function" == message:
            invocation_verdict = "RUNTIME_ERROR"
        elif "Time Limit Exceeded" == message:
            invocation_verdict = "TIME_LIMIT_EXCEEDED"
        elif "Output Limit Exceeded" == message:
            invocation_verdict = "RUNTIME_ERROR"
        elif "Command terminated by signal" in message:
            invocation_verdict = "RUNTIME_ERROR"
            comment = message
        elif "Command exited with non-zero status" in message:
            invocation_verdict = "RUNTIME_ERROR"
            comment = message
        elif "Memory Limit Exceeded" == message:
            invocation_verdict = "MEMORY_LIMIT_EXCEEDED"
        elif "OK" == message:
            invocation_verdict = "SUCCESS"
            exit_code = 0

        if memory_match is not None:
            mem = int(memory_match.group(1))
            consumed_memory = mem * 1024  # KiB -> Bytes

        if cpu_match is not None:
            tme = float(cpu_match.group(1))
            millis = ceil(tme * 1000)  # Secs -> Millis
            processor_user_mode_time = millis
            processor_kernel_mode_time = millis
            passed_time = millis
            execution_time = millis

    return {
        "invocation_verdict": invocation_verdict,
        "exit_code": exit_code,
        "processor_user_mode_time": processor_user_mode_time,
        "passed_time": passed_time,
        "processor_kernel_mode_time": processor_kernel_mode_time,
        "comment": comment,
        "consumed_memory": consumed_memory,
        "execution_time": execution_time,
    }


def run_safeexec(
    cmd: str,
    input_file: str,
    submission_folder: str,
    output_file: str = "output.txt",
):
    """Run one test case under safeexec, piping the case in and the answer out.

    The submission is executed as the unprivileged ``judge`` user; safeexec is
    setuid root, and that is the only reason it can drop privileges at all.
    """
    with open(os.path.join(submission_folder, output_file), "wb") as stdout:
        with open(os.path.join(submission_folder, input_file), "rb") as stdin:
            # Need to pipe manually
            ret, out, err = get_exitcode_stdout_stderr(
                cmd=cmd.format(
                    **{"input-file": input_file, "output-file": output_file}
                ),
                cwd=submission_folder,
                stdin=stdin,
                stdout=stdout,
                user="judge",
            )

    # Check for errors
    if ret != 0:
        log.debug(
            "(Grading) Process exited with non-zero result code (code=%d) stdout=%s, stderr=%s",
            ret,
            out,
            err,
        )
    return parse_safeexec_output(err), ret, out, err


def run_grader(
    cmd: str,
    input_file: str,
    submission_folder: str,
):
    """Run a single test case in safeexec"""
    result, ret, out, err = run_safeexec(cmd, input_file, submission_folder)
    log.debug("Submission ran: %s", json.dumps(result))
    return result, ret, out or "", err or ""

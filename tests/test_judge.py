"""Characterization tests for the judging pipeline.

These tests pin down the current behaviour of the judge before it is
refactored (Sprint 1) and before it is moved onto a task queue (Sprint 2).
They deliberately assert the *existing* output, including two places where
the current implementation looks inconsistent, so that any change in
grading behaviour shows up as a test failure rather than as a silently
different verdict:

  * Kotlin is given a heap 1024x larger than the problem's memory limit,
    while Java is given the correct one (see
    ``test_kotlin_heap_limit_is_1024_times_the_problem_limit``).
  * The compiled and interpreted runtimes are limited with ``--clock``
    (wall clock), but the virtual-machine runtimes are not (see
    ``test_vm_runtimes_have_no_wall_clock_limit``).

None of these tests touch the database, the sandbox or safeexec itself, so
they run without Docker.
"""

import os
import shutil
import tempfile
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from api.management.commands.__utils import compress_output_lines
from api.management.commands.grader import (
    LARGECONST,
    check_problem_folder,
    get_cmd_for_language_safeexec,
    parse_safeexec_output,
)


def safeexec_report(message, memory_kbytes=1424, cpu_seconds="1.000"):
    """Build a 4-line safeexec usage report.

    safeexec writes the report to stderr as::

        <message>
        elapsed time: <n> seconds
        memory usage: <n> kbytes
        cpu usage: <n.nnn> seconds
    """
    return "\n".join(
        [
            message,
            "elapsed time: 1 seconds",
            "memory usage: %d kbytes" % memory_kbytes,
            "cpu usage: %s seconds" % cpu_seconds,
        ]
    )


class ParseSafeexecOutputTestCase(SimpleTestCase):
    """The verdict, memory and time a run is credited with."""

    def test_ok_is_success(self):
        result = parse_safeexec_output(safeexec_report("OK"))

        self.assertEqual(result["invocation_verdict"], "SUCCESS")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["consumed_memory"], 1424 * 1024)
        self.assertEqual(result["execution_time"], 1000)
        self.assertIsNone(result["comment"])

    def test_time_limit_exceeded(self):
        result = parse_safeexec_output(
            safeexec_report("Time Limit Exceeded", 1424, "2.000")
        )

        self.assertEqual(result["invocation_verdict"], "TIME_LIMIT_EXCEEDED")
        self.assertEqual(result["execution_time"], 2000)

    def test_memory_limit_exceeded(self):
        result = parse_safeexec_output(
            safeexec_report("Memory Limit Exceeded", 32884, "0.416")
        )

        self.assertEqual(result["invocation_verdict"], "MEMORY_LIMIT_EXCEEDED")
        self.assertEqual(result["consumed_memory"], 32884 * 1024)
        self.assertEqual(result["execution_time"], 416)

    def test_non_zero_exit_is_runtime_error(self):
        result = parse_safeexec_output(
            safeexec_report("Command exited with non-zero status (1)", 64, "0.000")
        )

        self.assertEqual(result["invocation_verdict"], "RUNTIME_ERROR")
        self.assertEqual(result["comment"], "Command exited with non-zero status (1)")

    def test_terminated_by_signal_is_runtime_error(self):
        result = parse_safeexec_output(
            safeexec_report("Command terminated by signal (11)", 64, "0.000")
        )

        self.assertEqual(result["invocation_verdict"], "RUNTIME_ERROR")
        self.assertEqual(result["comment"], "Command terminated by signal (11)")

    def test_internal_error(self):
        result = parse_safeexec_output(safeexec_report("Internal Error"))

        self.assertEqual(result["invocation_verdict"], "INTERNAL_ERROR")

    def test_invalid_function_is_runtime_error(self):
        result = parse_safeexec_output(safeexec_report("Invalid Function"))

        self.assertEqual(result["invocation_verdict"], "RUNTIME_ERROR")

    def test_output_limit_exceeded_is_runtime_error(self):
        result = parse_safeexec_output(safeexec_report("Output Limit Exceeded"))

        self.assertEqual(result["invocation_verdict"], "RUNTIME_ERROR")

    def test_cpu_time_is_rounded_up_to_milliseconds(self):
        result = parse_safeexec_output(safeexec_report("OK", 64, "0.0001"))

        self.assertEqual(result["execution_time"], 1)

    def test_unparsable_report_falls_back_to_internal_error(self):
        """A report that is not 4 lines long yields FAIL, which the caller
        maps to "internal error". This is what happens when safeexec itself
        fails, e.g. when its setuid bit is missing."""
        result = parse_safeexec_output("safeexec: something went wrong")

        self.assertEqual(result["invocation_verdict"], "FAIL")
        self.assertEqual(result["exit_code"], 1)
        self.assertEqual(result["consumed_memory"], 0)
        self.assertEqual(result["execution_time"], 0)


class CompressOutputLinesTestCase(SimpleTestCase):
    """Compiler and runtime errors can be enormous; they are truncated."""

    def test_short_output_is_untouched(self):
        self.assertEqual(compress_output_lines("one\ntwo"), "one\ntwo")

    def test_output_is_capped_at_51_lines(self):
        output = "\n".join("line %d" % i for i in range(200))
        compressed = compress_output_lines(output)

        lines = compressed.split("\n")
        self.assertEqual(len(lines), 51)
        self.assertEqual(lines[0], "line 0")
        self.assertEqual(lines[25], "...")
        self.assertEqual(lines[26], "line 175")
        self.assertEqual(lines[-1], "line 199")


# Mirrors the compiler rows created by populate_local_dev, including the
# str.format placeholders in ``arguments``: the interpreted branch formats a
# single {0}, the compiled branch formats {0} and {1}.
COMPILER_STUBS = {
    "c++": SimpleNamespace(
        path="/opt/gcc-11.3.0/bin/g++",
        file_extension="cpp",
        exec_extension="exe",
        arguments="-O2 -std=c++11 {0} -o {1}",
    ),
    "python": SimpleNamespace(
        path="/usr/bin/python3",
        file_extension="py",
        exec_extension="exe",
        arguments="-O {0}",
    ),
    "java": SimpleNamespace(
        path="/opt/java/openjdk/bin/javac",
        file_extension="java",
        exec_extension="exe",
        arguments='-cp ".;*" {0}',
    ),
    "kotlin": SimpleNamespace(
        path="/opt/kotlin-1.7.21/bin/kotlinc",
        file_extension="kt",
        exec_extension="exe",
        arguments="{0}",
    ),
    "csharp": SimpleNamespace(
        path="/usr/bin/mcs",
        file_extension="cs",
        exec_extension="exe",
        arguments="{0}",
    ),
}


class SafeexecCommandTestCase(SimpleTestCase):
    """The sandbox command line, which is where the limits are enforced."""

    def setUp(self):
        super().setUp()
        self.submission = SimpleNamespace(id=42)

    def build(self, language, time_limit=2, memory_limit=256):
        return get_cmd_for_language_safeexec(
            self.submission,
            COMPILER_STUBS[language],
            language,
            time_limit,
            memory_limit,
        )

    def test_compiled_binary_command(self):
        self.assertEqual(
            self.build("c++"),
            "safeexec --stack 4194304 --mem %d --cpu 2 --clock 2 --exec ./42.exe"
            % (256 * 1024),
        )

    def test_interpreted_command_runs_the_compiler_path(self):
        self.assertEqual(
            self.build("python"),
            "safeexec --stack 4194304 --mem %d --cpu 2 --clock 2 "
            '--exec "/usr/bin/python3" -O 42.py' % (256 * 1024),
        )

    def test_java_command(self):
        cmd = self.build("java")

        self.assertEqual(
            cmd,
            "safeexec --stack 4194304 --nproc 20 --mem %d --cpu 2 --vmrss "
            "--exec /usr/bin/java -Dfile.encoding=UTF-8 -XX:+UseSerialGC "
            "-Xms32m -Xmx256M -Xss64m -DMOG=true Main" % (256 * 1024),
        )

    def test_java_heap_matches_the_problem_memory_limit(self):
        self.assertIn("-Xmx256M", self.build("java"))

    def test_kotlin_heap_limit_is_1024_times_the_problem_limit(self):
        """Known inconsistency, pinned on purpose.

        ``Problem.memory_limit`` is expressed in MB (see the model's
        ``verbose_name``), so a 256 MB problem should give Kotlin
        ``-J-Xmx256M``, exactly like Java. The current code multiplies by
        1024 a second time and asks for 256 GB. safeexec's ``--mem`` limit
        is still correct, so this most likely does not change verdicts, but
        it should be confirmed and fixed deliberately rather than by
        accident during the Sprint 1 refactor.
        """
        self.assertIn("-J-Xmx%dM" % (256 * 1024), self.build("kotlin"))

    def test_csharp_command(self):
        cmd = self.build("csharp")

        self.assertEqual(
            cmd,
            "safeexec --stack 4194304 --nproc 6 --mem %d --cpu 2 --vmrss "
            "--exec /usr/local/bin/mono ./42.exe" % (256 * 1024),
        )

    def test_vm_runtimes_have_no_wall_clock_limit(self):
        """Known inconsistency, pinned on purpose.

        Compiled and interpreted runtimes are bounded by both --cpu (CPU
        time) and --clock (wall clock). The VM runtimes (java, kotlin,
        csharp) only get --cpu, so a submission that blocks on, say, stdin
        can burn wall-clock time indefinitely.
        """
        for language in ["java", "kotlin", "csharp"]:
            with self.subTest(language=language):
                self.assertNotIn("--clock", self.build(language))

    def test_stack_limit_is_four_gibibytes(self):
        self.assertEqual(LARGECONST, 4194304)
        for language in ["c++", "python", "java", "kotlin", "csharp"]:
            with self.subTest(language=language):
                self.assertIn("--stack 4194304", self.build(language))


class CheckProblemFolderTestCase(SimpleTestCase):
    """A problem is only gradeable if its test cases are actually there."""

    def setUp(self):
        super().setUp()
        self.problems_folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.problems_folder, True)
        self.problem = SimpleNamespace(id=7)

    def make_problem(self, inputs, outputs):
        base = os.path.join(self.problems_folder, "7")
        for name, files in (("inputs", inputs), ("outputs", outputs)):
            folder = os.path.join(base, name)
            os.makedirs(folder)
            for filename in files:
                with open(os.path.join(folder, filename), "w") as handle:
                    handle.write("data")
        return base

    def test_complete_problem_folder(self):
        self.make_problem(["1.in", "2.in"], ["1.out", "2.out"])

        with override_settings(PROBLEMS_FOLDER=self.problems_folder):
            self.assertTrue(check_problem_folder(self.problem))

    def test_missing_problem_folder(self):
        with override_settings(PROBLEMS_FOLDER=self.problems_folder):
            self.assertFalse(check_problem_folder(self.problem))

    def test_missing_outputs_folder(self):
        self.make_problem(["1.in"], [])

        with override_settings(PROBLEMS_FOLDER=self.problems_folder):
            os.rmdir(os.path.join(self.problems_folder, "7", "outputs"))

            self.assertFalse(check_problem_folder(self.problem))

    def test_unequal_number_of_inputs_and_outputs(self):
        self.make_problem(["1.in", "2.in"], ["1.out"])

        with override_settings(PROBLEMS_FOLDER=self.problems_folder):
            self.assertFalse(check_problem_folder(self.problem))

"""Verdict names and the translation of safeexec outcomes into them.

The names here are matched case-insensitively against ``Result.name`` when a
submission is updated, so they are lower case on purpose. Keeping them in one
place avoids the previous situation where the same literal appeared in the
grader, in the views and in the rating code.
"""

# Verdict names, as stored in Result.name.
ACCEPTED = "accepted"
COMPILATION_ERROR = "compilation error"
IDLENESS_LIMIT_EXCEEDED = "idleness limit exceeded"
INTERNAL_ERROR = "internal error"
MEMORY_LIMIT_EXCEEDED = "memory limit exceeded"
RUNTIME_ERROR = "runtime error"
TIME_LIMIT_EXCEEDED = "time limit exceeded"
WRONG_ANSWER = "wrong answer"

# How a verdict reported by safeexec maps onto a Result name. An outcome that
# is missing from this table raises a KeyError, which the caller in
# judging.service turns into an internal error.
INVOCATION_TO_VERDICT = {
    "SECURITY_VIOLATION": RUNTIME_ERROR,
    "MEMORY_LIMIT_EXCEEDED": MEMORY_LIMIT_EXCEEDED,
    "TIME_LIMIT_EXCEEDED": TIME_LIMIT_EXCEEDED,
    "IDLENESS_LIMIT_EXCEEDED": IDLENESS_LIMIT_EXCEEDED,
    "CRASH": INTERNAL_ERROR,
    "FAIL": INTERNAL_ERROR,
    "RUNTIME_ERROR": RUNTIME_ERROR,
    "INTERNAL_ERROR": INTERNAL_ERROR,
}

# A submission is only retried when the verdict was a timing one. Timing
# verdicts depend on the machine being momentarily busy, so the grader gives
# them a second chance; anything else would be deterministic.
RETRYABLE_VERDICTS = frozenset({TIME_LIMIT_EXCEEDED, IDLENESS_LIMIT_EXCEEDED})


def verdict_for_invocation(invocation_verdict):
    """Translate a safeexec outcome into a Result name.

    Raises KeyError for an outcome that is not in the table, which callers
    treat as an internal error.
    """
    return INVOCATION_TO_VERDICT[invocation_verdict]

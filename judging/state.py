"""Submission state transitions.

A submission moves through pending -> compiling -> running -> <verdict>. The
intermediate two are not verdicts, they only say that the grader has taken
the submission, and they exist so that the web app can show the progress.

``Result`` rows are looked up by name, case-insensitively, which is how the
rest of the project refers to them.
"""

import logging as log
import time

from django.db import DatabaseError, transaction

from api.models import Result, Submission

PENDING = "pending"
COMPILING = "compiling"
RUNNING = "running"


def get_result(name):
    return Result.objects.get(name__iexact=name)


def mark_as_pending(submission):
    """Put a submission back in the queue."""
    submission.result = get_result(PENDING)
    submission.save()


def mark_as_compiling(submission):
    submission.result = get_result(COMPILING)
    submission.save()


def mark_as_running(submission):
    """Marks a submission as running"""
    submission.result = get_result(RUNNING)
    submission.save()


def set_verdict(
    submission,
    verdict: str,
    execution_time: int = 0,
    memory_used: int = 0,
    judgement_details=None,
):
    """Record the final outcome of a submission."""
    submission.execution_time = execution_time
    submission.memory_used = memory_used
    submission.result = get_result(verdict)
    submission.judgement_details = judgement_details
    submission.save()


def set_internal_error(submission, judgement_details=None):
    set_verdict(submission, "internal error", 0, 0, judgement_details)


def set_compilation_error(submission, judgement_details=None):
    set_verdict(submission, "compilation error", 0, 0, judgement_details)


def claim_next_pending_submission():
    """Atomically take the oldest pending submission, or return None.

    The whole claim happens in one transaction with ``SELECT ... FOR UPDATE
    NOWAIT``, so that two graders racing for the same submission cannot both
    get it: the loser raises instead of blocking, and moves on to the next
    one. The submission is flipped to compiling inside the same transaction,
    so it is invisible to other graders as soon as it is claimed.
    """
    with transaction.atomic():
        submission = (
            Submission.objects.select_for_update(nowait=True)
            .select_related("compiler", "problem")
            .filter(result__name__iexact=PENDING)
            .order_by("id")
            .first()
        )
        if submission:
            log.debug(
                "Received submission #%d, marking as '%s' and proceed",
                submission.id,
                COMPILING,
            )
            mark_as_compiling(submission)
    return submission


def reset_to_pending(submission_id, sleep=5, trials=100):
    """Return a submission to the queue, retrying if the database is busy.

    Used when the grader loses a submission it had already claimed: the
    submission is only left in compiling or running if the grader itself
    dies, which a database error can precede.
    """
    log.debug("Submission #%d -> pending", submission_id)
    success = False
    while not success and trials > 0:
        try:
            with transaction.atomic():
                submission = Submission.objects.get(pk=submission_id)
                submission.result = get_result(PENDING)
                submission.save()
            success = True
        except DatabaseError as e:
            log.error("Unexpected database error: %s", str(e))
            success = False
            trials -= 1
            time.sleep(sleep)

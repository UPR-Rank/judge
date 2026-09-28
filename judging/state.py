"""Submission state transitions.

A submission moves through pending -> compiling -> running -> <verdict>. The
intermediate two are not verdicts, they only say that the grader has taken
the submission, and they exist so that the web app can show the progress.

``Result`` rows are looked up by name, case-insensitively, which is how the
rest of the project refers to them.
"""

import logging as log
import time
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import Q
from django.utils import timezone

from api.models import Result, Submission

PENDING = "pending"
COMPILING = "compiling"
RUNNING = "running"


def get_result(name):
    return Result.objects.get(name__iexact=name)


def mark_as_pending(submission):
    """Put a submission back in the queue."""
    submission.result = get_result(PENDING)
    submission.claimed_at = None
    submission.save()


def mark_as_compiling(submission):
    submission.result = get_result(COMPILING)
    submission.claimed_at = timezone.now()
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


def claim_submission(submission_id):
    """Take one specific pending submission, or return None.

    Used by the queue, which is told which submission to grade. Returns None
    when the submission is no longer pending, which is how a worker finds out
    that someone else already graded it or that it was rejudged meanwhile.
    """
    with transaction.atomic():
        submission = (
            Submission.objects.select_for_update(nowait=True)
            .select_related("compiler", "problem")
            .filter(pk=submission_id)
            .filter(result__name__iexact=PENDING)
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


def is_stalled(submission, now=None, timeout=None):
    """Whether a claimed submission looks like its worker died.

    A worker that is alive does not sit on a claim for the whole timeout, and
    a submission that was never claimed cannot be stalled, so both are ruled
    out before the age is compared.
    """
    if submission.claimed_at is None:
        return False
    if submission.result.name.lower() not in (COMPILING, RUNNING):
        return False
    if timeout is None:
        timeout = settings.GRADER_CLAIM_TIMEOUT
    if now is None:
        now = timezone.now()
    return submission.claimed_at < now - timedelta(seconds=timeout)


def stalled_submissions(timeout=None, now=None):
    """Submissions a worker took and never finished, oldest claim first."""
    if timeout is None:
        timeout = settings.GRADER_CLAIM_TIMEOUT
    if now is None:
        now = timezone.now()
    return (
        Submission.objects.filter(
            Q(result__name__iexact=COMPILING) | Q(result__name__iexact=RUNNING),
            claimed_at__isnull=False,
            claimed_at__lt=now - timedelta(seconds=timeout),
        )
        .order_by("claimed_at")
        .only("id", "result__name", "claimed_at")
    )


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
                mark_as_pending(submission)
            success = True
        except DatabaseError as e:
            log.error("Unexpected database error: %s", str(e))
            success = False
            trials -= 1
            time.sleep(sleep)

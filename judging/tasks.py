"""The grading queue.

The flow is: a submission is created or rejudged, the view enqueues its id,
and a worker claims it and grades it. Redis carries the ids and PostgreSQL
carries the work, so the queue is a hint and the database is the truth. Three
things follow from that:

  * if enqueueing fails, the submission is still pending in the database and
    the reaper will pick it up, so a submission is never lost;
  * a task is idempotent, because it claims by id and finds nothing to do if
    the submission is no longer pending;
  * a worker that dies leaves its submission claimed, and the reaper puts it
    back once the claim is old enough.
"""

import logging as log

from celery import shared_task
from django.db import DatabaseError, close_old_connections, transaction

from api.models import Submission
from judging import state
from judging.service import grade_claimed_submission


def enqueue_grading(submission_id, on_commit=True):
    """Hand a submission to a worker.

    Called after the submission is created or rejudged. By default the message
    is only sent once that change is committed: a worker that receives the id
    before the row is visible would find nothing to grade and drop it.

    Returns False when the broker could not be reached. That is not fatal —
    the submission is pending in the database, so the reaper covers it.
    """
    from judging.tasks import grade_submission_task

    def send():
        try:
            grade_submission_task.delay(submission_id)
            return True
        except Exception as e:
            log.error(
                "Could not reach the grading queue for submission #%s (%s). "
                "It stays pending and the reaper will pick it up.",
                submission_id,
                e,
            )
            return False

    if on_commit:
        transaction.on_commit(send)
        return None
    return send()


@shared_task(
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=None,
)
def grade_submission_task(self, submission_id):
    """Claim and grade one submission.

    Returns the submission id when it was graded, and None when there was
    nothing to do: either the submission is gone, or it is no longer pending
    because another worker took it or it was rejudged in the meantime.
    """
    submission = None
    try:
        submission = state.claim_submission(submission_id)
        if submission is None:
            log.debug("Submission #%s is not pending, nothing to grade", submission_id)
            return None
        grade_claimed_submission(submission)
        return submission_id
    except DatabaseError as e:
        # Same reasoning as the legacy grader: a database error must not kill
        # the worker, and the claimed submission goes back to the queue.
        log.error("Unexpected database error grading #%s: %s", submission_id, str(e))
        close_old_connections()
        if submission:
            state.reset_to_pending(submission.id)
        return None
    except Exception as e:
        # Grading raised something unexpected (a missing toolchain, a checker
        # that cannot be written). The submission is left claimed and the
        # reaper will return it to the queue, which is better than a worker
        # that silently stops grading.
        log.exception("Unexpected error grading submission #%s: %s", submission_id, e)
        raise


@shared_task
def reap_stuck_submissions():
    """Return submissions stranded by a dead worker to the queue.

    Run periodically by celery beat. A submission that has been claimed for
    longer than the claim timeout is almost certainly not being worked on any
    more, because a worker that is still alive keeps nothing that long.

    Only submissions whose claim is old enough are touched, and each one is
    reset and re-enqueued in its own transaction, so a crash in the middle
    costs one submission a delay rather than the whole sweep.
    """
    stale_ids = list(state.stalled_submissions().values_list("id", flat=True))
    if not stale_ids:
        return 0

    log.warning(
        "Reaper: %d submission(s) stalled past the claim timeout: %s",
        len(stale_ids),
        ", ".join(str(i) for i in stale_ids),
    )
    requeued = 0
    for submission_id in stale_ids:
        with transaction.atomic():
            # Re-check under the row lock: the worker may have finished
            # between the query above and now, and re-queuing a submission
            # that already has a verdict would grade it a second time.
            submission = (
                Submission.objects.select_for_update(nowait=True)
                .select_related("result")
                .filter(pk=submission_id)
                .first()
            )
            if submission is None or not state.is_stalled(submission):
                continue
            state.mark_as_pending(submission)
        # Only enqueue once the reset is committed, otherwise a worker could
        # receive the id and find the submission still claimed.
        enqueue_grading(submission_id)
        requeued += 1
    return requeued

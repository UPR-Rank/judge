"""The grading queue: enqueueing, claiming, and recovering stalled work.

These tests never run a real grader. Compilation and execution are stubbed,
because what matters here is the bookkeeping around them: that a submission
is handed out once, that a lost message costs a delay and not a submission,
and that a worker which dies does not strand its work.
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from api.models import Result
from judging import state, tasks

from . import FixturedTestCase


def ensure_worker_results():
    """Fetch the Compiling and Running results a claim passes through.

    Migration api.0050 creates every standard result, so these rows already
    exist. They are looked up case-insensitively because that is how the
    grader finds them, which is also why nothing here may create a
    differently-cased duplicate: a second row would match the same lookup and
    make it ambiguous.
    """
    return (
        Result.objects.get_or_create(
            name__iexact="compiling",
            defaults={"name": "Compiling", "color": "orange", "penalty": False},
        )[0],
        Result.objects.get_or_create(
            name__iexact="running",
            defaults={"name": "Running", "color": "orange", "penalty": False},
        )[0],
    )


@override_settings(CELERY_TASK_ALWAYS_EAGER=False)
class EnqueueGradingTestCase(FixturedTestCase):
    def setUp(self):
        super(EnqueueGradingTestCase, self).setUp()
        ensure_worker_results()
        self.user = self.newUser(username="queue-user", is_active=True)
        self.instance = self.newContestInstance(self.running_contest, self.user)
        self.submission = self.newSubmission(
            self.instance,
            self.user,
            problem=self.problem1,
            result=self.pending,
        )

    def test_enqueue_hands_the_submission_id_to_a_worker(self):
        with patch.object(tasks.grade_submission_task, "delay") as delay:
            self.assertTrue(tasks.enqueue_grading(self.submission.id, on_commit=False))
        delay.assert_called_once_with(self.submission.id)

    def test_enqueue_waits_for_the_transaction_to_commit(self):
        # A worker that receives the id before the row is committed would
        # find nothing pending and drop the submission.
        with patch.object(tasks.grade_submission_task, "delay") as delay:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                self.assertIsNone(tasks.enqueue_grading(self.submission.id))
                # Nothing is sent while the transaction is still open.
                delay.assert_not_called()
            self.assertEqual(len(callbacks), 1)
            delay.assert_called_once_with(self.submission.id)

    def test_a_broker_that_is_down_does_not_lose_the_submission(self):
        # The queue is a hint and the database is the truth, so failing to
        # reach Redis must not raise: the submission is still pending and the
        # reaper will pick it up.
        with patch.object(
            tasks.grade_submission_task, "delay", side_effect=OSError("no redis")
        ):
            self.assertFalse(tasks.enqueue_grading(self.submission.id, on_commit=False))

        self.submission.refresh_from_db()
        self.assertEqual(self.submission.result, self.pending)
        self.assertIsNotNone(state.claim_submission(self.submission.id))


@override_settings(CELERY_TASK_ALWAYS_EAGER=False)
class GradeSubmissionTaskTestCase(FixturedTestCase):
    def setUp(self):
        super(GradeSubmissionTaskTestCase, self).setUp()
        ensure_worker_results()
        self.user = self.newUser(username="task-user", is_active=True)
        self.instance = self.newContestInstance(self.running_contest, self.user)
        self.submission = self.newSubmission(
            self.instance,
            self.user,
            problem=self.problem1,
            result=self.pending,
        )

    def run_task(self, submission_id):
        """Call the task body directly, without going through a broker."""
        with patch("judging.tasks.grade_claimed_submission") as grade:
            grade.side_effect = lambda submission: state.set_verdict(
                submission, "accepted"
            )
            return (
                tasks.grade_submission_task(submission_id),
                grade.call_count,
            )

    def test_the_task_grades_the_submission_it_is_given(self):
        result, graded = self.run_task(self.submission.id)

        self.assertEqual(result, self.submission.id)
        self.assertEqual(graded, 1)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.result, self.accepted)

    def test_the_task_does_nothing_when_the_submission_is_not_pending(self):
        self.submission.result = self.accepted
        self.submission.save()

        result, graded = self.run_task(self.submission.id)

        self.assertIsNone(result)
        self.assertEqual(graded, 0)

    def test_running_the_task_twice_grades_once(self):
        # This is what makes acks_late safe: a redelivered message finds the
        # submission already graded and does nothing.
        self.run_task(self.submission.id)
        result, graded = self.run_task(self.submission.id)

        self.assertIsNone(result)
        self.assertEqual(graded, 0)

    def test_claiming_stamps_the_time_the_claim_was_taken(self):
        state.claim_submission(self.submission.id)

        self.submission.refresh_from_db()
        self.assertIsNotNone(self.submission.claimed_at)


@override_settings(CELERY_TASK_ALWAYS_EAGER=False, GRADER_CLAIM_TIMEOUT=900)
class ReaperTestCase(FixturedTestCase):
    def setUp(self):
        super(ReaperTestCase, self).setUp()
        self.compiling, self.running = ensure_worker_results()
        self.user = self.newUser(username="reaper-user", is_active=True)
        self.instance = self.newContestInstance(self.running_contest, self.user)

    def new_submission(self, result, claimed_at):
        submission = self.newSubmission(
            self.instance,
            self.user,
            problem=self.problem1,
            result=result,
        )
        submission.claimed_at = claimed_at
        submission.save()
        return submission

    def long_ago(self):
        return timezone.now() - timedelta(seconds=3600)

    def just_now(self):
        return timezone.now()

    def test_a_stalled_claim_goes_back_to_the_queue(self):
        submission = self.new_submission(self.compiling, self.long_ago())

        with patch.object(tasks, "enqueue_grading") as enqueue:
            requeued = tasks.reap_stuck_submissions()

        self.assertEqual(requeued, 1)
        submission.refresh_from_db()
        self.assertEqual(submission.result, self.pending)
        self.assertIsNone(submission.claimed_at)
        enqueue.assert_called_once_with(submission.id)

    def test_a_claim_that_is_still_fresh_is_left_alone(self):
        # Stealing work from a worker that is simply slow would grade it twice.
        submission = self.new_submission(self.compiling, self.just_now())

        with patch.object(tasks, "enqueue_grading") as enqueue:
            requeued = tasks.reap_stuck_submissions()

        self.assertEqual(requeued, 0)
        submission.refresh_from_db()
        self.assertEqual(submission.result, self.compiling)
        enqueue.assert_not_called()

    def test_a_finished_submission_is_not_re_graded(self):
        # A worker that finished between the sweep and the re-check must not
        # have its verdict thrown away.
        submission = self.new_submission(self.compiling, self.long_ago())
        state.set_verdict(submission, "accepted")

        with patch.object(tasks, "enqueue_grading") as enqueue:
            requeued = tasks.reap_stuck_submissions()

        self.assertEqual(requeued, 0)
        submission.refresh_from_db()
        self.assertEqual(submission.result, self.accepted)
        enqueue.assert_not_called()

    def test_a_pending_submission_is_not_stalled(self):
        # Nothing was claimed, so there is no worker to have died.
        submission = self.newSubmission(
            self.instance,
            self.user,
            problem=self.problem1,
            result=self.pending,
        )
        submission.claimed_at = None
        submission.save()

        with patch.object(tasks, "enqueue_grading") as enqueue:
            requeued = tasks.reap_stuck_submissions()

        self.assertEqual(requeued, 0)
        submission.refresh_from_db()
        self.assertEqual(submission.result, self.pending)
        enqueue.assert_not_called()

    def test_a_running_submission_stalled_long_enough_is_reaped(self):
        submission = self.new_submission(self.running, self.long_ago())

        with patch.object(tasks, "enqueue_grading"):
            requeued = tasks.reap_stuck_submissions()

        self.assertEqual(requeued, 1)
        submission.refresh_from_db()
        self.assertEqual(submission.result, self.pending)

    def test_nothing_to_do_returns_zero(self):
        with patch.object(tasks, "enqueue_grading") as enqueue:
            self.assertEqual(tasks.reap_stuck_submissions(), 0)
        enqueue.assert_not_called()

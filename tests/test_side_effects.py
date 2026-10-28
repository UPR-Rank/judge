"""Notifications leave the request through the `side_effects` queue.

What matters: the request never waits for SMTP, Discord or the geo-IP lookup;
nothing is queued before the row it talks about is committed; an unreachable
broker costs the notification and not the request; links point at this site,
not at matcomgrader.com; and none of it lands on the grader's queue.
"""

import smtplib
from unittest.mock import patch

from django.contrib.sites.models import Site
from django.core import mail
from django.test import RequestFactory, override_settings

from api.models import Clarification, Comment, ContestPermission, Message, RatingChange
from judging.celery import app
from mog import ratings, tasks, webhooks
from palantir import utils as palantir_utils

from . import FixturedTestCase


class EagerTasksMixin:
    """Run tasks in-process, and the on-commit callbacks that queue them."""

    def setUp(self):
        super().setUp()
        eager = self.settings(CELERY_TASK_ALWAYS_EAGER=True)
        eager.enable()
        self.addCleanup(eager.disable)
        Site.objects.filter(pk=1).update(domain="judge.upr.edu.cu")


class EmailTestCase(EagerTasksMixin, FixturedTestCase):
    def test_message_is_mailed_after_commit_with_a_link_to_this_site(self):
        source, target = self.newUser("source"), self.newUser("target")
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            Message.objects.create(source=source, target=target, subject="s", body="b")
        self.assertEqual(len(mail.outbox), 0)  # nothing before commit

        for callback in callbacks:
            callback()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["target@host.com"])
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn("http://judge.upr.edu.cu", html)
        self.assertNotIn("matcomgrader", html)

    def test_mentions_mail_each_mentioned_user(self):
        author = self.newUser("author")
        self.newUser("ann")
        self.newUser("bob")
        post = self.newPost(author)
        with self.captureOnCommitCallbacks(execute=True):
            Comment(user=author, post=post, body="hi @ann and @bob").save()
        self.assertEqual(
            sorted(m.to[0] for m in mail.outbox), ["ann@host.com", "bob@host.com"]
        )

    def test_clarification_mails_admins_and_contest_judges(self):
        self.newAdmin("admin")
        judge = self.newUser("judge")
        ContestPermission.objects.create(
            user=judge, contest=self.running_contest, role="judge", granted=True
        )
        sender = self.newUser("asker")
        with self.captureOnCommitCallbacks(execute=True):
            Clarification.objects.create(
                contest=self.running_contest, sender=sender, question="?"
            )
        self.assertEqual(
            sorted(m.to[0] for m in mail.outbox), ["admin@host.com", "judge@host.com"]
        )

    def test_one_task_per_recipient(self):
        with patch.object(tasks.send_email_task, "delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                tasks.send_email("s", "m", ["a@x.com", "", "b@x.com"])
        self.assertEqual(delay.call_count, 2)  # blank addresses are skipped

    def test_unreachable_broker_does_not_fail_the_request(self):
        with patch.object(
            tasks.send_email_task, "delay", side_effect=ConnectionError("down")
        ):
            with self.captureOnCommitCallbacks(execute=True):
                tasks.send_email("s", "m", ["a@x.com"])  # must not raise
        self.assertEqual(len(mail.outbox), 0)

    def test_smtp_failure_is_retried(self):
        with patch("mog.tasks.send_mail", side_effect=smtplib.SMTPServerDisconnected):
            with patch.object(tasks.send_email_task, "retry") as retry:
                retry.side_effect = RuntimeError("retried")
                with self.assertRaisesMessage(RuntimeError, "retried"):
                    tasks.send_email_task.apply(args=("s", "m", "a@x.com"), throw=True)
        self.assertTrue(retry.called)


@override_settings(DISCORD_CLARIFICATION_WEBHOOKS=["https://discord.test/hook"])
class WebhookTestCase(EagerTasksMixin, FixturedTestCase):
    def test_clarification_is_posted_with_timeout_and_site_links(self):
        clarification = Clarification.objects.create(
            contest=self.running_contest, sender=self.newUser("asker"), question="?"
        )
        with patch("mog.tasks.requests.post") as post:
            with self.captureOnCommitCallbacks(execute=True):
                webhooks.push_clarification_to_webhooks(clarification, create=True)
        url, kwargs = post.call_args[0][0], post.call_args[1]
        self.assertEqual(url, "https://discord.test/hook")
        self.assertEqual(kwargs["timeout"], 10)
        embed = kwargs["json"]["embeds"][0]
        self.assertTrue(embed["url"].startswith("http://judge.upr.edu.cu/contest/"))


class AccessLogTestCase(FixturedTestCase):
    def message(self, path):
        request = RequestFactory().get(path)
        request.user = self.newUser("visitor")
        return {
            "request": palantir_utils.build_request_message(request),
            "response": {},
        }

    def test_logged_through_the_queue(self):
        with patch("palantir.tasks.log_access_task.delay") as delay:
            palantir_utils.log_access_eventually(self.message("/contests/"))
        self.assertEqual(delay.call_count, 1)

    def test_filtered_paths_are_not_queued(self):
        with patch("palantir.tasks.log_access_task.delay") as delay:
            palantir_utils.log_access_eventually(self.message("/static/x.css"))
        delay.assert_not_called()

    def test_unreachable_broker_does_not_fail_the_request(self):
        with patch("palantir.tasks.log_access_task.delay", side_effect=OSError):
            palantir_utils.log_access_eventually(self.message("/contests/"))


class RoutingTestCase(FixturedTestCase):
    def queue_of(self, task_name):
        return app.amqp.router.route({}, task_name)["queue"].name

    def test_notifications_never_share_the_grader_queue(self):
        for name in (
            "mog.tasks.send_email_task",
            "mog.tasks.post_to_webhook_task",
            "palantir.tasks.log_access_task",
        ):
            self.assertEqual(self.queue_of(name), "side_effects", name)
        self.assertNotEqual(
            self.queue_of("judging.tasks.grade_submission_task"), "side_effects"
        )


class RatingTestCase(FixturedTestCase):
    def test_failure_keeps_the_previous_rating(self):
        contest = self.past_contest
        user = self.newUser("rated")
        RatingChange.objects.create(
            profile=user.profile, contest=contest, rating=50, rank=1, seed=1
        )
        with patch.object(ratings, "calculate_standing", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                ratings.set_ratings(contest)
        self.assertEqual(contest.rating_changes.count(), 1)

    def test_inconsistent_result_keeps_the_previous_rating(self):
        contest = self.past_contest
        user = self.newUser("rated")
        RatingChange.objects.create(
            profile=user.profile, contest=contest, rating=50, rank=1, seed=1
        )
        with patch.object(ratings, "calculate_standing", return_value=(None, [])):
            with patch.object(ratings, "check_rating_deltas", return_value=False):
                self.assertFalse(ratings.set_ratings(contest))
        self.assertEqual(contest.rating_changes.count(), 1)

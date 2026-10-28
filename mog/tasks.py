"""Notifications that leave the site: e-mails and webhooks.

They used to run inside the request (or in a bare thread started from it), so
a slow SMTP server or Discord held up the page, and a failure was either
swallowed or lost with the thread. They are now Celery tasks on the
``side_effects`` queue (see CELERY_TASK_ROUTES), consumed by the ``tasks``
worker and never by the grader, so a burst of notifications cannot delay a
verdict.

Each task carries plain strings, rendered in the request, so it never depends
on a row that may have changed by the time it runs. Notifications are sent at
most once: a worker that dies mid-send drops the message rather than sending it
twice, and only a failure to reach the server is retried.
"""

import logging as log
import smtplib

import requests
from celery import shared_task
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string

from api import models

NOTIFICATION_TASK = dict(acks_late=False, ignore_result=True, retry_backoff=60)


def site_url():
    """The base URL used in links sent outside the site."""
    return "http://%s" % Site.objects.get(pk=settings.SITE_ID).domain


def enqueue(task, *args):
    """Queue `task` once the current transaction commits.

    Never raises: a notification is not worth failing the request over, so an
    unreachable broker costs that notification and is logged.
    """

    def send():
        try:
            task.delay(*args)
        except Exception as e:
            log.error("Could not queue %s, the notification is lost: %s", task.name, e)

    transaction.on_commit(send)


@shared_task(
    autoretry_for=(smtplib.SMTPException, OSError), max_retries=5, **NOTIFICATION_TASK
)
def send_email_task(subject, message, recipient, html_message=None):
    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient],
        html_message=html_message,
    )


def send_email(subject, message, recipients, html_message=None):
    """Send the same email to several recipients.

    One task per recipient, so a retry after a failure does not send the mail
    again to the ones that already got it.
    """
    for email in recipients:
        if email:
            enqueue(send_email_task, subject, message, email, html_message)


@shared_task(
    autoretry_for=(requests.RequestException,), max_retries=3, **NOTIFICATION_TASK
)
def post_to_webhook_task(webhook, content):
    response = requests.post(webhook, json={"embeds": [content]}, timeout=10)
    response.raise_for_status()


def report_clarification(clarification):
    """Send an email to every admin to handle this
    clarification.

    Parameters
    ----------
    clarification: Clarification,
                   api.models.Clarification instance
    """
    subject = "Clarification request"
    message = render_to_string(
        "mog/email/clarification.txt",
        {
            "clarification": clarification,
            "domain": site_url(),
        },
    )

    admins = set(user.email for user in User.objects.filter(profile__role="admin"))
    judges = set(
        permission.user.email
        for permission in models.ContestPermission.objects.filter(
            contest=clarification.contest, role="judge", granted=True
        )
    )
    recipients = list(admins.union(judges))

    send_email(subject=subject, message=message, recipients=recipients)


def report_feedback(feedback):
    subject = "Feedback request"
    message = render_to_string(
        "mog/email/feedback.txt",
        {
            "feedback": feedback,
            "domain": site_url(),
        },
    )

    admins = set(user.email for user in User.objects.filter(profile__role="admin"))
    recipients = list(admins)

    send_email(subject=subject, message=message, recipients=recipients)


def report_feedback_to_user(feedback, subject):
    send_email(
        subject,
        "<NO TEXT>",
        [feedback.sender.email],
        html_message=render_to_string(
            "mog/email/feedback_notification.html",
            {"feedback": feedback, "subject": subject, "domain": site_url()},
        ),
    )


def report_feedback_to_assigned(feedback):
    subject = "Feedback assigned to you"
    message = render_to_string(
        "mog/email/feedback.txt",
        {
            "feedback": feedback,
            "domain": site_url(),
        },
    )

    recipients = list([feedback.assigned.email])

    send_email(subject=subject, message=message, recipients=recipients)

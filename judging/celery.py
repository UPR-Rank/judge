"""The Celery application.

Redis carries the messages and PostgreSQL carries the work: a submission is
pending in the database until a worker claims it, so losing a message, or
having the broker down at the moment a submission is created, only delays that
submission. Nothing is lost, and the reaper covers the cases where a message
is lost.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "judge.settings")

app = Celery("judge")

# Every CELERY_* setting in judge/settings.py configures the app.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Picks up `tasks.py` in each installed app, which is where judging.tasks
# lives.
app.autodiscover_tasks()

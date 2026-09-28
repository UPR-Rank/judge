# The Celery app has to be created before Django loads the app registry,
# otherwise the tasks defined in judging.tasks are not bound to an app.
from .celery import app as celery_app

__all__ = ("celery_app",)

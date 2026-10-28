from celery import shared_task

from palantir.utils import _log_access_now


# One per request, so the result is never stored: it would only fill Redis.
@shared_task(acks_late=False, ignore_result=True)
def log_access_task(message):
    _log_access_now(message)

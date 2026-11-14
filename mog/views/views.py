import logging

import redis
from django.conf import settings
from django.core.paginator import (
    EmptyPage,
    PageNotAnInteger,
    Paginator,
)
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.cache import cache_page

from api.models import Post

log = logging.getLogger(__name__)


def index(request):
    post_list = (
        Post.objects.filter(show_in_main_page=True)
        .order_by("-creation_date")
        .select_related("user")
    )
    paginator = Paginator(post_list, 5)
    page = request.GET.get("page")
    try:
        posts = paginator.page(page)
    except PageNotAnInteger:
        posts = paginator.page(1)
    except EmptyPage:
        posts = paginator.page(paginator.num_pages)
    return render(request, "mog/index.html", {"posts": posts})


def faq(request):
    return render(request, "mog/faq.html")


def privacy(request):
    return render(request, "mog/privacy.html")


def _check_database():
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")


def _check_broker():
    # The broker, not the cache: a cache outage degrades to a local cache by
    # design (judge/cache.py), but without the broker nothing gets graded.
    with redis.Redis.from_url(
        settings.CELERY_BROKER_URL, socket_connect_timeout=2, socket_timeout=2
    ) as client:
        client.ping()


def health(request):
    checks = {}
    for name, check in (("database", _check_database), ("broker", _check_broker)):
        try:
            check()
            checks[name] = "ok"
        except Exception:
            # Details go to the log only: the endpoint is public.
            log.exception("Health check %s failed", name)
            checks[name] = "error"
    healthy = all(value == "ok" for value in checks.values())
    return JsonResponse(
        {"status": "ok" if healthy else "error", **checks},
        status=200 if healthy else 503,
    )


@cache_page(24 * 60 * 60)
def robotstxt(request):
    content = render_to_string("mog/robots.txt")
    response = HttpResponse(content, content_type="text/plain")
    response["Cache-Control"] = "private, no-cache, no-store, must-revalidate"
    return response

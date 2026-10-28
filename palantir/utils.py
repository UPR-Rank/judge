import json
import re
import requests
import threading
import urllib

from django.contrib.auth.models import User
from django.core.cache import cache
from django.utils.text import slugify

from api.lib import cache_keys, constants
from palantir.models import AccessLog


BLACK_LISTED_PATHS_RE = [
    re.compile("^/admin"),
    re.compile("^/favicon[.]ico$"),
    re.compile("^/health"),
    re.compile("^/media"),
    re.compile("^/static"),
]

BLACK_LISTED_IPS = []


def get_real_address_from_ip(ip: str) -> object:
    if not ip:
        return {}
    version = cache_keys.current_version(constants.CACHE_KEY_IP_PREFIX)
    key = cache_keys.versioned_key(constants.CACHE_KEY_IP_PREFIX, version, ip)
    info = cache.get(key)
    if info:
        return info
    try:
        url = "http://ip-api.com/json/{}".format(ip)
        info = json.loads(requests.get(url).content.decode("utf8"))
    except:
        info = {}
    cache.set(key, info, constants.IP_INFO_TIMEOUT)
    return info


def get_client_ip_from_request(request):
    if request.META.get("HTTP_X_FORWARDED_FOR"):
        return request.META.get("HTTP_X_FORWARDED_FOR").split(",")[-1]
    return request.META.get("REMOTE_ADDR")


def build_request_message(request):
    return {
        "user": request.user.pk if request.user.is_authenticated else None,
        "url": request.build_absolute_uri(),
        "ip": get_client_ip_from_request(request),
        "method": request.method,
        "files": [
            {
                "name": request.FILES[key].name,
                "size": request.FILES[key].size,
            }
            for key in request.FILES
        ],
        "meta": {
            "HTTP_REFERER": request.META.get("HTTP_REFERER"),
        },
    }


def get_path_from_url(url: str) -> str:
    return urllib.parse.urlparse(url).path


def should_log_access(message: object) -> bool:
    # block-by-path
    path = get_path_from_url(message["request"].get("url", ""))
    for black_listed_path_re in BLACK_LISTED_PATHS_RE:
        if black_listed_path_re.match(path):
            return False
    # block-by-ip
    if message["request"].get("ip") in BLACK_LISTED_IPS:
        return False
    return True


def _log_access_now(message: object):
    if not should_log_access(message):
        return
    message["address"] = get_real_address_from_ip(message["request"]["ip"])
    try:
        user = User.objects.get(pk=message["request"]["user"])
    except:
        user = None
    path = get_path_from_url(message["request"].get("url", ""))
    AccessLog.objects.create(
        user=user,
        message=json.dumps(message),
        slug=slugify(path.replace("/", "-")).strip("-").strip()[:1024],
    )


def log_access_eventually(message: object):
    """Log access in a different thread to unblock the current request"""
    threading.Thread(target=_log_access_now, args=[message], kwargs={}).start()

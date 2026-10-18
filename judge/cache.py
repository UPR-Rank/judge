"""Cache backends for the judge.

The site is served by several processes at once (four Gunicorn workers, a
Celery worker and a Celery beat process), so a per-process cache is really
several unrelated caches: whichever worker happens to answer a standings
request is the only one that learns the result, and an invalidation only
reaches the process that wrote the row. Everything here exists to put one
shared cache in front of all of them.

Two properties matter beyond "the cache is shared":

* **A cache outage must not take the site down.** Everything cached is derived
  data that PostgreSQL can recompute, so a Redis that is unreachable or slow
  has to look like an empty cache, not like a 500. :class:`ResilientRedisCache`
  swallows the connection errors and answers from a local in-process cache
  instead, which is slower but still correct.
* **Keys must not collide across the deployment.** ``KEY_PREFIX`` keeps one
  environment's keys from being read as another's when several instances share
  a Redis, and the prefix travels with the key so a delete cannot reach
  outside its own namespace.
"""

from django.core.cache.backends.locmem import LocMemCache
from django.core.cache.backends.redis import RedisCache
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

# Both mean "Redis did not answer" as far as a cache is concerned. They are the
# only failures worth degrading on: a Lua or protocol error would mean this code
# and the server disagree, which is a bug rather than an outage.
UNREACHABLE = (RedisConnectionError, RedisTimeoutError)


class ResilientRedisCache(RedisCache):
    """A shared Redis cache that keeps serving when Redis does not.

    On any connection failure the request is answered from a per-process
    in-memory cache. That loses the sharing — each process keeps its own copy —
    but it preserves the two properties that actually matter for correctness:
    the site still answers, and it answers with the same values it would have
    computed anyway. A cache that raises takes down standings and the front page
    over data that was only ever an optimisation.
    """

    def __init__(self, server, params):
        super().__init__(server, params)
        self._local = None

    @property
    def local(self):
        """The per-process cache used while Redis is unreachable.

        Created on first use so that the common case — Redis healthy — never
        allocates a second cache or keeps one alive.
        """
        if self._local is None:
            self._local = LocMemCache(str(id(self)), {})
        return self._local

    def _fallback(self, operation, *args, **kwargs):
        """Run `operation` on the local cache, ignoring a Redis failure.

        A local cache cannot fail this way, but its failures are not worth
        propagating either: this code only ever runs after Redis has already
        failed, and raising here would hide that first, more useful error.
        """
        return getattr(self.local, operation)(*args, **kwargs)

    def get(self, key, default=None, version=None):
        try:
            return super().get(key, default, version)
        except UNREACHABLE:
            return self._fallback("get", key, default, version)

    def set(self, key, value, timeout=RedisCache.default_timeout, version=None):
        try:
            return super().set(key, value, timeout, version)
        except UNREACHABLE:
            return self._fallback("set", key, value, timeout, version)

    def add(self, key, value, timeout=RedisCache.default_timeout, version=None):
        try:
            return super().add(key, value, timeout, version)
        except UNREACHABLE:
            return self._fallback("add", key, value, timeout, version)

    def delete(self, key, version=None):
        try:
            return super().delete(key, version)
        except UNREACHABLE:
            return self._fallback("delete", key, version)

    def get_many(self, keys, version=None):
        try:
            return super().get_many(keys, version)
        except UNREACHABLE:
            return self._fallback("get_many", keys, version)

    def set_many(self, data, timeout=RedisCache.default_timeout, version=None):
        try:
            return super().set_many(data, timeout, version)
        except UNREACHABLE:
            return self._fallback("set_many", data, timeout, version)

    def delete_many(self, keys, version=None):
        try:
            return super().delete_many(keys, version)
        except UNREACHABLE:
            return self._fallback("delete_many", keys, version)

    def has_key(self, key, version=None):
        try:
            return super().has_key(key, version)
        except UNREACHABLE:
            return self._fallback("has_key", key, version)

    def touch(self, key, timeout=RedisCache.default_timeout, version=None):
        try:
            return super().touch(key, timeout, version)
        except UNREACHABLE:
            return self._fallback("touch", key, timeout, version)

    def incr(self, key, delta=1, version=None):
        try:
            return super().incr(key, delta, version)
        except UNREACHABLE:
            return self._fallback("incr", key, delta, version)

    def decr(self, key, delta=1, version=None):
        try:
            return super().decr(key, delta, version)
        except UNREACHABLE:
            return self._fallback("decr", key, delta, version)

    def clear(self):
        try:
            return super().clear()
        except UNREACHABLE:
            # Dropping the local copy too keeps the two caches from disagreeing
            # about a namespace that has just been emptied on purpose.
            return self.local.clear()

    def close(self, **kwargs):
        if self._local is not None:
            self._local.close(**kwargs)
        try:
            return super().close(**kwargs)
        except (UNREACHABLE, RedisError):
            return None


__all__ = ["ResilientRedisCache", "UNREACHABLE"]

"""Cache keys and the invalidation that goes with them.

Two things live here because they have to agree with each other: how a key is
built from a base name and its arguments, and how every key derived from that
base name is dropped again. When those drift apart the failure is silent — the
entry is written, the delete misses it, and a stale value is served until its
timeout expires. That is exactly what the old key building did: it appended the
arguments with no separator, so a query called with no arguments stored under
``key`` while the invalidation deleted exactly ``key`` and the two only matched
by accident for zero-argument queries.

Redis cannot enumerate keys cheaply and deleting by pattern needs ``SCAN``,
which is a poor fit for a signal handler on every write. Instead each cached
value is addressed by a *version* that lives under the base name. Invalidation
is one delete of the base name, and the old entries become unreachable
immediately and expire on their own.
"""

from django.core.cache import cache


def make_key(base, *args):
    """Build the cache key for `base` applied to `args`.

    The argument part is hashed rather than joined, because the arguments can
    be arbitrarily long (a group name, an IP) and Redis keys are limited to a
    few hundred bytes. ``None`` and ``()`` produce the same key: a query with no
    arguments is the same query every time, so it should not depend on how it
    happened to be called.
    """
    if args in ((), ((),), (None,)):
        return base
    return "{}:{}".format(base, hash_args(args))


def hash_args(args):
    """Return a short, stable digest of `args` for use in a cache key."""
    import hashlib
    import json

    payload = json.dumps(args, sort_keys=True, default=repr)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def versioned_key(base, version, *args):
    """Address a cached value by the current version of `base`."""
    return make_key(base, version, *args)


def current_version(base):
    """Return the version token currently in force for `base`.

    Defaults to a constant so that an entry cached before any invalidation
    still has a predictable address.
    """
    version = cache.get(make_key(base, VERSION_MARKER))
    if version is None:
        version = INITIAL_VERSION
        cache.add(make_key(base, VERSION_MARKER), version, None)
    return version


def invalidate(base):
    """Drop every cached value derived from `base`.

    The values themselves are left to expire on their own; only the version
    they were written under stops being current. This is one write, so it does
    not need to know how many entries `base` has or what arguments they were
    built from.
    """
    from uuid import uuid4

    cache.set(make_key(base, VERSION_MARKER), uuid4().hex, None)


VERSION_MARKER = "__version__"
INITIAL_VERSION = "0"


__all__ = [
    "current_version",
    "hash_args",
    "invalidate",
    "make_key",
    "versioned_key",
]

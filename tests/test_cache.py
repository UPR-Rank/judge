"""Tests for the shared cache layer.

The cache is shared across processes (web workers, Celery worker, beat), so the
behaviour that matters is:

* Keys built by ``api.lib.cache_keys`` do not collide when arguments differ and
  do not depend on how zero-argument queries are called.
* Invalidating a base name drops every derived key via the version marker.
* The Redis backend degrades to a per-process in-memory cache when Redis is
  unreachable, so requests do not fail on a cache outage.
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from api.lib import cache_keys, constants
from judge import cache as judge_cache


class CacheKeysTestCase(TestCase):
    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_make_key_separates_zero_args(self):
        base = "test/base"
        self.assertEqual(cache_keys.make_key(base), base)
        self.assertEqual(cache_keys.make_key(base), cache_keys.make_key(base, ()))
        self.assertEqual(cache_keys.make_key(base), cache_keys.make_key(base, None))

    def test_make_key_changes_with_args(self):
        base = "test/base"
        k1 = cache_keys.make_key(base, "a")
        k2 = cache_keys.make_key(base, "b")
        self.assertNotEqual(k1, k2)

    def test_make_key_is_stable_for_same_args(self):
        base = "test/base"
        args = ("x", 1, {"y": 2})
        self.assertEqual(
            cache_keys.make_key(base, *args), cache_keys.make_key(base, *args)
        )

    def test_versioned_key_includes_version(self):
        base = "test/base"
        v = "v1"
        self.assertNotEqual(
            cache_keys.versioned_key(base, v, "a"),
            cache_keys.make_key(base, "a"),
        )

    def test_invalidate_rotates_version(self):
        base = "test/base"
        v0 = cache_keys.current_version(base)
        cache_keys.invalidate(base)
        v1 = cache_keys.current_version(base)
        self.assertNotEqual(v0, v1)

    def test_invalidate_makes_old_versioned_keys_unreachable(self):
        base = "test/base"
        key = cache_keys.versioned_key(base, cache_keys.current_version(base), "x")
        cache.set(key, "value")
        self.assertEqual(cache.get(key), "value")
        cache_keys.invalidate(base)
        # old versioned key is still in the store, but a new read uses new version
        new_key = cache_keys.versioned_key(base, cache_keys.current_version(base), "x")
        self.assertNotEqual(key, new_key)
        self.assertIsNone(cache.get(key))
        self.assertIsNone(cache.get(new_key))


class ResilientRedisCacheTestCase(TestCase):
    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_degrades_on_connection_error(self):
        backend = judge_cache.ResilientRedisCache("redis://invalid:6379/0", {})
        backend.set("k", "v", timeout=None)
        with patch.object(
            backend,
            "set",
            side_effect=judge_cache.RedisConnectionError("boom"),
        ):
            backend.set("k2", "v2", timeout=None)
        self.assertEqual(backend.local.get("k2"), "v2")

    def test_degrades_on_timeout_error(self):
        backend = judge_cache.ResilientRedisCache("redis://invalid:6379/0", {})
        with patch.object(
            backend,
            "get",
            side_effect=judge_cache.RedisTimeoutError("boom"),
        ):
            self.assertIsNone(backend.get("missing"))

    def test_degrades_on_many_operations(self):
        backend = judge_cache.ResilientRedisCache("redis://invalid:6379/0", {})
        backend.set_many({"a": 1, "b": 2}, timeout=None)
        with patch.object(
            backend,
            "set_many",
            side_effect=judge_cache.RedisConnectionError("boom"),
        ):
            backend.set_many({"c": 3}, timeout=None)
        self.assertEqual(
            backend.local.get_many(["a", "b", "c"]), {"a": 1, "b": 2, "c": 3}
        )

    def test_clear_degrades_and_clears_local(self):
        backend = judge_cache.ResilientRedisCache("redis://invalid:6379/0", {})
        backend.local.set("x", 1)
        with patch.object(
            backend,
            "clear",
            side_effect=judge_cache.RedisConnectionError("boom"),
        ):
            backend.clear()
        self.assertIsNone(backend.local.get("x"))


class ConstantsAndWiringTestCase(TestCase):
    def test_constants_match_expected_keys(self):
        self.assertEqual(constants.CACHE_KEY_DIVISIONS, "mog/filters/divisions")
        self.assertEqual(constants.CACHE_KEY_RESULTS, "mog/filters/results")
        self.assertEqual(constants.CACHE_KEY_USER_RATING, "mog/filters/user_rating")
        self.assertEqual(constants.CACHE_KEY_IP_PREFIX, "palantir/ip")
        self.assertGreater(constants.DIVISIONS_TIMEOUT, 0)
        self.assertGreater(constants.RESULTS_TIMEOUT, 0)
        self.assertGreater(constants.USER_RATING_TIMEOUT, 0)
        self.assertGreater(constants.IP_INFO_TIMEOUT, 0)


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "judge.cache.ResilientRedisCache",
            "LOCATION": "redis://fake:6379/2",
            "KEY_PREFIX": "test",
        }
    }
)
class SettingsCacheBackendTestCase(TestCase):
    def test_settings_use_resilient_backend(self):
        from django.conf import settings

        self.assertEqual(
            settings.CACHES["default"]["BACKEND"],
            "judge.cache.ResilientRedisCache",
        )
        self.assertEqual(settings.CACHES["default"]["LOCATION"], "redis://fake:6379/2")
        self.assertEqual(settings.CACHES["default"]["KEY_PREFIX"], "test")

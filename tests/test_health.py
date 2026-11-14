"""Tests for the public /health endpoint used by the container healthcheck."""

from unittest.mock import patch

from django.test import TestCase
from redis.exceptions import ConnectionError as RedisConnectionError


class HealthTestCase(TestCase):
    @patch("mog.views.views._check_broker")
    def test_healthy(self, _broker):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(), {"status": "ok", "database": "ok", "broker": "ok"}
        )

    @patch(
        "mog.views.views._check_broker",
        side_effect=RedisConnectionError("redis://secret@broker:6379"),
    )
    def test_broker_down(self, _broker):
        with self.assertLogs("mog.views.views", "ERROR"):
            response = self.client.get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(), {"status": "error", "database": "ok", "broker": "error"}
        )
        self.assertNotIn(b"secret", response.content)

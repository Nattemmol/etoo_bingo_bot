import unittest

from starlette.testclient import TestClient

from server.main import app


class TestHealthz(unittest.TestCase):
    def test_healthz_not_shadowed_by_static_root(self):
        with TestClient(app) as client:
            response = client.get("/healthz")
        self.assertIn(response.status_code, (200, 503))
        payload = response.json()
        self.assertIn(payload.get("status"), ("ok", "degraded"))
        self.assertEqual(payload.get("server"), "ok")
        self.assertIn("database", payload)
        self.assertIn("webapp_url", payload)

    def test_healthz_ok_when_db_works(self):
        with TestClient(app) as client:
            response = client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["database"], "ok")

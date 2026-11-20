"""Integration tests for the HTTP API and the static dashboard."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from kvstore.server import create_server
from kvstore.snapshot import SnapshotManager
from kvstore.store import KVStore


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = KVStore()
        self.manager = SnapshotManager(Path(self.temp.name) / "snapshots")
        self.server = create_server(self.store, self.manager, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, payload=None, raw=None):
        body = raw
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base + path, data=body, method=method, headers=headers)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def raw_get(self, path):
        with urlopen(self.base + path, timeout=5) as response:
            return response.status, response.headers.get("Content-Type"), response.read()

    # ------------------------------------------------------------------
    # Health, status, and static files
    # ------------------------------------------------------------------
    def test_health(self):
        status, data = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "ok")

    def test_status(self):
        self.store.set("one", "1")
        status, data = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(data["entries"], 1)
        self.assertEqual(data["snapshots"], 0)
        self.assertEqual(data["data_dir"], str(self.manager.directory))

    def test_dashboard_is_served(self):
        status, content_type, body = self.raw_get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn(b"KV Snapshot", body)

    def test_static_assets_are_served(self):
        for path, expected, content_type_hint in [
            ("/app.js", b"refreshAll", "javascript"),
            ("/style.css", b"--accent", "css"),
        ]:
            status, content_type, body = self.raw_get(path)
            self.assertEqual(status, 200)
            self.assertIn(expected, body)
            self.assertIn(content_type_hint, content_type)

    def test_unknown_page_is_json_404(self):
        status, data = self.request("GET", "/does-not-exist")
        self.assertEqual(status, 404)
        self.assertFalse(data["ok"])

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def test_create_read_update_delete_entry(self):
        status, data = self.request(
            "POST", "/api/entries", {"key": "name", "value": "Seif"}
        )
        self.assertEqual(status, 201)
        self.assertTrue(data["created"])

        status, data = self.request("GET", "/api/entries")
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["entries"][0], {"key": "name", "value": "Seif"})

        status, data = self.request("GET", "/api/entries/name")
        self.assertEqual(status, 200)
        self.assertEqual(data["value"], "Seif")

        status, data = self.request(
            "POST", "/api/entries", {"key": "name", "value": "Updated"}
        )
        self.assertEqual(status, 200)
        self.assertFalse(data["created"])
        self.assertEqual(self.store.get("name"), "Updated")

        status, data = self.request("DELETE", "/api/entries/name")
        self.assertEqual(status, 200)
        self.assertEqual(data["deleted"], "name")
        self.assertFalse(self.store.exists("name"))

        status, data = self.request("GET", "/api/entries/name")
        self.assertEqual(status, 404)
        self.assertFalse(data["ok"])

    def test_delete_missing_entry_is_404(self):
        status, data = self.request("DELETE", "/api/entries/missing")
        self.assertEqual(status, 404)
        self.assertEqual(data["error"]["code"], "not_found")

    def test_entry_validation_errors(self):
        cases = [
            {"key": "", "value": "x"},
            {"key": "   ", "value": "x"},
            {"key": "key\nwith\nnewlines", "value": "x"},
            {"key": "key"},
            {"value": "value"},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                status, data = self.request("POST", "/api/entries", payload)
                self.assertEqual(status, 400)
                self.assertFalse(data["ok"])

    def test_invalid_json_is_rejected(self):
        status, data = self.request(
            "POST", "/api/entries", raw=b"{this is not json"
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "bad_request")

    def test_oversized_value_is_rejected(self):
        status, data = self.request(
            "POST", "/api/entries", {"key": "big", "value": "x" * (64 * 1024 + 1)}
        )
        self.assertEqual(status, 400)
        self.assertIn("too long", data["error"]["message"])

    def test_unicode_entry(self):
        payload = {"key": "مدينة", "value": "القاهرة 🚀"}
        status, _ = self.request("POST", "/api/entries", payload)
        self.assertEqual(status, 201)
        status, data = self.request("GET", "/api/entries/%D9%85%D8%AF%D9%8A%D9%86%D8%A9")
        self.assertEqual(status, 200)
        self.assertEqual(data["value"], payload["value"])

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------
    def test_snapshot_create_list_and_restore(self):
        self.store.set("kept", "original")
        status, data = self.request("POST", "/api/snapshots")
        self.assertEqual(status, 201)
        name = data["snapshot"]["name"]
        self.assertTrue(name.endswith(".kvs"))
        self.assertEqual(data["snapshot"]["entries"], 1)

        status, data = self.request("GET", "/api/snapshots")
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["snapshots"][0]["name"], name)

        self.store.set("temporary", "later")
        status, data = self.request("POST", "/api/restore", {"file": name})
        self.assertEqual(status, 200)
        self.assertEqual(data["entries"], 1)
        self.assertFalse(self.store.exists("temporary"))
        self.assertEqual(self.store.get("kept"), "original")

    def test_restore_missing_snapshot(self):
        status, data = self.request(
            "POST", "/api/restore", {"file": "snap-missing.kvs"}
        )
        self.assertEqual(status, 404)
        self.assertFalse(data["ok"])

    def test_restore_rejects_path_traversal(self):
        status, data = self.request(
            "POST", "/api/restore", {"file": "../outside.kvs"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "validation_error")

    def test_corrupt_snapshot_returns_422_and_keeps_state(self):
        self.store.set("safe", "value")
        status, data = self.request("POST", "/api/snapshots")
        name = data["snapshot"]["name"]
        path = self.manager.directory / name
        raw = bytearray(path.read_bytes())
        raw[-1] ^= 0xFF
        path.write_bytes(raw)

        self.store.set("after", "snapshot")
        status, data = self.request("POST", "/api/restore", {"file": name})
        self.assertEqual(status, 422)
        self.assertEqual(data["error"]["code"], "snapshot_corrupt")
        self.assertEqual(self.store.get("safe"), "value")
        self.assertEqual(self.store.get("after"), "snapshot")

    def test_restore_requires_file_field(self):
        status, data = self.request("POST", "/api/restore", {})
        self.assertEqual(status, 400)

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------
    def test_unknown_api_route(self):
        status, data = self.request("GET", "/api/unknown")
        self.assertEqual(status, 404)
        self.assertEqual(data["error"]["code"], "not_found")

    def test_put_is_not_allowed(self):
        status, data = self.request("PUT", "/api/entries", {"key": "x", "value": "y"})
        self.assertEqual(status, 405)
        self.assertEqual(data["error"]["code"], "method_not_allowed")


if __name__ == "__main__":
    unittest.main()

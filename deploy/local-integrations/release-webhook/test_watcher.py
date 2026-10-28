import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import tempfile
import secrets
import threading
import unittest
from unittest.mock import patch
import urllib.error

import watcher as w


def release(identifier, **overrides):
    return {"id": identifier, "tag_name": f"v{identifier}", "name": f"Release {identifier}",
            "html_url": f"https://github.com/paperclipai/paperclip/releases/tag/v{identifier}",
            "published_at": f"2026-10-{identifier:02d}T00:00:00Z", "draft": False, "prerelease": False, **overrides}


def state():
    return {"version": 1, "repository": w.REPO, "seen_release_ids": [1], "pending": [], "cache": {}}


class WatcherTests(unittest.TestCase):
    def test_only_new_stable_releases_are_queued_once(self):
        value = state()
        releases = [release(4, draft=True), release(3, prerelease=True), release(2), release(1)]
        w.stage(value, releases, {})
        w.stage(value, releases, {})
        self.assertEqual([e["payload"]["release"]["id"] for e in value["pending"]], [2])
        # A prerelease promotion should count as a new stable release.
        w.stage(value, [release(3)], {})
        self.assertEqual([e["payload"]["release"]["id"] for e in value["pending"]], [2, 3])

    def test_failed_delivery_survives_restart_with_identical_id(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(w, "STATE", Path(directory)):
            value = state()
            w.stage(value, [release(2)], {})
            w.save(value)
            delivery = value["pending"][0]
            def fail(*_):
                raise w.WatchError("webhook_unreachable")
            with self.assertRaises(w.WatchError):
                w.deliver(value, send=fail)
            restored = w.load()
            self.assertEqual(restored["pending"][0], delivery)
            sent = []
            def succeed(payload, identifier):
                sent.append(identifier)
                return {"id": "run-2", "status": "issue_created"}
            w.deliver(restored, send=succeed)
            self.assertEqual(sent, [delivery["delivery_id"]])
            self.assertEqual(w.load()["pending"], [])

    def test_setup_receipt_and_failed_dispatch_do_not_acknowledge_release(self):
        for result in ({"status": "test_received", "routineStarted": False},
                       {"id": "failed-run", "status": "failed"}):
            value = state()
            w.stage(value, [release(2)], {})
            with self.assertRaises(w.WatchError):
                w.deliver(value, send=lambda *_: result, persist=lambda _: None)
            self.assertEqual(len(value["pending"]), 1)

    def test_first_initialization_never_sends_historical_releases(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(w, "STATE", Path(directory)), \
                patch.object(w, "fetch_releases", return_value=([release(1), release(2)], {})), \
                patch.object(w, "post") as send, patch("sys.argv", ["watcher.py", "--initialize"]):
            w.main()
            initialized = w.load()
            self.assertEqual(initialized["pending"], [])
            self.assertEqual(initialized["seen_release_ids"], [1, 2])
            self.assertFalse(initialized["armed"])
            send.assert_not_called()
            with self.assertRaises(w.WatchError):
                w.main()

    def test_missing_state_does_not_silently_reset_baseline(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(w, "STATE", Path(directory)), \
                patch("sys.argv", ["watcher.py", "--loop"]), patch.object(w, "ENDPOINT", "https://example.invalid/fire"), patch.object(w, "fetch_releases") as fetch:
            with self.assertRaises(FileNotFoundError):
                w.main()
            fetch.assert_not_called()

    def test_sender_requires_an_explicit_webhook_url(self):
        with patch.object(w, "ENDPOINT", None), patch("sys.argv", ["watcher.py", "--loop"]):
            with self.assertRaisesRegex(w.WatchError, "webhook_url_required"):
                w.main()

    def test_unchanged_github_page_uses_etag_and_cached_data(self):
        cached = {w.API: {"etag": '"version-1"', "releases": [release(1)], "next": None}}
        def unchanged(request, **_):
            self.assertEqual(request.get_header("If-none-match"), '"version-1"')
            raise urllib.error.HTTPError(w.API, 304, "Not Modified", {}, None)
        with patch.object(w.urllib.request, "urlopen", side_effect=unchanged):
            releases, updated = w.fetch_releases(cached)
        self.assertEqual(releases, [release(1)])
        self.assertEqual(updated, cached)

    def test_signs_exact_json_bytes_without_bearer_authentication(self):
        received = {}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                received["headers"] = self.headers
                received["body"] = self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(202)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"test_received","routineStarted":false}')
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        try:
            test_key = secrets.token_bytes(32)
            with patch.object(w, "SECRET") as secret, patch.object(w, "ENDPOINT", f"http://127.0.0.1:{server.server_port}/fire"):
                secret.read_bytes.return_value = test_key
                w.post({"release": {"name": "Unicode \u2713"}}, "stable-delivery-id", "ping")
            headers, body = received["headers"], received["body"]
            expected = "sha256=" + hmac.new(test_key.strip(), body, hashlib.sha256).hexdigest()
            self.assertTrue(hmac.compare_digest(headers["X-Hub-Signature-256"], expected))
            self.assertEqual(headers["Content-Type"], "application/json")
            self.assertEqual(headers["X-GitHub-Delivery"], "stable-delivery-id")
            self.assertEqual(headers["Idempotency-Key"], "stable-delivery-id")
            self.assertIsNone(headers.get("Authorization"))
            self.assertIsInstance(json.loads(body), dict)
        finally:
            thread.join(timeout=5)
            server.server_close()


if __name__ == "__main__":
    unittest.main()

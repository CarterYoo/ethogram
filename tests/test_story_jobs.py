"""The UI can start a harness run explicitly; polling never starts agents or writes a work store."""
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from swarmgraph import story_jobs as J, web


class Jobs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "fixture.sqlite")
        sqlite3.connect(self.db).close()
        self.release = threading.Event()
        self.done = threading.Event()

    def tearDown(self):
        self.release.set()
        self.tmp.cleanup()

    def test_clicks_deduplicate_and_return_while_agent_is_running(self):
        calls = []
        entered = threading.Event()

        def runner(db, **kwargs):
            calls.append(db)
            kwargs["log"]("Reviewing candidate scenes")
            entered.set()
            self.release.wait(3)
            self.done.set()
            return {"accepted": 1}

        first = J.start(self.db, runner)
        self.assertTrue(entered.wait(3))
        second = J.start(self.db, runner)
        self.assertEqual(first["status"], "building")
        self.assertEqual(second["status"], "building")
        self.assertEqual(len(calls), 1)
        self.release.set()
        self.assertTrue(self.done.wait(3))

    def test_datasets_have_independent_jobs(self):
        entered = threading.Event()
        calls = []

        def runner(db, **kwargs):
            calls.append(db)
            if len(calls) == 2:
                entered.set()
            self.release.wait(3)
            return {}

        J.start(self.db, runner)
        J.start(os.path.join(self.tmp.name, "other.sqlite"), runner)
        self.assertTrue(entered.wait(3))
        self.assertEqual(len(calls), 2)

    def test_http_status_is_read_only_and_post_is_explicit(self):
        class FixtureHandler(web.Handler):
            db = self.db

        server = web.http.server.ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = "http://127.0.0.1:%s/api/feature_storylines" % server.server_port
        try:
            with patch("swarmgraph.incident_storylines.page", return_value={"status": "missing"}), \
                    patch.object(J, "start", return_value={"status": "building"}) as start:
                with urllib.request.urlopen(url) as response:
                    self.assertEqual(json.load(response)["status"], "missing")
                start.assert_not_called()
                self.assertFalse(os.path.exists(self.db + ".work"))
                req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req) as response:
                    self.assertEqual(response.status, 202)
                start.assert_called_once_with(self.db)
                for body, headers, code in [
                        (b"{}", {"Content-Type": "text/plain"}, 415),
                        (b"{}", {"Content-Type": "application/json", "Origin": "http://other.invalid"}, 403),
                        (b'{"prompt":"x"}', {"Content-Type": "application/json"}, 400)]:
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers))
                    self.assertEqual(error.exception.code, code)
                self.assertEqual(start.call_count, 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)

    def test_shared_page_never_starts_a_run(self):
        class SharedHandler(web.Handler):
            db = self.db
            share = True

        server = web.http.server.ThreadingHTTPServer(("127.0.0.1", 0), SharedHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = "http://127.0.0.1:%s/api/feature_storylines" % server.server_port
        old = web.SHARE
        web.SHARE = True
        try:
            with patch("swarmgraph.incident_storylines.page", return_value={"status": "missing"}), \
                    patch.object(J, "start", return_value={"status": "building"}) as start:
                with urllib.request.urlopen(url) as response:
                    self.assertIs(json.load(response)["can_create"], False)
                req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"})
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(req)
                self.assertEqual(error.exception.code, 403)
                start.assert_not_called()
        finally:
            web.SHARE = old
            server.shutdown()
            server.server_close()
            thread.join(3)

    def test_health_and_home_redirect(self):
        class DeployHandler(web.Handler):
            db = self.db
            dbs = {"main": self.db}
            home = "/features"

        server = web.http.server.ThreadingHTTPServer(("127.0.0.1", 0), DeployHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:%s" % server.server_port
        try:
            with urllib.request.urlopen(base + "/healthz") as response:
                self.assertEqual(json.load(response), {"ok": True, "datasets": ["main"]})
            with urllib.request.urlopen(base + "/?ds=main") as response:  # follows the redirect
                self.assertTrue(response.geturl().endswith("/features?ds=main"))
                self.assertIn(b"Behaviour", response.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)


if __name__ == "__main__":
    unittest.main()


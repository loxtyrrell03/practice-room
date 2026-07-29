import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import server
from practice_logs import ObservationPipeline


class ObservationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / "data-repo"
        (self.data / "data").mkdir(parents=True)
        (self.data / "data/observations.json").write_text(
            '{"version":2,"obs":[]}\n', encoding="utf-8"
        )
        self.old_data = server.DATA
        self.old_pipeline = server.observation_pipeline
        server.DATA = self.data
        server.observation_pipeline = ObservationPipeline(self.data)
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        server.DATA = self.old_data
        server.observation_pipeline = self.old_pipeline
        self.temp.cleanup()

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.httpd.server_address[1], timeout=3
        )
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if payload else {}
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        parsed = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, parsed

    def test_observation_endpoint_persists_before_accepting_and_deduplicates(self):
        payload = {
            "clientId": "browser-outbox-1",
            "day": 1,
            "blockId": "bach",
            "block": "Bach repair",
            "text": "Fugue b.12 entry missed",
        }
        first_status, first = self.request("POST", "/api/observations", payload)
        second_status, second = self.request("POST", "/api/observations", payload)

        self.assertEqual(201, first_status)
        self.assertEqual(200, second_status)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(
            first["observation"]["id"], second["observation"]["id"]
        )
        saved = json.loads(
            (self.data / "data/observations.json").read_text(encoding="utf-8")
        )
        self.assertEqual(1, len(saved["obs"]))
        self.assertEqual("pending", saved["obs"][0]["status"])

        meta_status, meta = self.request("GET", "/api/meta")
        self.assertEqual(200, meta_status)
        self.assertEqual(1, meta["practiceLogs"]["counts"]["pending"])

    def test_generic_file_endpoint_cannot_overwrite_observation_queue(self):
        status, result = self.request(
            "POST",
            "/api/file",
            {"path": "data/observations.json", "content": '{"obs":[]}\n'},
        )
        self.assertEqual(409, status)
        self.assertIn("durable server workflow", result["error"])

    def test_generic_file_endpoint_cannot_overwrite_repertoire_audit(self):
        status, result = self.request(
            "POST",
            "/api/file",
            {
                "path": "data/repertoire-changes.json",
                "content": '{"version":1,"pending":[],"changes":[]}\n',
            },
        )
        self.assertEqual(409, status)
        self.assertIn("durable server workflow", result["error"])


if __name__ == "__main__":
    unittest.main()

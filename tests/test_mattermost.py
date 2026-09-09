import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import mattermost


class FakeMattermost(BaseHTTPRequestHandler):
    requests = []
    fail_next = False

    def log_message(self, *args):
        pass

    def _read(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def _reply(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        FakeMattermost.requests.append(("GET", self.path, dict(self.headers), None))
        if self.path == "/api/v4/system/ping":
            return self._reply(200, {"status": "OK"})
        return self._reply(404, {"message": "not found"})

    def do_POST(self):
        body = self._read()
        FakeMattermost.requests.append(("POST", self.path, dict(self.headers), body))
        if FakeMattermost.fail_next:
            FakeMattermost.fail_next = False
            return self._reply(500, {"message": "boom"})
        if self.path == "/api/v4/posts":
            return self._reply(201, {"id": "post123", "channel_id": body["channel_id"], "message": body["message"]})
        if self.path == "/hooks/abc":
            return self._reply(200, {"status": "ok"})
        return self._reply(404, {"message": "not found"})


class MattermostClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeMattermost)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeMattermost.requests = []
        FakeMattermost.fail_next = False

    def test_ping_hits_system_ping(self):
        client = mattermost.MattermostClient(self.base, token="tok", channel_id="chan")
        self.assertTrue(client.ping())
        self.assertEqual(FakeMattermost.requests[0][1], "/api/v4/system/ping")

    def test_post_with_bot_token_returns_post_id_and_sends_bearer(self):
        client = mattermost.MattermostClient(self.base, token="tok", channel_id="chan")
        post_id = client.post("hello")
        self.assertEqual(post_id, "post123")
        method, path, headers, body = FakeMattermost.requests[0]
        self.assertEqual((method, path), ("POST", "/api/v4/posts"))
        self.assertEqual(headers["Authorization"], "Bearer tok")
        self.assertEqual(body, {"channel_id": "chan", "message": "hello"})

    def test_post_falls_back_to_webhook_without_token(self):
        client = mattermost.MattermostClient(self.base, webhook_url=f"{self.base}/hooks/abc")
        self.assertIsNone(client.post("hello"))
        method, path, _, body = FakeMattermost.requests[0]
        self.assertEqual((method, path), ("POST", "/hooks/abc"))
        self.assertEqual(body, {"text": "hello"})
        self.assertFalse(client.can_read)
        self.assertTrue(client.can_post)

    def test_server_error_raises_mattermost_error(self):
        FakeMattermost.fail_next = True
        client = mattermost.MattermostClient(self.base, token="tok", channel_id="chan")
        with self.assertRaises(mattermost.MattermostError):
            client.post("hello")

    def test_client_without_any_credentials_cannot_post(self):
        client = mattermost.MattermostClient(self.base)
        self.assertFalse(client.can_post)
        with self.assertRaises(mattermost.MattermostError):
            client.post("hello")

    def test_client_from_env_requires_url_and_either_token_or_webhook(self):
        self.assertIsNone(mattermost.client_from_env({}))
        self.assertIsNone(mattermost.client_from_env({"MATTERMOST_URL": self.base}))
        bot = mattermost.client_from_env({"MATTERMOST_URL": self.base + "/", "MATTERMOST_BOT_TOKEN": "t",
                                          "MATTERMOST_CHANNEL_ID": "c"})
        self.assertEqual(bot.base_url, self.base)
        self.assertTrue(bot.can_read)
        hook = mattermost.client_from_env({"MATTERMOST_URL": self.base, "MATTERMOST_WEBHOOK_URL": self.base + "/hooks/abc"})
        self.assertTrue(hook.can_post)
        self.assertFalse(hook.can_read)


if __name__ == "__main__":
    unittest.main()

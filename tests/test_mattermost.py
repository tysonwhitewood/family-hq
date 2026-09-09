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
        if self.path == "/api/v4/users/me":
            return self._reply(200, {"id": "botid", "username": "familyhq"})
        if self.path.startswith("/api/v4/channels/chan/posts"):
            return self._reply(200, {"order": ["p2", "p1"], "posts": {
                "p1": {"id": "p1", "user_id": "u1", "message": "gst 9262", "create_at": 100, "file_ids": [], "root_id": "", "type": ""},
                "p2": {"id": "p2", "user_id": "u2", "message": "", "create_at": 200, "file_ids": ["f1"], "root_id": "", "type": ""},
            }})
        if self.path == "/api/v4/files/f1/info":
            return self._reply(200, {"id": "f1", "name": "shot.png", "mime_type": "image/png", "size": 4})
        if self.path == "/api/v4/files/f1":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.write(b"PNG!")
            return
        if self.path == "/api/v4/files/big/info":
            return self._reply(200, {"id": "big", "name": "x.png", "mime_type": "image/png", "size": 20 * 1024 * 1024})
        if self.path == "/api/v4/files/doc/info":
            return self._reply(200, {"id": "doc", "name": "x.pdf", "mime_type": "application/pdf", "size": 4})
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
        if self.path == "/api/v4/reactions":
            return self._reply(200, body)
        if self.path == "/api/v4/users/ids":
            return self._reply(200, [{"id": i, "username": {"u1": "tawhai", "u2": "mum"}.get(i, "x")} for i in body])
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

    def test_posts_since_returns_ascending_and_flattened(self):
        client = mattermost.MattermostClient(self.base, token="tok", channel_id="chan")
        posts = client.posts_since(50)
        self.assertEqual([p["id"] for p in posts], ["p1", "p2"])
        self.assertIn("since=50", FakeMattermost.requests[0][1])
        self.assertEqual(posts[1]["file_ids"], ["f1"])

    def test_download_file_returns_bytes_and_mime(self):
        client = mattermost.MattermostClient(self.base, token="tok", channel_id="chan")
        content, mime, name = client.download_file("f1")
        self.assertEqual((content, mime, name), (b"PNG!", "image/png", "shot.png"))

    def test_download_rejects_large_and_non_image_files(self):
        client = mattermost.MattermostClient(self.base, token="tok", channel_id="chan")
        with self.assertRaises(mattermost.MattermostError):
            client.download_file("big")
        with self.assertRaises(mattermost.MattermostError):
            client.download_file("doc")

    def test_me_is_cached_and_reaction_and_users_work(self):
        client = mattermost.MattermostClient(self.base, token="tok", channel_id="chan")
        self.assertEqual(client.me()["id"], "botid")
        client.me()
        self.assertEqual(sum(1 for r in FakeMattermost.requests if r[1] == "/api/v4/users/me"), 1)
        client.add_reaction("p1")
        method, path, _, body = FakeMattermost.requests[-1]
        self.assertEqual((method, path, body["emoji_name"], body["user_id"]), ("POST", "/api/v4/reactions", "white_check_mark", "botid"))
        self.assertEqual(client.users_by_ids(["u1", "u2"]), {"u1": "tawhai", "u2": "mum"})

    def test_read_calls_need_a_bot_token(self):
        client = mattermost.MattermostClient(self.base, webhook_url=f"{self.base}/hooks/abc")
        with self.assertRaises(mattermost.MattermostError):
            client.posts_since(0)


if __name__ == "__main__":
    unittest.main()

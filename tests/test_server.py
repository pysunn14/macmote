import json
import time
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from macremote import actions as a
from macremote import commands as c
from macremote import server as srv
from tests.fakes import FakeRunner

JSON_HEADERS = {"Content-Type": "application/json", "X-Mac-Remote": "1"}


class GuardTests(unittest.TestCase):
    def test_requires_custom_header(self):
        self.assertIsNotNone(srv.check_request_headers({"Content-Type": "application/json"}))

    def test_requires_json(self):
        self.assertIsNotNone(srv.check_request_headers({"X-Mac-Remote": "1", "Content-Type": "text/plain"}))

    def test_rejects_cross_site(self):
        headers = {"X-Mac-Remote": "1", "Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"}
        self.assertIsNotNone(srv.check_request_headers(headers))

    def test_accepts_same_origin_json(self):
        headers = {"X-Mac-Remote": "1", "Content-Type": "application/json; charset=utf-8", "Sec-Fetch-Site": "same-origin"}
        self.assertIsNone(srv.check_request_headers(headers))


class ServerCase(unittest.TestCase):
    """A live loopback server backed by the fake runner."""

    @classmethod
    def setUpClass(cls):
        cls.runner = FakeRunner()
        cls.app = srv.App(cls.runner, name="Test Mac")
        cls.server = srv.make_server("127.0.0.1", 0, cls.app)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def post(self, path, body, headers=JSON_HEADERS):
        return self.request("POST", path, body, headers)


class HttpTests(ServerCase):
    def test_page_is_served(self):
        with urllib.request.urlopen(self.base + "/") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("text/html", response.headers["Content-Type"])

    def test_state_endpoint(self):
        status, state = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["name"], "Test Mac")
        self.assertIn("heartbeat_at", state["server"])

    def test_action_without_custom_header_is_forbidden(self):
        status, body = self.post("/api/action", {"action": "volume", "value": 20}, {"Content-Type": "application/json"})
        self.assertEqual(status, 403)
        self.assertIn("error", body)

    def test_invalid_action_is_bad_request(self):
        status, _ = self.post("/api/action", {"action": "volume", "value": 400})
        self.assertEqual(status, 400)

    def test_valid_action_returns_state(self):
        status, body = self.post("/api/action", {"action": "volume", "value": 20})
        self.assertEqual(status, 200)
        self.assertEqual(body["action"]["name"], "volume")
        self.assertIn([c.OSASCRIPT, "-e", "set volume output volume 20"], self.runner.calls)

    def test_mute_on_device_without_mute_control_succeeds(self):
        saved = a.MUTE_MEMORY
        with tempfile.TemporaryDirectory() as tmp:
            a.MUTE_MEMORY = Path(tmp) / "mute.json"
            try:
                status, body = self.post("/api/action", {"action": "mute", "value": True})
                self.assertEqual(status, 200)
                self.assertIs(body["muted"], True)
                self.assertEqual(body["mute_mode"], "software")
                status, body = self.post("/api/action", {"action": "mute", "value": False})
                self.assertEqual(status, 200)
                self.assertIs(body["muted"], False)
                self.assertGreater(body["volume"], 0)
            finally:
                a.MUTE_MEMORY = saved

    def test_keyboard_while_displays_sleep_is_conflict(self):
        status, body = self.post("/api/action", {"action": "keyboard", "value": 30})
        self.assertEqual(status, 409)
        self.assertIn("display", body["error"])
        self.assertIn("state", body)


SUDO = {"command": ["sudo", "ls"], "cwd": "/tmp", "user": "me", "pid": 1, "ppid": 2, "parent": "zsh",
        "tty": "/dev/ttys001", "challenge": "Y2hhbGxlbmdl", "timeout": 60}
ASSERTION = {"credential_id": "Y3JlZA", "authenticator_data": "YXV0aA", "client_data_json": "Y2xpZW50", "signature": "c2ln"}
SUBSCRIPTION = {"endpoint": "https://web.push.apple.com/QAbc", "keys": {"p256dh": "cDI1NmRo", "auth": "YXV0aA"}}


class FakePush:
    def __init__(self):
        self.sent = []
        self.subscriptions = []
        self.public_key = "VkFQSUQ"

    def subscribe(self, subscription):
        self.subscriptions.append(subscription)

    def unsubscribe(self, endpoint):
        self.subscriptions = [s for s in self.subscriptions if s["endpoint"] != endpoint]

    def send(self, payload):
        self.sent.append(payload)


class ApprovalRouteTests(ServerCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.push = FakePush()
        cls.app.push = cls.push

    def test_create_list_answer_and_wait(self):
        status, item = self.post("/api/approvals/sudo", SUDO)
        self.assertEqual(status, 201)
        self.assertEqual(item["status"], "pending")
        status, listed = self.request("GET", "/api/approvals/sudo")
        self.assertEqual(status, 200)
        self.assertIn(item["id"], [i["id"] for i in listed["items"]])
        status, answered = self.post(f"/api/approvals/sudo/{item['id']}/answer", ASSERTION)
        self.assertEqual(status, 200)
        self.assertEqual(answered["answer"], ASSERTION)
        status, waited = self.request("GET", f"/api/approvals/sudo/{item['id']}/wait?timeout=1")
        self.assertEqual(status, 200)
        self.assertEqual(waited["status"], "answered")
        self.assertEqual(waited["answer"]["signature"], "c2ln")

    def test_deny_and_unknown_item(self):
        _, item = self.post("/api/approvals/sudo", SUDO)
        status, denied = self.post(f"/api/approvals/sudo/{item['id']}/deny", {})
        self.assertEqual(status, 200)
        self.assertEqual(denied["status"], "denied")
        status, _ = self.post("/api/approvals/sudo/nope/deny", {})
        self.assertEqual(status, 400)
        status, _ = self.request("GET", "/api/approvals/sudo/nope/wait?timeout=1")
        self.assertEqual(status, 404)

    def test_wait_is_capped_and_returns_pending_on_timeout(self):
        _, item = self.post("/api/approvals/sudo", SUDO)
        status, waited = self.request("GET", f"/api/approvals/sudo/{item['id']}/wait?timeout=0")
        self.assertEqual(status, 200)
        self.assertEqual(waited["status"], "pending")

    def test_invalid_request_is_bad_request_and_posts_need_the_header(self):
        status, _ = self.post("/api/approvals/sudo", {**SUDO, "command": "ls"})
        self.assertEqual(status, 400)
        status, _ = self.post("/api/approvals/sudo", SUDO, {"Content-Type": "application/json"})
        self.assertEqual(status, 403)
        status, _ = self.post("/api/approvals/rm", SUDO)
        self.assertEqual(status, 404)

    def test_new_sudo_request_sends_a_push(self):
        before = len(self.push.sent)
        _, item = self.post("/api/approvals/sudo", SUDO)
        deadline = time.monotonic() + 2
        while len(self.push.sent) == before and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(len(self.push.sent), before + 1)
        self.assertIn(item["id"], self.push.sent[-1]["url"])
        self.assertIn("sudo ls", self.push.sent[-1]["body"])


class PushRouteTests(ServerCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.push = FakePush()
        cls.app.push = cls.push

    def test_public_key_is_served(self):
        status, body = self.request("GET", "/api/push/key")
        self.assertEqual(status, 200)
        self.assertEqual(body["public_key"], "VkFQSUQ")

    def test_subscribe_and_unsubscribe(self):
        status, body = self.post("/api/push/subscribe", SUBSCRIPTION)
        self.assertEqual(status, 200)
        self.assertEqual(self.push.subscriptions[-1]["endpoint"], SUBSCRIPTION["endpoint"])
        status, _ = self.post("/api/push/subscribe", {"endpoint": "not a url"})
        self.assertEqual(status, 400)
        status, _ = self.post("/api/push/unsubscribe", {"endpoint": SUBSCRIPTION["endpoint"]})
        self.assertEqual(status, 200)
        self.assertEqual(self.push.subscriptions, [])

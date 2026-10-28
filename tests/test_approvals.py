import threading
import time
import unittest

from macmote import approvals as ap
from macmote.commands import InvalidAction

SUDO = {"command": ["sudo", "rm", "-rf", "/tmp/x"], "cwd": "/tmp/proj", "user": "me", "pid": 123, "ppid": 100,
        "parent": "claude", "tty": "/dev/ttys004", "challenge": "Y2hhbGxlbmdl", "timeout": 120}
ASSERTION = {"credential_id": "Y3JlZA", "authenticator_data": "YXV0aA", "client_data_json": "Y2xpZW50", "signature": "c2ln"}
ENROLL = {"user": "me", "challenge": "Y2hhbGxlbmdl", "rp_id": "mac.example.com", "timeout": 120}


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class CreateTests(unittest.TestCase):
    def test_sudo_request_gets_an_id_and_pending_status(self):
        relay = ap.Relay()
        item = relay.create("sudo", SUDO)
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["kind"], "sudo")
        self.assertEqual(item["command"], SUDO["command"])
        self.assertTrue(item["id"])
        self.assertIn("created_at", item)

    def test_sudo_requests_carry_a_risk_assessment(self):
        relay = ap.Relay()
        self.assertEqual(relay.create("sudo", SUDO)["risk"], {"level": "high", "key": "delete-recursive"})
        self.assertIsNone(relay.create("sudo", {**SUDO, "command": ["sudo", "ls"]})["risk"])

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(InvalidAction):
            ap.Relay().create("rm", SUDO)

    def test_missing_or_wrong_fields_are_rejected(self):
        for broken in ({**SUDO, "command": "rm -rf /"}, {**SUDO, "pid": "123"}, {**SUDO, "challenge": "not base64url!"},
                       {k: v for k, v in SUDO.items() if k != "cwd"}, {**SUDO, "timeout": 10_000}):
            with self.subTest(broken=broken), self.assertRaises(InvalidAction):
                ap.Relay().create("sudo", broken)

    def test_unknown_fields_are_dropped(self):
        item = ap.Relay().create("sudo", {**SUDO, "evil": True})
        self.assertNotIn("evil", item)

    def test_notify_is_called_for_new_sudo_requests_only(self):
        seen = []
        relay = ap.Relay(notify=seen.append)
        relay.create("sudo", SUDO)
        relay.create("enroll", ENROLL)
        self.assertEqual([i["kind"] for i in seen], ["sudo"])


class PendingTests(unittest.TestCase):
    def test_pending_lists_only_open_items_of_that_kind(self):
        relay = ap.Relay()
        first = relay.create("sudo", SUDO)
        relay.create("enroll", ENROLL)
        relay.deny(first["id"])
        second = relay.create("sudo", SUDO)
        self.assertEqual([i["id"] for i in relay.pending("sudo")], [second["id"]])

    def test_enrollment_request_never_carries_the_code(self):
        # The root tool keeps the code; only the phone's typed code travels back through the relay.
        relay = ap.Relay()
        item = relay.create("enroll", {**ENROLL, "code": "482913"})
        self.assertNotIn("code", item)
        self.assertNotIn("code", relay.pending("enroll")[0])


class DecisionTests(unittest.TestCase):
    def test_answer_stores_the_assertion_verbatim_without_judging_it(self):
        relay = ap.Relay()
        item = relay.create("sudo", SUDO)
        answered = relay.answer(item["id"], ASSERTION)
        self.assertEqual(answered["status"], "answered")
        self.assertEqual(answered["answer"], ASSERTION)
        self.assertEqual(relay.get(item["id"])["status"], "answered")

    def test_answer_requires_every_assertion_field_as_base64url(self):
        relay = ap.Relay()
        item = relay.create("sudo", SUDO)
        with self.assertRaises(InvalidAction):
            relay.answer(item["id"], {**ASSERTION, "signature": "@@"})
        with self.assertRaises(InvalidAction):
            relay.answer(item["id"], {k: v for k, v in ASSERTION.items() if k != "signature"})

    def test_enrollment_answer_carries_the_typed_code_and_public_key(self):
        relay = ap.Relay()
        item = relay.create("enroll", ENROLL)
        answer = {"code": "482913", "credential_id": "Y3JlZA", "public_key": "cHVi", "client_data_json": "Y2xpZW50"}
        self.assertEqual(relay.answer(item["id"], answer)["answer"], answer)

    def test_deny_and_double_decisions(self):
        relay = ap.Relay()
        item = relay.create("sudo", SUDO)
        self.assertEqual(relay.deny(item["id"])["status"], "denied")
        with self.assertRaises(InvalidAction):
            relay.answer(item["id"], ASSERTION)
        with self.assertRaises(InvalidAction):
            relay.deny("no-such-id")


class WaitTests(unittest.TestCase):
    def test_wait_returns_as_soon_as_a_decision_lands(self):
        relay = ap.Relay()
        item = relay.create("sudo", SUDO)
        threading.Timer(0.1, relay.deny, args=[item["id"]]).start()
        started = time.monotonic()
        result = relay.wait(item["id"], timeout=5)
        self.assertEqual(result["status"], "denied")
        self.assertLess(time.monotonic() - started, 2)

    def test_wait_times_out_with_the_item_still_pending(self):
        relay = ap.Relay()
        item = relay.create("sudo", SUDO)
        self.assertEqual(relay.wait(item["id"], timeout=0.05)["status"], "pending")


class ExpiryTests(unittest.TestCase):
    def test_items_expire_after_their_timeout_and_are_pruned_later(self):
        clock = FakeClock()
        relay = ap.Relay(clock=clock)
        item = relay.create("sudo", SUDO)
        clock.t += SUDO["timeout"] + 1
        self.assertEqual(relay.pending("sudo"), [])
        self.assertEqual(relay.get(item["id"])["status"], "expired")
        with self.assertRaises(InvalidAction):
            relay.answer(item["id"], ASSERTION)
        clock.t += ap.KEEP_DECIDED + 1
        self.assertIsNone(relay.get(item["id"]))


class FloodTests(unittest.TestCase):
    """A local process can hit the relay freely, so the relay itself must bound the damage."""

    def test_requests_beyond_the_window_limit_are_refused(self):
        clock = FakeClock()
        relay = ap.Relay(clock=clock)
        for n in range(ap.RATE_LIMIT):
            relay.create("sudo", {**SUDO, "command": ["sudo", "echo", str(n)]})
        with self.assertRaises(ap.RateLimited):
            relay.create("sudo", {**SUDO, "command": ["sudo", "echo", "one too many"]})
        clock.t += ap.RATE_WINDOW + 1
        relay.create("sudo", {**SUDO, "command": ["sudo", "echo", "after the window"]})

    def test_refused_requests_send_no_notification(self):
        seen = []
        relay = ap.Relay(notify=seen.append, clock=FakeClock())
        for n in range(ap.RATE_LIMIT):
            relay.create("sudo", {**SUDO, "command": ["sudo", "echo", str(n)]})
        with self.assertRaises(ap.RateLimited):
            relay.create("sudo", {**SUDO, "command": ["sudo", "echo", "flood"]})
        self.assertEqual(len(seen), ap.RATE_LIMIT)

    def test_identical_pending_request_is_refused_with_the_existing_id(self):
        relay = ap.Relay()
        first = relay.create("sudo", SUDO)
        with self.assertRaises(ap.Duplicate) as caught:
            relay.create("sudo", SUDO)
        self.assertEqual(caught.exception.existing_id, first["id"])
        relay.deny(first["id"])
        relay.create("sudo", SUDO)  # allowed again once the earlier one is settled

    def test_kinds_are_limited_separately(self):
        relay = ap.Relay(clock=FakeClock())
        for n in range(ap.RATE_LIMIT):
            relay.create("sudo", {**SUDO, "command": ["sudo", "echo", str(n)]})
        relay.create("enroll", ENROLL)

    def test_only_one_enrollment_may_be_pending(self):
        relay = ap.Relay()
        relay.create("enroll", ENROLL)
        with self.assertRaises(ap.Duplicate):
            relay.create("enroll", {**ENROLL, "challenge": "b3RoZXI"})

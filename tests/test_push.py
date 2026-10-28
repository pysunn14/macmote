import json
import tempfile
import unittest
from pathlib import Path

from macmote import push as p
from macmote.commands import InvalidAction

SUBSCRIPTION = {"endpoint": "https://web.push.apple.com/QAbc", "keys": {"p256dh": "cDI1NmRo", "auth": "YXV0aA"}}


class FakeHelper:
    def __init__(self, status=201):
        self.calls = []
        self.status = status

    def __call__(self, argv, stdin):
        self.calls.append((argv[-1], json.loads(stdin) if stdin else None))
        if argv[-1] == "keygen":
            return json.dumps({"public_key": "UFVC", "private_key": "UFJJVg"})
        return json.dumps({"status": self.status})


class PushTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_keys_are_generated_once_and_persisted(self):
        helper = FakeHelper()
        first = p.Push(self.dir, helper)
        second = p.Push(self.dir, helper)
        self.assertEqual(first.public_key, "UFVC")
        self.assertEqual(second.public_key, "UFVC")
        self.assertEqual([c[0] for c in helper.calls], ["keygen"])

    def test_subscriptions_persist_and_dedupe_by_endpoint(self):
        helper = FakeHelper()
        push = p.Push(self.dir, helper)
        push.subscribe(SUBSCRIPTION)
        push.subscribe(SUBSCRIPTION)
        self.assertEqual(len(p.Push(self.dir, helper).subscriptions), 1)
        push.unsubscribe(SUBSCRIPTION["endpoint"])
        self.assertEqual(p.Push(self.dir, helper).subscriptions, [])

    def test_send_hands_each_subscription_to_the_helper(self):
        helper = FakeHelper()
        push = p.Push(self.dir, helper, subject="https://mac.example.com:18791")
        push.subscribe(SUBSCRIPTION)
        push.send({"title": "t", "body": "b", "url": "/"})
        sent = [c for c in helper.calls if c[0] == "send"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1]["subscription"]["endpoint"], SUBSCRIPTION["endpoint"])
        self.assertEqual(sent[0][1]["payload"]["title"], "t")
        self.assertEqual(sent[0][1]["vapid"]["private_key"], "UFJJVg")
        self.assertEqual(sent[0][1]["subject"], "https://mac.example.com:18791")

    def test_send_without_an_origin_sends_nothing(self):
        helper = FakeHelper()
        push = p.Push(self.dir, helper)
        push.subscribe(SUBSCRIPTION)
        push.send({"title": "t", "body": "b", "url": "/"})
        self.assertEqual([c for c in helper.calls if c[0] == "send"], [])

    def test_gone_subscriptions_are_dropped(self):
        helper = FakeHelper(status=410)
        push = p.Push(self.dir, helper, subject="https://mac.example.com:18791")
        push.subscribe(SUBSCRIPTION)
        push.send({"title": "t", "body": "b", "url": "/"})
        self.assertEqual(push.subscriptions, [])

    def test_subscription_validation(self):
        self.assertEqual(p.validate_subscription({**SUBSCRIPTION, "extra": 1}), SUBSCRIPTION)
        for bad in ({"endpoint": "http://x", "keys": SUBSCRIPTION["keys"]}, {"endpoint": SUBSCRIPTION["endpoint"]},
                    {"endpoint": SUBSCRIPTION["endpoint"], "keys": {"p256dh": "a b", "auth": "x"}}):
            with self.subTest(bad=bad), self.assertRaises(InvalidAction):
                p.validate_subscription(bad)

import unittest

from macmote import setup

RUNNING = {"BackendState": "Running", "CertDomains": ["mac.example.ts.net"],
           "Self": {"DNSName": "mac.example.ts.net.", "TailscaleIPs": ["203.0.113.5"]}}
SERVE = {"TCP": {"18790": {"HTTP": True}},
         "Web": {"mac.example.ts.net:18790": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:18790"}}}}}


class PrerequisiteTests(unittest.TestCase):
    def test_everything_present(self):
        checks = setup.prerequisites(RUNNING, "/usr/bin/swiftc", (3, 9, 6), None)
        self.assertTrue(all(c.ok for c in checks))

    def test_missing_compiler_is_fatal(self):
        checks = setup.prerequisites(RUNNING, None, (3, 9, 6), None)
        bad = [c for c in checks if not c.ok]
        self.assertEqual([c.name for c in bad], ["Swift compiler"])
        self.assertTrue(bad[0].fatal)

    def test_no_certificates_is_flagged(self):
        checks = setup.prerequisites({**RUNNING, "CertDomains": []}, "/usr/bin/swiftc", (3, 9, 6), None)
        self.assertFalse([c for c in checks if c.name == "HTTPS certificates"][0].ok)

    def test_without_tailscale_a_configured_origin_is_enough(self):
        fatal = [c for c in setup.prerequisites(None, "/usr/bin/swiftc", (3, 9, 6), None) if not c.ok and c.fatal]
        self.assertEqual([c.name for c in fatal], ["Tailscale"])
        ok = setup.prerequisites(None, "/usr/bin/swiftc", (3, 9, 6), "https://proxy.example:8443")
        self.assertFalse([c for c in ok if not c.ok and c.fatal])


class ServePlanTests(unittest.TestCase):
    def test_only_the_missing_https_rule_is_planned(self):
        plan = setup.serve_plan(SERVE, "mac.example.ts.net", 18790, 18791)
        self.assertEqual(len(plan), 1)
        self.assertIn("--https=18791", plan[0])
        self.assertEqual(plan[0][-1], "http://127.0.0.1:18790")

    def test_nothing_planned_when_both_rules_exist(self):
        serve = {"Web": {**SERVE["Web"], "mac.example.ts.net:18791": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:18790"}}}}}
        self.assertEqual(setup.serve_plan(serve, "mac.example.ts.net", 18790, 18791), [])

    def test_rule_pointing_elsewhere_counts_as_missing(self):
        serve = {"Web": {"mac.example.ts.net:18790": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9999"}}}}}
        self.assertEqual(len(setup.serve_plan(serve, "mac.example.ts.net", 18790, 18791)), 2)

    def test_empty_status(self):
        self.assertEqual(len(setup.serve_plan({}, "mac.example.ts.net", 18790, 18791)), 2)

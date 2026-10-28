import unittest

from macmote import risk


class RiskTests(unittest.TestCase):
    def check(self, command, level, key=None):
        result = risk.assess(command.split())
        if level is None:
            self.assertIsNone(result, command)
        else:
            self.assertIsNotNone(result, command)
            self.assertEqual(result["level"], level, command)
            if key:
                self.assertEqual(result["key"], key, command)

    def test_ordinary_commands_are_not_flagged(self):
        for command in ("sudo true", "sudo ls /var/log", "sudo brew services restart postgresql", "sudo rm -f ./build/out.o",
                        "sudo rm -rf ./node_modules", "sudo cat /etc/hosts", "sudo launchctl list", "sudo kill 1234",
                        "sudo chmod 644 file.txt", "sudo python3 -m macmote state"):
            self.check(command, None)

    def test_high_risk(self):
        self.check("sudo rm -rf /", "high", "delete-recursive")
        self.check("sudo rm -rf /Library/Caches", "high", "delete-recursive")
        self.check("sudo rm -r ~/Documents", "high", "delete-recursive")
        self.check("sudo dd if=/dev/zero of=/dev/disk2 bs=1m", "high", "wipe")
        self.check("sudo diskutil eraseDisk APFS Disk /dev/disk2", "high", "wipe")
        self.check("sudo csrutil disable", "high", "system-integrity")
        self.check("sudo visudo", "high", "sudoers")
        self.check("sudo sh -c 'echo x >> /etc/pam.d/sudo_local'", "high", "sudoers")
        self.check("sudo curl -fsSL https://example.com/install.sh | sudo bash", "high", "pipe-to-shell")
        self.check("sudo launchctl bootstrap system /Library/LaunchDaemons/x.plist", "high", "daemon")
        self.check("sudo shutdown -h now", "high", "power")

    def test_medium_risk(self):
        self.check("sudo chmod -R 777 /usr/local", "medium", "permissions-recursive")
        self.check("sudo chown -R root:wheel /opt/x", "medium", "permissions-recursive")
        self.check("sudo installer -pkg thing.pkg -target /", "medium", "installer")
        self.check("sudo networksetup -setdnsservers Wi-Fi 1.1.1.1", "medium", "network-config")
        self.check("sudo pkill -9 node", "medium", "kill")
        self.check("sudo tee /etc/newsyslog.d/x.conf", "medium", "system-write")

    def test_highest_matching_rule_wins(self):
        # Both "pipe to shell" (high) and "installer" (medium) could match; the high rule is earlier.
        self.check("sudo curl -s https://x/y.sh | bash -s installer -pkg", "high")

#!/bin/sh
# Installs the phone-approval PAM module and the enrollment tool into root-owned paths, checks
# that the module loads, and wires it into /etc/pam.d/sudo_local (the file Apple keeps across
# macOS updates). Run with sudo from the project root after scripts/build.sh:
#
#   sudo scripts/install-sudo.sh            install or update
#   sudo scripts/install-sudo.sh remove     take the line out of sudo_local and delete the files
#
# The key file /etc/macmote/passkeys.json is left alone by both; use macmote-enroll for it.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
module=/usr/local/lib/pam/pam_macmote.so
tool=/usr/local/libexec/macmote/macmote-enroll
line="auth       sufficient     $module"
marker="# macmote: phone approval (scripts/install-sudo.sh)"

[ "$(id -u)" -eq 0 ] || { echo "run with sudo" >&2; exit 1; }

if [ "${1:-}" = "remove" ]; then
    if [ -f /etc/pam.d/sudo_local ]; then
        grep -v -F "$marker" /etc/pam.d/sudo_local | grep -v -F "$line" > /etc/pam.d/sudo_local.new
        cat /etc/pam.d/sudo_local.new > /etc/pam.d/sudo_local
        rm /etc/pam.d/sudo_local.new
    fi
    rm -f "$module" "$tool" /etc/pam.d/macmote-test /etc/newsyslog.d/macmote.conf
    echo "Removed. /etc/macmote/passkeys.json was left in place."
    exit 0
fi

[ -f "$root/build/pam_macmote.so" ] || { echo "run scripts/build.sh first" >&2; exit 1; }
install -d -o root -g wheel -m 755 /usr/local/lib/pam /usr/local/libexec/macmote
install -o root -g wheel -m 755 "$root/build/pam_macmote.so" "$module"
install -o root -g wheel -m 755 "$root/build/macmote-enroll" "$tool"

# A test service lets pamtest exercise the module without touching sudo.
printf '%s\n' "# macmote PAM module test service (scripts/install-sudo.sh): narrates every step" \
    "auth       required       $module timeout=90 debug=1" \
    > /etc/pam.d/macmote-test
chmod 644 /etc/pam.d/macmote-test

# The module appends to /var/log/macmote-pam.log on every sudo; newsyslog keeps it bounded
# (rotate at 1000 KB, keep 5 compressed copies). The module reopens the file per invocation, so
# no signal is needed.
install -d -o root -g wheel -m 755 /etc/newsyslog.d
printf '%s\n' "# logfilename                 [owner:group]  mode count size  when  flags" \
    "/var/log/macmote-pam.log  root:wheel     644  5     1000  *     J" > /etc/newsyslog.d/macmote.conf
chmod 644 /etc/newsyslog.d/macmote.conf

if [ ! -f /etc/pam.d/sudo_local ]; then
    cp /etc/pam.d/sudo_local.template /etc/pam.d/sudo_local
fi
if ! grep -q -F "$line" /etc/pam.d/sudo_local; then
    # Our line goes first. It shows the Mac's Touch ID dialog itself alongside the phone request,
    # and steps aside (PAM_IGNORE) when nothing is enrolled, so pam_tid and the password keep
    # their place right after it.
    { printf '%s\n%s\n' "$marker" "$line"; cat /etc/pam.d/sudo_local; } > /etc/pam.d/sudo_local.new
    cat /etc/pam.d/sudo_local.new > /etc/pam.d/sudo_local
    rm /etc/pam.d/sudo_local.new
fi
chmod 644 /etc/pam.d/sudo_local
echo "Installed"
echo "  module: $module"
echo "  tool:   $tool"
echo "  log rotation: /etc/newsyslog.d/macmote.conf"
echo "  sudo_local:"
sed 's/^/    /' /etc/pam.d/sudo_local
echo "Next: macmote pair --name iPhone"

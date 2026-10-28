#!/bin/sh
# Builds the native helpers the server and the sudo approval flow use, then renders the icons.
#   build/macctl              private-API helper the server calls for state and actions
#   build/webpush             Web Push encryption and VAPID signing
#   build/pam_macmote.so    PAM module (installed to /usr/local/lib/pam by scripts/install-sudo.sh)
#   build/macmote-enroll    passkey enrollment tool (installed to /usr/local/libexec/macmote)
#   build/pamtest             exercises the PAM module through a test service
#   build/passkey-selftest    checks the passkey verification code with synthetic keys
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$root/build"
swiftc -O -o "$root/build/macctl" "$root/macctl.swift"
swiftc -O -o "$root/build/webpush" "$root/approve/webpush.swift"
swiftc -O -emit-library -module-name pam_macmote -import-objc-header "$root/approve/bridge.h" \
    -o "$root/build/pam_macmote.so" -lpam -framework LocalAuthentication \
    "$root/approve/Passkey.swift" "$root/approve/RelayClient.swift" "$root/approve/pam_macmote.swift"
swiftc -O -module-name macmote_enroll -import-objc-header "$root/approve/bridge.h" \
    -o "$root/build/macmote-enroll" \
    "$root/approve/Passkey.swift" "$root/approve/RelayClient.swift" "$root/approve/QRCode.swift" "$root/approve/enroll/main.swift"
swiftc -O -module-name pamtest -import-objc-header "$root/approve/bridge.h" -o "$root/build/pamtest" -lpam \
    "$root/approve/pamtest.swift"
swiftc -O -module-name passkey_selftest -o "$root/build/passkey-selftest" \
    "$root/approve/Passkey.swift" "$root/approve/QRCode.swift" "$root/approve/selftest/main.swift"
swift "$root/scripts/make_icon.swift" "$root/web"

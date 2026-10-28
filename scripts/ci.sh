#!/bin/sh
# Everything CI runs, in one place, so the same checks run locally before a push:
#   sh scripts/ci.sh
# Needs macOS with the Command Line Tools (swiftc, /usr/bin/python3) and node for the JS syntax check.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"
step() { printf '\n== %s\n' "$1"; }

step "privacy scan"
scripts/check-private.sh --all

step "build native helpers"
scripts/build.sh
for name in macctl webpush pam_macmote.so macmote-enroll pamtest passkey-selftest; do
    [ -x "build/$name" ] || { echo "missing build/$name" >&2; exit 1; }
done

step "python tests"
/usr/bin/python3 -m unittest discover -s tests -t . -v 2>&1 | tail -3

step "passkey and QR self-test"
build/passkey-selftest

step "page syntax"
for file in web/*.js; do node --check "$file"; done
/usr/bin/python3 - <<'PY'
import json, pathlib
json.loads(pathlib.Path("web/manifest.webmanifest").read_text())
html = pathlib.Path("web/index.html").read_text()
for asset in ("app.css", "i18n.js", "app.js", "approvals.js", "manifest.webmanifest"):
    assert asset in html, asset
PY

step "pam module exports and links"
nm -gU build/pam_macmote.so | grep -q " T _pam_sm_authenticate"
nm -gU build/pam_macmote.so | grep -q " T _pam_sm_setcred"
otool -L build/pam_macmote.so | grep -q "/usr/lib/swift/libswiftCore.dylib"

echo
echo "ci: all checks passed"

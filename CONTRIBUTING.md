# Contributing

Thanks for looking. The project is small on purpose: Python standard library on the server,
Swift with system frameworks on the native side, one HTML page. Please keep it that way.

## Ground rules

- **No third-party dependencies.** Python 3.9 standard library only (`/usr/bin/python3` from
  the Command Line Tools); Swift with Foundation, CryptoKit, CoreImage, LocalAuthentication and
  the like; no npm, no package managers.
- **Observe, never assume.** Every action re-reads the state it changed and reports
  `action.reached`; the PAM module verifies signatures itself; the wizard checks `launchctl`,
  the HTTP endpoint and `tailscale serve status` before each step. Keep that discipline.
- **No silent fallbacks.** If something cannot be done, fail with a reason that reaches the log
  and, where possible, the person. A fallback needs a comment explaining why and a test.
- **Record the why.** Comments explain decisions the code cannot show (observed macOS behaviour,
  private-API quirks, security reasoning), not what the line does.
- **English in code, logs and CLI; the page is translated.** User-facing page strings live in
  `web/i18n.js` with both `en` and `ko`; add a key to both.
- **Nothing machine-specific in the tree.** The pre-commit hook (`scripts/check-private.sh`)
  refuses user names, host names, tailnet domains, home paths and e-mail addresses. Put your
  own identifiers in `.private-patterns` (git-ignored) so the hook knows them.

## Layout

| Path | What |
| --- | --- |
| `macremote/` | server package: `state`, `actions`, `approvals` (relay), `push`, `server`, `agent`, `setup`, `config` |
| `macctl.swift` | private-API helper the server calls |
| `approve/` | `Passkey.swift` (verification, key file), `pam_macremote.swift`, `enroll/`, `webpush.swift`, `QRCode.swift`, `pamtest.swift`, `selftest/` |
| `web/` | `index.html`, `app.css`, `i18n.js`, `app.js` (remote), `approvals.js` (approvals, enrollment, push, setup), `sw.js` |
| `scripts/` | `build.sh`, `install-sudo.sh`, `check-private.sh`, `make_icon.swift` |
| `tests/` | `unittest` suites with a fake command runner |

## Working on it

```sh
git config core.hooksPath .githooks        # once: enables the privacy check
scripts/build.sh                           # native helpers and tools
/usr/bin/python3 -m unittest discover -s tests -t .
build/passkey-selftest                     # verification and QR code round trips
sh scripts/ci.sh                           # the full set CI runs; run it before pushing
python3 -m macremote agent restart         # after server changes
```

Write the failing test first. Server behaviour is tested through the fake runner in
`tests/fakes.py` and a live loopback server; nothing in the suite touches the real Mac.

### Changing the PAM module

The module runs as root inside `sudo`, so a crash locks `sudo` until the line is removed from
`/etc/pam.d/sudo_local`. Keep a root shell open (`sudo -s`) in another terminal while testing.

1. `scripts/build.sh`, then `sudo scripts/install-sudo.sh` to copy the module into place.
2. `sudo build/pamtest` runs it through `/etc/pam.d/macremote-test`, which narrates every step
   (`debug=1`). Never point a real service at the test module line: OpenPAM treats a chain
   where every module returns `PAM_IGNORE` as success.
3. Read `/var/log/mac-remote-pam.log`. Every refusal names its reason.

Anything that touches verification (`approve/Passkey.swift`) needs a case in
`approve/selftest/main.swift`, which builds synthetic passkeys with CryptoKit.

### Changing the page

The server reads `web/` from disk on every request, so edits show up on reload without a
restart. Check both languages. For layout, a headless Chrome screenshot at 500px wide is
enough; passkeys and push can only be tried on a real iPhone over the HTTPS origin.

## Pull requests

- One change per PR, with the reasoning in the description. Mention what you tested on a real
  device and what you could not.
- Update `README.md` when behaviour or setup changes; `SECURITY.md` when the trust model does.
- No personal paths, host names or e-mail addresses in commits, PR text or screenshots.
- Commits end with a short body explaining why, not a changelog of files.

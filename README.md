# Mac Remote

A phone remote for your Mac over your tailnet, with one unusual feature: when you are away
from the Mac and an AI coding agent (or anyone) runs `sudo`, your iPhone gets a notification,
shows the exact command, and lets you approve it with Face ID. The approval is a passkey
signature verified by a root-owned PAM module, so nothing running as your user can forge it.

The everyday part is a Control Center style web page: display on/off, lock, "step away",
volume and mute, built-in display brightness, keyboard backlight, battery. Add it to the Home
Screen and it behaves like an app. The UI is English by default and switches to Korean on a
Korean phone (or with the footer button).

## How it fits together

```
iPhone (Safari / Home Screen app)
   │  HTTPS, passkey (WebAuthn), Web Push
   ▼
Tailscale Serve  (TLS, tailnet only)  ──►  127.0.0.1:18790  macremote server (your user)
                                               │    relays approval requests, never judges them
                                               │
sudo ──► pam_macremote.so (root) ──────────────┘    verifies the Face ID signature with
                                                    /etc/mac-remote/passkeys.json (root, 0600)
```

- `macremote/` – Python standard library server: state, actions, the approval relay, Web Push.
- `macctl.swift` – private-API helper for keyboard backlight, display brightness, lock.
- `approve/` – the PAM module, the passkey enrollment tool, the Web Push crypto helper, and the
  test tools. All Swift, CryptoKit only.
- `web/` – one HTML file plus a service worker for notifications.

## Security model

- The server listens on loopback only. Exposure to the phone is Tailscale Serve's job (TLS
  certificate, tailnet-only). Any other HTTPS proxy works too; see *Configuration*.
- A `sudo` approval is a WebAuthn assertion over a random challenge minted by the PAM module.
  The module checks the rpId hash, the origin, the challenge, the user-verified flag (Face ID
  happened for this request) and the ES256 signature against keys in a root-only file. The
  user-level server only moves bytes, so an agent that edits or restarts the server still
  cannot approve anything.
- Enrollment is opened from the Mac with `sudo`. A one-time code is printed in the terminal and
  compared by the root tool; it never passes through the server.
- The module asks the phone and shows the Mac's own Touch ID dialog at the same time; the first
  answer wins and the other prompt is withdrawn. So you can answer wherever you are, with no
  "am I at the Mac" guess. It steps aside (`PAM_IGNORE`) when `sudo -n` was used, when no
  passkey is enrolled, or when the relay is unreachable, so Touch ID and the password keep
  working exactly as before. Denials and timeouts fall through to those as well.
- Known limits: only `sudo` is covered, not macOS GUI authorization dialogs. The page is served
  by the user-level process, so a hostile local process could in principle show a different
  command text than the one being approved; the signature binds the challenge, not the text.
  Approvals are cached by sudo's normal timestamp (5 minutes by default). POST requests
  require a custom header and same-origin, which blocks cross-site forgery from other pages.
- Push payloads are encrypted end to end (RFC 8291); Apple's servers see ciphertext.

## Requirements

- macOS with Xcode Command Line Tools (`swiftc`, `/usr/bin/python3`).
- Tailscale with MagicDNS and HTTPS certificates enabled (for the recommended setup).
- An iPhone with iCloud Keychain on (passkeys) running iOS 16.4 or later (Web Push).

## Install

```sh
python3 -m macremote setup
```

The wizard checks prerequisites, builds the helpers, installs the launchd agent, proposes the
Tailscale Serve rules, installs the PAM module (asks for `sudo`), and prints a QR code. Scan it
with the iPhone camera: it opens the page in Safari with the pairing token filled in, and the
page walks through the rest: register with Face ID, approve a test request (the Mac verifies the
signature and prints the result in the terminal), add the page to the Home Screen, turn on
notifications from the Home Screen app, send a test notification. The wizard waits for that
subscription and finishes. Re-running it is safe; every step looks before it acts.

Flags: `--name` (how the phone is listed), `--yes` (no questions), `--rebuild`, `--skip-sudo`,
`--no-qr` (type the six-digit code instead).

The pieces are available on their own as well:

```sh
scripts/build.sh                          # helpers, PAM module, tools, icons
python3 -m macremote agent install        # launchd user agent; remembers the HTTPS origin in the config file
tailscale serve --bg --http=18790  http://127.0.0.1:18790   # plain page
tailscale serve --bg --https=18791 http://127.0.0.1:18790   # HTTPS: passkeys and push need it
sudo scripts/install-sudo.sh              # PAM module into /usr/local/lib/pam and /etc/pam.d/sudo_local
sudo /usr/local/libexec/mac-remote/macremote-enroll enroll --origin "$(python3 -m macremote config origin)" --name iPhone --test
```

Passkeys must be created in **Safari** (or the Home Screen app), not an in-app browser.
Notifications only work from the Home Screen app; Safari and the Home Screen app keep separate
storage on iOS, which is why the wizard registers in Safari and subscribes from the app.
`macremote-enroll list` and `macremote-enroll remove <id prefix>` manage enrolled phones.

## Configuration

`~/.config/mac-remote/config.json`:

| key          | default                              | meaning                                        |
| ------------ | ------------------------------------ | ---------------------------------------------- |
| `origin`     | detected from Tailscale at install   | HTTPS origin the phone uses                    |
| `port`       | `18790`                              | loopback port the server listens on            |
| `https_port` | `18791`                              | port used when deriving `origin` from Tailscale |

Without Tailscale, set `origin` to whatever HTTPS proxy you put in front of the loopback port.
The origin must match what the phone sees exactly (scheme, host, port); passkeys are bound to it.

Module options on the `sudo_local` line: `timeout=120` (seconds to wait), `local=0` (phone
only, no Touch ID dialog), `server=` (relay URL).

## Operations

```sh
python3 -m macremote agent status|restart|uninstall
python3 -m macremote state                 # current state as JSON
python3 -m macremote run volume 30         # run one action
python3 -m macremote config                # effective configuration
tail ~/.local/state/mac-remote/launchd.err.log   # server log: actions, approvals, push, client errors
tail /var/log/mac-remote-pam.log                 # PAM module log
sudo scripts/install-sudo.sh remove        # unhook from sudo_local, delete module and tool
```

Every action response carries the state re-read from the system; `action.reached` says whether
the Mac actually did it. `GET /api/state` includes per-field errors, the server heartbeat and the
last action. `~/.local/state/mac-remote/` holds the heartbeat, push subscriptions and VAPID keys.

## Testing

```sh
python3 -m unittest discover -s tests -t .   # server, relay, actions, push (fake runner)
build/passkey-selftest                       # verification code against synthetic passkeys
sudo build/pamtest                           # runs the module through /etc/pam.d/macremote-test
scripts/check-private.sh --all               # no machine-specific strings in the tree
```

`build/webpush encrypt` is checked against the RFC 8291 example. The test PAM service always asks
the phone and narrates each step; never use it for real authentication.

## Observed macOS behaviour

- While every display sleeps, keyboard backlight changes are silently ignored; the server
  refuses them with 409 instead.
- Some USB DACs expose no mute control (`output muted` is `missing value`); mute is then done
  in software by driving the volume to 0 and restoring the remembered level.
- Apple's push endpoint rejects VAPID tokens whose subject mentions `localhost`.
- Passkey creation fails with `NotAllowedError` inside in-app browsers (KakaoTalk, etc.). Use Safari.

## License

MIT. See `LICENSE`.

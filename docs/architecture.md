# How Macmote works

```
iPhone (Safari / Home Screen app)
   │  HTTPS, passkey (WebAuthn), Web Push
   ▼
Tailscale Serve  (TLS, tailnet only)  ──►  127.0.0.1:18790  macmote server (your user)
                                               │    relays approval requests, never judges them
                                               │
sudo ──► pam_macmote.so (root) ──────────────┘    verifies the Face ID signature with
                                                    /etc/macmote/passkeys.json (root, 0600)
```

## Pieces

| Path | Role |
| --- | --- |
| `macmote/` | The server, Python standard library only: reads the Mac's state, runs actions, relays approval requests between the PAM module and the phone, sends Web Push. Also the `macmote` command. |
| `macctl.swift` | Helper for the things macOS has no command for: keyboard backlight, built-in display brightness, lock, audio output devices. |
| `macmote/nowplaying.js` | Now-playing info and transport commands, run under `osascript` because MediaRemote only answers Apple processes since macOS 15.4. |
| `approve/pam_macmote.swift` | The PAM module sudo loads. Mints a challenge, asks the phone through the relay, shows the Mac's Touch ID dialog at the same time, verifies the phone's signature itself. |
| `approve/enroll/` | `macmote-enroll`: pairs a phone (QR code, one-time code, verification of a test approval). Runs as root; writes the key file. |
| `approve/webpush.swift` | RFC 8291 encryption and RFC 8292 VAPID signing for Web Push. |
| `web/` | The page: one HTML file, a stylesheet, three scripts, a service worker for notifications. |

## An approval, step by step

1. Something runs `sudo`. PAM loads `pam_macmote.so` first (it is the first line in `/etc/pam.d/sudo_local`).
2. The module reads sudo's own argv from the kernel, the working directory and the parent process name. It mints 32 random bytes as the challenge and posts all of it to the relay on loopback.
3. The relay stores the request, flags risky commands, and sends a push notification to every subscribed phone. At the same time the module shows the Mac's own Touch ID dialog (it switches its effective uid to the invoking user for that, because LocalAuthentication looks up enrolment for the effective user).
4. The phone shows the card. Approving runs `navigator.credentials.get()` with the challenge; Face ID happens; the assertion goes back through the relay.
5. The module checks the rpId hash, the origin, the challenge, the user-verified flag and the ES256 signature against the public keys in `/etc/macmote/passkeys.json`. Success returns `PAM_SUCCESS`. Whichever side answers first wins; the other prompt is withdrawn.
6. Denial, timeout or a bad signature return `PAM_AUTH_ERR`, and sudo continues to Touch ID (`pam_tid`) and the password as before.

The module steps aside entirely (`PAM_IGNORE`) for `sudo -n`, when no passkey is enrolled, or when the relay is down, so sudo never gets worse than it was.

## Pairing

`macmote pair` (or the last step of `macmote setup`) runs the root tool. It opens a pairing window on the relay, prints a QR code whose link carries a token in the URL fragment, and a six-digit code as the typed fallback. The page creates a passkey with Face ID and sends the token, the credential id and the public key back. The root tool accepts the answer only if the token or code matches and the challenge and origin match, then writes the key file. It then sends a real approval request and verifies the phone's signature, so pairing ends with proof.

Passkeys are bound to the HTTPS origin. Safari and the Home Screen app share passkeys (iCloud Keychain) but not local storage, which is why registration happens in Safari and the notification subscription in the Home Screen app.

## Actions and state

`GET /api/state` returns what the Mac reports right now: volume, mute, brightness, keyboard backlight, whether displays sleep, whether the session is locked, battery, audio devices, now playing, and per-field errors. Every action re-reads that state afterwards and reports `action.reached`, so the page shows what happened rather than what was asked. Display and lock changes land asynchronously and are re-read for up to two seconds.

The relay refuses more than six requests of a kind per minute (429) and an identical request while one is pending (409).

## Configuration

`~/.config/macmote/config.json`:

| key | default | meaning |
| --- | --- | --- |
| `origin` | detected from Tailscale at install | HTTPS origin the phone uses; passkeys are bound to it |
| `port` | `18790` | loopback port the server listens on |
| `https_port` | `18791` | port used when deriving `origin` from Tailscale |

Without Tailscale, set `origin` to whatever HTTPS proxy fronts the loopback port (`macmote config set origin https://host:port`). The origin must match what the phone sees exactly.

Module options on the `sudo_local` line: `timeout=120` (seconds to wait), `local=0` (phone only, no Touch ID dialog), `server=` (relay URL).

## Files on the Mac

| Path | Owner | What |
| --- | --- | --- |
| `~/Library/LaunchAgents/io.github.pysunn14.macmote.plist` | you | keeps the server running; generated by `macmote agent install` |
| `~/.config/macmote/config.json` | you | configuration |
| `~/.local/state/macmote/` | you | `server.log` (rotates at 1 MB, 5 kept), heartbeat, push subscriptions and VAPID keys |
| `/usr/local/lib/pam/pam_macmote.so` | root | the PAM module |
| `/usr/local/libexec/macmote/macmote-enroll` | root | the pairing tool |
| `/etc/macmote/passkeys.json` | root, 0600 | enrolled passkeys, rpId and origin |
| `/etc/pam.d/sudo_local` | root | one added line; Apple keeps this file across updates |
| `/etc/pam.d/macmote-test` | root | a test service for `pamtest`; never use it for real authentication |
| `/var/log/macmote-pam.log` | root | the module's log; `newsyslog` rotates it |

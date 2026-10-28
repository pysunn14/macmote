# Security

Macmote lets a phone approve `sudo` on a Mac. That makes its security model the product, so
this file states what the project promises, what it does not, and how to report a problem.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting on this repository ("Security" tab → "Report a
vulnerability"). Please do not open a public issue for anything that could let someone run a
command as root, approve a request they should not, or read the key file.

Include the macOS and iOS versions, how the Mac is exposed (Tailscale Serve or another proxy),
the relevant lines from `/var/log/macmote-pam.log` and `~/.local/state/macmote/launchd.err.log`,
and steps to reproduce. Expect an acknowledgement within a week. Fixes land on `main`; there are
no maintained release branches.

## What is protected, and by what

| Promise | Mechanism |
| --- | --- |
| Nothing running as your user can approve a `sudo` | The PAM module (root) mints a random challenge and verifies the phone's WebAuthn assertion against public keys in `/etc/macmote/passkeys.json` (root, mode 0600). The user-level server only relays bytes. |
| An approval proves the phone's owner was present | `userVerification: required`; the module checks the UV flag, the rpId hash, the origin and the challenge before the ES256 signature. |
| A key cannot be enrolled from the Mac without being root at the keyboard | Enrollment runs under `sudo`, prints a one-time token (QR) and code in the terminal, and compares them in the root tool. They never pass through the server. |
| Other websites cannot drive the page | POST requests need a custom header and same-origin; the CSP forbids framing; the server listens on loopback only. |
| Push payloads are private | RFC 8291 encryption; Apple's push service sees ciphertext. |
| Normal `sudo` keeps working | The module steps aside (`PAM_IGNORE`) for `sudo -n`, when nothing is enrolled, or when the relay is down. Touch ID and the password follow in `sudo_local`. |

## Known limitations

- **Only `sudo` is covered.** macOS authorization dialogs (System Settings, installers) are not.
- **The page comes from the user-level server.** A hostile process running as your user could
  serve a page that shows one command while the module waits on another. The signature binds
  the challenge, not the text shown. Moving the approval page and verification into a root
  daemon is planned; until then, treat "what the phone displays" as advisory.
- **sudo's timestamp applies.** After an approval, `sudo` does not ask again for the configured
  timeout (5 minutes by default) on that terminal.
- **Local prompt.** The module shows the Mac's Touch ID dialog alongside the phone request by
  switching its effective uid to the invoking user for LocalAuthentication; a local success is
  not independently verified by the module beyond what LocalAuthentication reports.
- **The relay accepts requests from any local process.** It refuses more than 6 per minute
  and identical pending requests, so a flood costs at most a few notifications per minute, but
  a hostile local process can still make the phone ring.
- **Logs contain full command lines.** Secrets passed on a `sudo` command line end up in
  `/var/log/macmote-pam.log` and `~/.local/state/macmote/server.log`. Both rotate
  (newsyslog and an in-process rotating handler), but rotated copies are kept.
- **Private macOS APIs** back the keyboard backlight, brightness and lock features. They can
  break with a macOS update; failures show up in `GET /api/state` under `errors`.

## Out of scope

- Attacks by someone who already has root on the Mac.
- A phone that is unlocked and in an attacker's hands (Face ID is required per approval, but
  the phone's own security is the user's).
- Exposure beyond a tailnet. The HTTPS origin is whatever proxy you configure; putting the page
  on the public internet is not supported until passkey login for the page itself exists.

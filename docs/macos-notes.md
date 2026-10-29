# macOS notes and troubleshooting

Things observed while building Macmote, in case they bite you too.

## Passkeys and notifications

- **Passkey creation fails with `NotAllowedError`.** The page was opened in an in-app browser (KakaoTalk, Slack, Instagram, …). Those cannot create passkeys. Open the link in Safari; the page detects this and offers to copy the link.
- **Notifications never arrive.** They only work from the page added to the Home Screen, and only after "sudo approval alerts" was turned on *in that app*. Re-adding the Home Screen icon creates a new app with no subscription; turn alerts on again. `macmote push` shows how many phones are subscribed and `macmote push test` sends one.
- **A notification opens the page but the card is gone.** The request was already approved, denied or expired. Requests wait two minutes by default.
- Safari and the Home Screen app keep separate storage on iOS. Passkeys are shared (iCloud Keychain); the notification subscription is not.

## sudo

- At the Mac, the Touch ID dialog and the phone card appear together; use either. Approvals are cached by sudo's usual timestamp (five minutes per terminal by default).
- `sudo -n` never asks the phone.
- The module writes `/var/log/macmote-pam.log`. "no passkeys … ignoring" means nothing is paired yet; "relay create failed" means the server is not running (`macmote status`).
- LocalAuthentication reports "Biometry is not enrolled" to a process whose effective uid is root. The module switches its effective uid to the invoking user for the dialog; that is why it works from inside sudo.
- `/etc/pam.d/sudo_local` is the file Apple keeps across macOS updates. If an update ever removes the line, `macmote sudo install` puts it back.

## The remote

- While every display sleeps, macOS ignores keyboard backlight changes without an error; the server refuses them with 409 instead of reporting a silent miss.
- Some USB DACs expose no mute control (`output muted` reads `missing value`). Mute is then done in software: volume to 0, and back to the remembered level.
- Since macOS 15.4, MediaRemote answers only processes with a `com.apple.*` bundle identifier. Now playing is read, and commands are sent, by `macmote/nowplaying.js` running under `osascript`. Any player that publishes to Now Playing works, browsers included. Artwork bytes are not available through that path.
- Private APIs back keyboard backlight, brightness, lock and now playing. When a macOS update breaks one, `macmote state` shows the error under `errors` and the page shows it at the bottom.

## Push and Tailscale

- Apple's push endpoint rejects VAPID tokens whose subject mentions `localhost`; the server uses the site's origin.
- Headless Chrome will not open a window narrower than 500 px; the screenshots script accounts for it.
- The Mac itself may not resolve its MagicDNS name when Tailscale DNS is off; the phone still does. `macmote setup` pins the IP when it checks the HTTPS address.

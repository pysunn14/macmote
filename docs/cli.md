# The `macmote` command

```
macmote                      grouped help
macmote status [--json]      what is running, exposed, hooked and pending
macmote setup                guided install; ends with a QR code to pair the phone
macmote pair [--name X]      pair another phone (sudo)
macmote phones               list paired phones (sudo)
macmote unpair <id prefix>   remove a paired phone (sudo)
macmote sudo install|remove  the PAM hook (sudo)
macmote approvals            pending sudo requests
macmote approvals deny <id>  deny one from the terminal
macmote run <action> [value] drive the remote; `macmote actions` lists them
macmote push [test] [--json] notification subscriptions; send a test
macmote state [--local]      the Mac's state as JSON
macmote logs [-n N] [--pam]  recent log lines
macmote config [key]         effective configuration
macmote config set <key> <v> origin, port or https_port
macmote agent install|restart|uninstall|status [--json]
macmote serve                run the server in the foreground
```

Colour and marks appear only on a terminal; piped output is plain. `NO_COLOR` is honoured.

## Actions

| action | value | |
| --- | --- | --- |
| `display_off`, `display_on` | | sleep or wake the displays |
| `lock` | | lock the session |
| `away` | | keyboard backlight off, lock, displays off |
| `volume` | 0–100 | |
| `mute` | true/false | software mute on devices without a mute control |
| `brightness` | 0–100 | built-in display |
| `keyboard` | 0–100 | keyboard backlight; refused while displays sleep |
| `output` | device uid | default audio output (`macmote state` lists uids) |
| `media_toggle`, `media_next`, `media_previous` | | now playing |

`macmote run` exits 0 only when the Mac actually reached the requested state.

## Logs

- `~/.local/state/macmote/server.log`: actions, approvals, push deliveries, errors the phone reported. JSON lines.
- `/var/log/macmote-pam.log`: what the module did for each `sudo`, and why an answer was refused.
- `~/.local/state/macmote/launchd.err.log`: only crashes of the server process.

## Development checks

```sh
sh scripts/ci.sh                             # everything CI runs
python3 -m unittest discover -s tests -t .
build/passkey-selftest                       # passkey verification and QR code round trips
sudo build/pamtest                           # the module through /etc/pam.d/macmote-test
scripts/screenshots.py                       # README images from a mock Mac
```

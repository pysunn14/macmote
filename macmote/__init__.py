"""Macmote: a tailnet-only phone remote for this Mac.

The server listens on loopback only and is exposed to the tailnet by Tailscale Serve.
Each action maps to a fixed argv list (never a shell string), values are validated
strictly, and every response carries state re-read from the system after the action,
so the page shows what the Mac actually did rather than what it was asked to do.
"""

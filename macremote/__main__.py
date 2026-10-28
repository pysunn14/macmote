"""Command line: `serve` for the LaunchAgent, `state` and `run` for diagnosis."""
from __future__ import annotations

import argparse
import json
from typing import Any, Optional

from . import agent, config
from .actions import ACTIONS
from .commands import ActionError, run
from .server import App, serve


def _cli_value(text: Optional[str]) -> Any:
    if text is None:
        return None
    if text in ("true", "false"):
        return text == "true"
    try:
        return int(text)
    except ValueError:
        raise SystemExit(f"value must be an integer, true or false: {text}") from None


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(prog="macremote", description="Mac Remote server and diagnostics")
    commands = parser.add_subparsers(dest="command", required=True)
    serve_parser = commands.add_parser("serve", help="run the HTTP server")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=None, help="loopback port (default: config or 18790)")
    commands.add_parser("state", help="print the current state as JSON")
    run_parser = commands.add_parser("run", help="run one action and print the resulting state")
    run_parser.add_argument("action", choices=sorted(ACTIONS))
    run_parser.add_argument("value", nargs="?")
    agent_parser = commands.add_parser("agent", help="manage the launchd user agent that runs the server")
    agent_parser.add_argument("verb", choices=["install", "uninstall", "restart", "status"])
    config_parser = commands.add_parser("config", help="print effective configuration")
    config_parser.add_argument("key", nargs="?", choices=["origin", "detected_origin", "port", "https_port", "agent_label"])
    args = parser.parse_args(argv)

    if args.command == "serve":
        serve(args.host, config.port() if args.port is None else args.port)
    elif args.command == "agent":
        result = {"install": lambda: agent.install(config.port()), "uninstall": agent.uninstall,
                  "restart": agent.restart, "status": agent.status}[args.verb]()
        print(json.dumps(result, indent=2))
    elif args.command == "config":
        effective = {"origin": config.https_origin(), "detected_origin": config.detect_origin(), "port": config.port(),
                     "https_port": int(config.load().get("https_port", config.DEFAULT_HTTPS_PORT)),
                     "agent_label": config.AGENT_LABEL, "file": str(config.CONFIG_FILE)}
        if args.key:
            print("" if effective[args.key] is None else effective[args.key])
        else:
            print(json.dumps(effective, indent=2))
    elif args.command == "state":
        print(json.dumps(App(run).state(), ensure_ascii=False, indent=2))
    else:
        try:
            state = App(run).act(args.action, _cli_value(args.value))
        except ActionError as error:
            raise SystemExit(str(error)) from None
        print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

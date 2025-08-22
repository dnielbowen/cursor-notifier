#!/usr/bin/env python3
"""
Cursor Agent Notifier

Polls all tmux panes, heuristically detects when a pane that looked like it was
running cursor-agent becomes idle (awaiting input / finished), and sends a
Discord webhook notification. Includes pane path and current git branch.

Requirements: tmux installed; Python 3.8+; internet access for Discord webhook.
No third-party dependencies.

Usage:
  - Set environment variable CURSOR_NOTIFIER_WEBHOOK to your Discord webhook URL
  - Optionally set CURSOR_NOTIFIER_INTERVAL (seconds, default 7)
  - Optionally set CURSOR_NOTIFIER_LINES (buffer lines to scan, default 120)
  - Or override via CLI flags: --webhook-url, --interval, --lines

Heuristic:
  - A pane is considered "active" if its recent output contains a token indicator
    such as "<number> tokens" or the word "tokens" near the bottom of the
    scrollback. When this disappears (and was previously present), the pane is
    considered to have become idle.
  - By default we only monitor panes that appear related to cursor/cursor-agent
    either by their current command or by buffer text. You can broaden/narrow
    this using --match-command and --match-text regexes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from pathlib import Path

# Optional .env support (load before reading environment variables)
try:  # noqa: SIM105
    from dotenv import load_dotenv, find_dotenv  # type: ignore
    # 1) Load from current working directory or nearest parent
    env_path = find_dotenv(usecwd=True)
    if env_path:
        load_dotenv(env_path)
    # 2) Fallback: load a repo-local .env next to this file
    load_dotenv(Path(__file__).resolve().parent.joinpath('.env'))
    # 3) Fallback: allow user-level config
    load_dotenv(Path.home().joinpath('.cursor-notifier.env'))
except Exception:
    pass


TMUX = "tmux"
DEFAULT_INTERVAL_SECONDS = int(os.environ.get("CURSOR_NOTIFIER_INTERVAL", "7"))
DEFAULT_SCAN_LINES = int(os.environ.get("CURSOR_NOTIFIER_LINES", "120"))
DEFAULT_PROCESS_NAMES = os.environ.get("CURSOR_NOTIFIER_PROCESSES", "cursor-agent,node")
DEFAULT_WEBHOOK_URL = os.environ.get("CURSOR_NOTIFIER_WEBHOOK")
DEFAULT_THREAD_ID = os.environ.get("CURSOR_NOTIFIER_THREAD_ID")

# Match token counters like "12 tokens", "12.34k tokens", "5.3k tokens", "554k tokens"
# Examples covered: integer or decimal number, optional 'k' suffix, then the word 'tokens'
TOKEN_REGEX = re.compile(r"\b\d+(?:\.\d+)?k?\s+tokens\b", re.IGNORECASE)


@dataclass
class Pane:
    session_name: str
    window_index: str
    pane_index: str
    pane_id: str
    is_active_flag: bool
    current_path: str
    pane_pid: str
    current_command: str
    pane_tty: str

    @property
    def human_ref(self) -> str:
        return f"{self.session_name}:{self.window_index}.{self.pane_index}"


@dataclass
class PaneState:
    last_seen_active: Optional[bool] = None
    last_transition_ts: float = 0.0


class Notifier:
    def __init__(
        self,
        webhook_url: Optional[str],
        interval_seconds: int,
        scan_lines: int,
        process_names: List[str],
        debug: bool,
        verbose: bool,
        dry_run: bool,
        thread_id: Optional[str] = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.interval_seconds = max(2, interval_seconds)
        self.scan_lines = max(20, scan_lines)
        # Normalize configured process names
        self.process_names = sorted({name.strip() for name in process_names if name.strip()})
        self.debug = debug
        self.verbose = verbose
        self.dry_run = dry_run
        self.pane_id_to_state: Dict[str, PaneState] = {}
        self.thread_id = thread_id

    def log(self, message: str) -> None:
        if self.verbose:
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] {message}")

    def run(self) -> None:
        self.log("Starting Cursor Agent Notifier")
        if not self.webhook_url and not self.dry_run:
            print("Error: Discord webhook URL not provided. Set CURSOR_NOTIFIER_WEBHOOK (via env or .env) or pass --webhook-url.", file=sys.stderr)
            sys.exit(2)

        while True:
            try:
                panes = self._list_tmux_panes()
            except Exception as exc:  # noqa: BLE001
                self.log(f"Failed to list tmux panes: {exc}")
                time.sleep(self.interval_seconds)
                continue

            for pane in panes:
                if not self._should_monitor_pane(pane):
                    continue
                try:
                    buffer_text = self._capture_pane_text(pane.pane_id, self.scan_lines)
                except Exception as exc:  # noqa: BLE001
                    self.log(f"Failed to capture {pane.human_ref}: {exc}")
                    continue

                looks_active = self._detect_active(buffer_text)
                self._maybe_notify_transition(pane, looks_active, buffer_text)

            time.sleep(self.interval_seconds)

    def _should_monitor_pane(self, pane: Pane) -> bool:
        # Monitor panes that have any process on this TTY whose executable name is in configured list
        tty_names = self._pane_tty_process_names(pane)
        decision = any(name in tty_names for name in self.process_names)
        if self.debug:
            self.log(
                f"pane={pane.human_ref} tty={pane.pane_tty or '-'} names={','.join(sorted(tty_names)) or '-'} targets={','.join(self.process_names)} cmd={pane.current_command} monitor={decision}"
            )
        return decision

    def _detect_active(self, text: str) -> bool:
        # Look at the last ~20 lines for tokens occurrence
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        tail = "\n".join(lines[-20:])
        return bool(TOKEN_REGEX.search(tail))

    def _maybe_notify_transition(self, pane: Pane, looks_active: bool, buffer_text: str) -> None:
        state = self.pane_id_to_state.setdefault(pane.pane_id, PaneState())
        if state.last_seen_active is None:
            state.last_seen_active = looks_active
            state.last_transition_ts = time.time()
            if self.verbose:
                self.log(f"state init: {pane.human_ref} -> {'active' if looks_active else 'idle'}")
            return

        if state.last_seen_active and not looks_active:
            # Active -> Idle: send notification
            if self.verbose:
                self.log(f"state change: {pane.human_ref} active -> idle")
            self._send_idle_notification(pane)
            state.last_transition_ts = time.time()
        elif (not state.last_seen_active) and looks_active:
            if self.verbose:
                self.log(f"state change: {pane.human_ref} idle -> active")
            state.last_transition_ts = time.time()
        state.last_seen_active = looks_active

    def _send_idle_notification(self, pane: Pane) -> None:
        path = pane.current_path
        branch = self._get_git_branch(path)
        ref = pane.human_ref
        message = f"Cursor-Agent idle in {ref} — {path}"
        if branch:
            message += f" (branch {branch})"
        self.log(f"NOTIFY: {message}")
        if self.dry_run:
            return
        try:
            status = self._post_discord_message(message)
            if self.verbose:
                self.log(f"WEBHOOK OK: status {status}")
        except Exception as exc:  # noqa: BLE001
            self.log(f"Failed to send webhook: {exc}")

    def _post_discord_message(self, content: str) -> int:
        if not self.webhook_url:
            raise RuntimeError("webhook_url missing")
        body = json.dumps({"content": content}).encode("utf-8")
        # Append wait=true so Discord returns JSON on success/errors
        url = self.webhook_url
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}wait=true"
        if self.thread_id:
            url = f"{url}&thread_id={self.thread_id}"
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "cursor-notifier/1.0 (+https://example.local)",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                # On success, Discord typically returns 200/204; we accept any 2xx
                if not (200 <= resp.status < 300):
                    raise RuntimeError(f"Discord webhook status {resp.status}")
                return resp.status
        except urllib.error.HTTPError as e:
            err_text = e.read().decode("utf-8", errors="replace")
            try:
                err_json = json.loads(err_text)
                msg = err_json.get("message")
                code = err_json.get("code")
                detail = f"{e.code} {e.reason}: {msg} (code {code})"
            except Exception:
                detail = f"{e.code} {e.reason}: {err_text.strip()}"
            raise RuntimeError(f"Discord webhook error: {detail}") from None

    def _get_git_branch(self, path: str) -> Optional[str]:
        try:
            cp = subprocess.run(
                ["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            )
            out = cp.stdout.strip()
            if cp.returncode == 0 and out and out != "HEAD":
                return out
        except Exception:
            return None
        return None

    def _list_tmux_panes(self) -> List[Pane]:
        fmt = (
            "#{session_name}\t#{window_index}\t#{pane_index}\t#{pane_id}\t#{pane_active}"
            "\t#{pane_current_path}\t#{pane_pid}\t#{pane_current_command}\t#{pane_tty}"
        )
        cp = subprocess.run(
            [TMUX, "list-panes", "-a", "-F", fmt],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        if cp.returncode != 0:
            raise RuntimeError(cp.stderr.strip() or "tmux list-panes failed")
        panes: List[Pane] = []
        for line in cp.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 9:
                continue
            session_name, window_index, pane_index, pane_id, active_flag, path, pid, cmd, tty = parts
            panes.append(
                Pane(
                    session_name=session_name,
                    window_index=window_index,
                    pane_index=pane_index,
                    pane_id=pane_id,
                    is_active_flag=(active_flag == "1"),
                    current_path=path,
                    pane_pid=pid,
                    current_command=cmd,
                    pane_tty=tty,
                )
            )
        return panes

    def _pane_tty_process_names(self, pane: Pane) -> List[str]:
        # Return executable names (comm) of all processes attached to this TTY
        tty = pane.pane_tty
        if not tty:
            return []
        tty_short = tty.replace("/dev/", "")
        try:
            cp = subprocess.run(
                [
                    "ps",
                    "-t",
                    tty_short,
                    "-o",
                    "comm=",
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=3,
            )
        except Exception:
            return []
        names: List[str] = []
        for raw in cp.stdout.splitlines():
            name = raw.strip()
            if name:
                names.append(name)
        return names

    def _capture_pane_text(self, pane_id: str, lines: int) -> str:
        # -J joins wrapped lines; -p prints; -S -N starts N lines from the bottom
        cp = subprocess.run(
            [TMUX, "capture-pane", "-p", "-J", "-t", pane_id, "-S", f"-{int(lines)}"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        if cp.returncode != 0:
            raise RuntimeError(cp.stderr.strip() or f"tmux capture-pane failed for {pane_id}")
        return cp.stdout


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Notify when cursor-agent becomes idle in tmux panes")
    parser.add_argument("--webhook-url", default=DEFAULT_WEBHOOK_URL, help="Discord webhook URL (or set CURSOR_NOTIFIER_WEBHOOK)")
    parser.add_argument("--thread-id", default=DEFAULT_THREAD_ID, help="Optional Discord thread_id to post into")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS, help="Polling interval seconds (default: env or 7)")
    parser.add_argument("--lines", type=int, default=DEFAULT_SCAN_LINES, help="How many recent lines to scan (default: env or 120)")
    parser.add_argument("--process-names", default=DEFAULT_PROCESS_NAMES, help="Comma-separated executable names to monitor (default: cursor-agent,node)")
    parser.add_argument("--debug", action="store_true", help="Log diagnostics for all panes (not just matches)")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--dry-run", action="store_true", help="Do not send webhooks; log only")
    parser.add_argument("--test", nargs="?", const="cursor-notifier test message", help="Send a test message and exit (optionally provide custom text)")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    # Split and normalize process names
    process_names = [part.strip() for part in (args.process_names or "").split(",") if part.strip()]
    notifier = Notifier(
        webhook_url=args.webhook_url,
        thread_id=args.thread_id,
        interval_seconds=args.interval,
        scan_lines=args.lines,
        process_names=process_names,
        debug=args.debug,
        verbose=args.verbose,
        dry_run=args.dry_run,
    )
    # One-off webhook test path (test should honor .env loaded at import time)
    if args.test is not None:
        message = args.test or "cursor-notifier test message"
        if not notifier.webhook_url and not notifier.dry_run:
            print("Error: no webhook set. Use CURSOR_NOTIFIER_WEBHOOK in .env or --webhook-url.", file=sys.stderr)
            sys.exit(2)
        if args.dry_run:
            notifier.log(f"[dry-run] Would send test message: {message}")
            return
        try:
            notifier._post_discord_message(message)
            notifier.log("Test message sent successfully")
            return
        except Exception as exc:  # noqa: BLE001
            print(f"Webhook test failed: {exc}", file=sys.stderr)
            sys.exit(1)
    notifier.run()


if __name__ == "__main__":
    main()

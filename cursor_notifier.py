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


TMUX = "tmux"
DEFAULT_INTERVAL_SECONDS = int(os.environ.get("CURSOR_NOTIFIER_INTERVAL", "7"))
DEFAULT_SCAN_LINES = int(os.environ.get("CURSOR_NOTIFIER_LINES", "120"))
DEFAULT_MATCH_COMMAND = os.environ.get("CURSOR_NOTIFIER_MATCH_COMMAND", r"^(cursor|cursor-agent)$")
DEFAULT_MATCH_TEXT = os.environ.get("CURSOR_NOTIFIER_MATCH_TEXT", r"cursor[-_ ]?agent|Cursor Agent|Cursor-?Agent")
DEFAULT_WEBHOOK_URL = os.environ.get("CURSOR_NOTIFIER_WEBHOOK")

TOKEN_REGEX = re.compile(r"\b(\d+\s+tokens|tokens)\b", re.IGNORECASE)


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
        match_command_regex: str,
        match_text_regex: str,
        verbose: bool,
        dry_run: bool,
    ) -> None:
        self.webhook_url = webhook_url
        self.interval_seconds = max(2, interval_seconds)
        self.scan_lines = max(20, scan_lines)
        self.match_command = re.compile(match_command_regex) if match_command_regex else None
        self.match_text = re.compile(match_text_regex, re.IGNORECASE) if match_text_regex else None
        self.verbose = verbose
        self.dry_run = dry_run
        self.pane_id_to_state: Dict[str, PaneState] = {}

    def log(self, message: str) -> None:
        if self.verbose:
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] {message}")

    def run(self) -> None:
        self.log("Starting Cursor Agent Notifier")
        if not self.webhook_url and not self.dry_run:
            print("Error: Discord webhook URL not provided. Set CURSOR_NOTIFIER_WEBHOOK or pass --webhook-url.", file=sys.stderr)
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
        match_by_cmd = bool(self.match_command and self.match_command.search(pane.current_command))
        if match_by_cmd:
            return True
        # As a fallback, cheaply capture a tiny tail and see if it mentions cursor agent.
        if self.match_text:
            try:
                tail_text = self._capture_pane_text(pane.pane_id, 40)
                if self.match_text.search(tail_text):
                    return True
            except Exception:
                return False
        return False

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
            self.log(f"Initialized state for {pane.human_ref}: active={looks_active}")
            return

        if state.last_seen_active and not looks_active:
            # Active -> Idle: send notification
            self._send_idle_notification(pane)
            state.last_transition_ts = time.time()
        elif (not state.last_seen_active) and looks_active:
            self.log(f"{pane.human_ref} became active again")
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
            self._post_discord_message(message)
        except Exception as exc:  # noqa: BLE001
            self.log(f"Failed to send webhook: {exc}")

    def _post_discord_message(self, content: str) -> None:
        if not self.webhook_url:
            raise RuntimeError("webhook_url missing")
        body = json.dumps({"content": content}).encode("utf-8")
        req = urllib.request.Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if not (200 <= resp.status < 300):
                raise RuntimeError(f"Discord webhook status {resp.status}")

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
        fmt = "#{session_name}\t#{window_index}\t#{pane_index}\t#{pane_id}\t#{pane_active}\t#{pane_current_path}\t#{pane_pid}\t#{pane_current_command}"
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
            if len(parts) != 8:
                continue
            session_name, window_index, pane_index, pane_id, active_flag, path, pid, cmd = parts
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
                )
            )
        return panes

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
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS, help="Polling interval seconds (default: env or 7)")
    parser.add_argument("--lines", type=int, default=DEFAULT_SCAN_LINES, help="How many recent lines to scan (default: env or 120)")
    parser.add_argument("--match-command", default=DEFAULT_MATCH_COMMAND, help="Regex to match pane current command for monitoring (default targets cursor/cursor-agent)")
    parser.add_argument("--match-text", default=DEFAULT_MATCH_TEXT, help="Regex to match pane buffer text for monitoring (default targets cursor agent)")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--dry-run", action="store_true", help="Do not send webhooks; log only")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    notifier = Notifier(
        webhook_url=args.webhook_url,
        interval_seconds=args.interval,
        scan_lines=args.lines,
        match_command_regex=args.match_command,
        match_text_regex=args.match_text,
        verbose=args.verbose,
        dry_run=args.dry_run,
    )
    notifier.run()


if __name__ == "__main__":
    main()

# cursor-notifier

Discord webhook notifier for cursor-agent activity in tmux panes.

It polls tmux panes, identifies panes running cursor agent (by foreground TTY process names like `cursor-agent` or `node`), and sends a Discord message when activity transitions from active to idle (token counter disappears).

## Features

- Foreground process detection via pane TTY (robust to shells/multiplexers)
- Token counter detection with flexible regex (e.g. `12 tokens`, `12.3k tokens`)
- Discord webhook integration with error details
- Includes pane working directory and git branch in notifications
- `.env` support for configuration
- Binary entrypoint `cursor-notifier` when installed

## Install (from source)

```bash
# Clone and enter
git clone https://github.com/yourname/cursor-notifier.git
cd cursor-notifier

# Optional: use the provided runner
./run_notifier.sh --verbose --dry-run

# Or install locally in editable mode
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

## Configuration

You can configure via environment variables or a `.env` file in the project directory (loaded automatically if `python-dotenv` is installed).

- `CURSOR_NOTIFIER_WEBHOOK`: Discord webhook URL (required unless `--dry-run`)
- `CURSOR_NOTIFIER_INTERVAL`: Poll interval seconds (default: 7)
- `CURSOR_NOTIFIER_LINES`: Number of lines to scan from tmux buffer (default: 120)
- `CURSOR_NOTIFIER_PROCESSES`: Comma-separated executable names to treat as cursor agent (default: `cursor-agent,node`)

Example `.env`:

```env
CURSOR_NOTIFIER_WEBHOOK=https://discord.com/api/webhooks/123/abc
CURSOR_NOTIFIER_PROCESSES=cursor-agent,node
CURSOR_NOTIFIER_INTERVAL=7
CURSOR_NOTIFIER_LINES=120
```

## Usage

After installation, you can run the console script:

```bash
cursor-notifier --verbose
```

Or from source within this repo:

```bash
./run_notifier.sh --verbose
```

Useful flags:

- `--process-names`: Comma-separated executable names to monitor (e.g., `cursor-agent,node`)
- `--interval`: Polling interval seconds
- `--lines`: How many recent lines to scan in each pane
- `--debug`: Log diagnostics for all panes
- `--verbose`: Print logs
- `--dry-run`: Do not send Discord messages
- `--test [message]`: Send a test message to the webhook and exit

## How it works

- Enumerates tmux panes with `tmux list-panes -a`
- For each pane, gets its TTY and uses `ps -t <tty> -o comm=` to list executable names
- If any of those names match your configured target names, the pane is considered a cursor agent pane
- It captures recent pane output and checks for a token counter; when it disappears, it sends a Discord message with the pane path and git branch

## Packaging and publishing to PyPI

This project is set up with `pyproject.toml` using setuptools and exposes an entrypoint `cursor-notifier`.

### Build

```bash
python3 -m pip install --upgrade build twine
python3 -m build
```

Artifacts will be in `dist/` (e.g., `cursor_notifier-0.1.0-py3-none-any.whl` and `.tar.gz`).

### Test upload to TestPyPI (recommended)

```bash
python3 -m twine upload --repository testpypi dist/*
# Then install to verify
python3 -m pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple cursor-notifier
```

### Upload to PyPI

```bash
python3 -m twine upload dist/*
```

You will need a PyPI account and API token. Store credentials securely, e.g., in `~/.pypirc`:

```ini
[distutils]
index-servers = pypi testpypi

[pypi]
  repository = https://upload.pypi.org/legacy/
  username = __token__
  password = pypi-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX

[testpypi]
  repository = https://test.pypi.org/legacy/
  username = __token__
  password = pypi-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

## Troubleshooting

- 403 on webhook: ensure the webhook URL is correct, the webhook has channel access, and you are not using the Slack-compatible URL. Use `--test` to get detailed error info.
- No panes detected: confirm tmux is running and that `tmux list-panes -a` works. Ensure the target process names include the executable you run (e.g., add `node`).
- Git branch missing: the pane path must be inside a git repo.

## License

MIT

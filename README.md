# devctl — Your Personal Dev Environment Manager

A CLI tool that acts as the single source of truth for your entire dev setup across machines.
Think of it as a smarter, developer-focused version of Ansible — but just for you.

## Why

- New machine setup takes hours of reinstalling, reconfiguring, re-cloning.
- Dotfiles on laptop vs desktop drift apart.
- You forget which projects exist, where they live, and how to run them.
- Secrets and env vars are scattered everywhere.

`devctl` fixes all of that with one cohesive tool.

## Features

| Command | What it does |
|---|---|
| `devctl jump` | Fuzzy-search registered projects and `cd` into one instantly |
| `devctl env new <name>` | Scaffold a new project folder and auto-register it |
| `devctl sync push/pull/status` | Push/pull tracked dotfiles to a private Git repo |
| `devctl snapshot` | Capture installed packages, VS Code extensions, aliases, env keys |
| `devctl restore` | Rebuild your environment on a fresh machine from a profile |
| `devctl secret set/get/list` | Per-project encrypted `.env` vault (PyNaCl) |

## Install

```bash
pip install -e .
# or once published:
pip install devctl
```

Requires Python 3.9+.

## Quick start

```bash
# Register an existing project
devctl env add ~/code/my-app

# Or scaffold a new one
devctl env new my-app --lang python

# Jump to any project (interactive fuzzy picker)
devctl jump

# Track your dotfiles
devctl sync init git@github.com:you/dotfiles.git
devctl sync add ~/.zshrc ~/.vimrc ~/.config/nvim
devctl sync push

# Snapshot this machine, restore on another
devctl snapshot           # writes ~/.devctl/profile.toml
devctl restore            # reads it back

# Per-project secret vault
devctl secret set DATABASE_URL "postgres://..."
devctl secret get DATABASE_URL
devctl secret list
```

### Shell integration for `jump`

`devctl jump` prints the chosen path. Add this to your `.zshrc` / `.bashrc`
so it actually changes directory:

```bash
j() {
  local dir
  dir="$(devctl jump --print "$@")" && [ -n "$dir" ] && cd "$dir"
}
```

## Storage layout

Everything lives under `~/.devctl/`:

```
~/.devctl/
├── config.toml           # global config (dotfile repo, tracked paths)
├── projects.db           # SQLite project index
├── profile.toml          # latest snapshot
├── dotfiles/             # local clone of your dotfiles repo
└── vault/<project>.enc   # encrypted per-project .env vaults
```

## Tech stack

- **Typer** — type-safe CLI
- **Rich** — gorgeous terminal output
- **SQLite** — local project index
- **PyNaCl** — secret vault encryption
- **GitPython** — dotfile sync
- **tomli / tomli-w** — TOML config

# devctl — performance patterns to apply to every command module

## What was slow and why

Python imports are *synchronous and transitive*. When `cli.py` does:

```python
from .commands import env, jump, secret, snapshot, sync
```

…Python immediately imports all five modules **and every module they import**.
So if `snapshot.py` imports `tarfile`, `hashlib`, and `shutil` at the top, those
are loaded even when you run `devctl version`.

For a CLI tool, startup time is the most felt latency. Every 30 ms of unnecessary
import adds up.

---

## The three fixes (already applied to cli.py, jump.py, db.py)

### 1. Lazy subcommand loading in cli.py ✅
`cli.py` now reads `sys.argv[1]` before importing anything and only loads the
one module the user actually invoked. `devctl jump foo` never imports
`snapshot.py`, `sync.py`, or `secret.py`.

### 2. Defer heavy imports to inside command functions ✅ (applied to jump.py)
Move all non-typer imports from module level to inside the function body.
Python caches imports in `sys.modules`, so the **second call is free**;
only the first call pays the import cost once.

### 3. SQLite connection reuse + WAL mode in db.py ✅
Open the connection once per process (module-level singleton). WAL mode
allows reads to proceed concurrently with a write, and `SYNCHRONOUS=NORMAL`
skips the expensive fsync on every commit without risking corruption.

---

## Pattern to apply to env.py, sync.py, secret.py, snapshot.py

**Before:**
```python
# env.py (current pattern)
import subprocess
import shutil
from pathlib import Path
from rich.console import Console
from rich.table import Table
import typer
from .. import db

console = Console()
app = typer.Typer(help="…")

@app.command()
def add(path: str = typer.Argument(…)):
    p = Path(path)
    …
```

**After:**
```python
# env.py (optimized)
from __future__ import annotations
import typer

app = typer.Typer(help="…")   # ← only typer at module level

@app.command()
def add(path: str = typer.Argument(…)):
    # All heavy imports go HERE, inside the function.
    # Python caches them after the first call — no repeated cost.
    import subprocess
    import shutil
    from pathlib import Path
    from rich.console import Console
    from rich.table import Table
    from .. import db

    console = Console()
    p = Path(path)
    …
```

The rule: **if something is only needed when a specific command runs, it
belongs inside that command function, not at the top of the file.**

Typer itself must stay at module level because `app = typer.Typer()` and the
`@app.command()` decorators are evaluated at import time (cli.py needs them to
build the command tree).

---

## Quick-win checklist for each remaining file

| File            | Likely heavy imports to move inside functions       |
|-----------------|-----------------------------------------------------|
| `env.py`        | `subprocess`, `shutil`, `pathlib`, `rich.table`     |
| `sync.py`       | `subprocess`, `shutil`, `pathlib`, `git` / `pygit2` |
| `secret.py`     | `keyring`, `cryptography`, `base64`                 |
| `snapshot.py`   | `tarfile`, `hashlib`, `shutil`, `pathlib`, `json`   |

---

## Expected speedup

| Command                  | Before (est.) | After (est.) |
|--------------------------|--------------|--------------|
| `devctl version`         | ~300 ms      | ~80 ms       |
| `devctl jump foo`        | ~300 ms      | ~100 ms      |
| `devctl --help`          | ~300 ms      | ~300 ms      |
| `devctl snapshot`        | ~300 ms      | ~120 ms      |

`--help` stays the same because it must load all subcommands to print them.
All targeted invocations become significantly faster.

(Times are rough estimates; actual numbers depend on your machine and which
third-party packages each command imports.)
## License

MIT

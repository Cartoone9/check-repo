<h1 align="center">check-repo</h1>
<p align="center">
  <b>A small and simple terminal dashboard to monitor many Git repositories at once.</b>
</p>

<!-- toc -->
- [Introduction](#introduction)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
  - [Non-interactive mode](#non-interactive-mode)
  - [Interactive mode](#interactive-mode)
  - [Keyboard shortcuts](#keyboard-shortcuts)
- [Output states](#output-states)
- [Environment variables](#environment-variables)
- [Notes](#notes)
- [License](#license)
<!-- tocstop -->

## Introduction
`check-repo` scans a configurable list of folders, detects Git repositories, fetches `origin`, and reports whether each repo is clean, dirty, missing, or out of sync with its tracked remote branch.

It can run either as:
- a single-shot report (good for scripts/cron), or
- a lightweight interactive TUI with keyboard actions for refresh, pull, push, add, and delete.

## Features
- Concurrent repo scanning for fast updates.
- Per-repo branch/ahead/behind indicators.
- Colorized terminal UI with grouped categories (`default`, `linux`, `macos`, `wsl`).
- Optional interactive mode with selection and actions.
- JSON-backed target list (`repo_targets.json`) with in-app add/remove.

## Requirements
- Python 3.9+ (tested on modern Python 3)
- `git` available in your `PATH`
- A Unix-like terminal for interactive mode (uses `termios`/`tty`)

## Installation
Clone this repository into a stable tools directory, then make the launcher executable.

A common XDG-style location is `~/.local/share/check-repo`:

```bash
mkdir -p ~/.local/share
git clone <YOUR_FORK_OR_THIS_REPO_URL> ~/.local/share/check-repo
cd ~/.local/share/check-repo
chmod +x check-repo.zsh
```

If you prefer another location (for example `~/src/check-repo`), use that path consistently in the steps below.

### Pick your command name (`check`, or `check-repo`)
If you want to run it like a real command (for example `check` or `check-repo`), create a symlink in a directory already in your `PATH`:

```bash
mkdir -p ~/.local/bin
ln -sf "$HOME/.local/share/check-repo/check-repo.zsh" ~/.local/bin/check
# or
ln -sf "$HOME/.local/share/check-repo/check-repo.zsh" ~/.local/bin/check-repo
```

Then ensure `~/.local/bin` is in your `PATH` (for zsh):

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

If you prefer aliases instead of symlinks:

```bash
alias check="$HOME/.local/share/check-repo/check-repo.zsh"
# or
alias check-repo="$HOME/.local/share/check-repo/check-repo.zsh"
```

## Configuration
Edit `repo_targets.json` to define what gets scanned.

```json
{
  "default": ["~/dotfiles", "~/scripts"],
  "linux": ["~/.config/hypr"],
  "macos": ["~/.config/kitty"],
  "wsl": []
}
```

Category behavior:
- `default`: always included.
- `linux`: included on Linux (except WSL, which uses the `wsl` category).
- `macos`: included on macOS.
- `wsl`: included on WSL.

## Usage

### Non-interactive mode
Run once and print the dashboard:

```bash
python3 check-repo.py
```

or:

```bash
./check-repo.zsh
```

or if you have set an alias:

```bash
check
```

### Interactive mode
Start the interactive dashboard:

```bash
python3 check-repo.py --interactive
```

or if you have set an alias:

```bash
check -i
```

### Keyboard shortcuts
In interactive mode:

- `j` / `↓`: next actionable repo
- `k` / `↑`: previous actionable repo
- `p`: `git pull --ff-only` on selected repo
- `P`: `git push` on selected repo
- `r`: refresh all repos
- `a`: add a repo target to a category
- `d`: delete selected repo target
- `q`: quit

## Output states
- `CLEAN`: no uncommitted changes, no ahead/behind.
- `DIRTY`: local changes detected (`git status --porcelain`).
- `UPDATES`: branch is ahead and/or behind remote.
- `NOT_FOUND`: configured path does not exist.
- `NOT_REPO`: path exists but is not a Git repository.
- `PULLING` / `PUSHING` / `DELETING`: transient interactive action states.

## Environment variables
- `CHECK_REPOS_CONFIG`: path to an alternate JSON config file.

Example:

```bash
CHECK_REPOS_CONFIG=~/my-repo-list.json python3 check-repo.py
```

## Notes
> [!IMPORTANT]
> This is a personal utility script. It is designed for convenience and visibility, not as a full Git management framework.

- Interactive mode requires a TTY (keyboard + terminal).
- Pull/push output is intentionally suppressed in the UI.

## Credits

Inspired by [git-overview](https://github.com/yimyom/git-overview) by David Bellot.

Similar projects:
[gita](https://github.com/nosarthur/gita)
[repocheck](https://github.com/bevane/repocheck)

## License

[MIT](LICENSE)

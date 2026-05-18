#!/usr/bin/env python3
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import termios
import time
import tty
import argparse
from concurrent.futures import ThreadPoolExecutor

COLORS = {
    "border": "\033[38;2;73;73;73m",
    "category": "\033[38;2;128;128;128m",
    "blue": "\033[1;34m",
    "green": "\033[0;32m",
    "red": "\033[0;31m",
    "yellow": "\033[0;33m",
    "cyan": "\033[0;36m",
    "bg_select": "\033[48;5;238m",
    "nc": "\033[0m",
}

REPO_STATES = {"CLEAN", "UPDATES", "DIRTY", "NOT_FOUND", "NOT_REPO", "PENDING", "PULLING", "PUSHING", "DELETING"}
CONFIG_FILE = "repo_targets.json"


def get_config_path() -> str:
    config_env = os.getenv("CHECK_REPOS_CONFIG")
    if config_env:
        return os.path.expanduser(config_env)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG_FILE)


def clear_screen() -> None:
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def abbreviate(path: str) -> str:
    home = os.path.expanduser("~")
    return path.replace(home + "/", "~/", 1) if path.startswith(home + "/") else path


def parse_branch_tracking(status_line: str) -> tuple[str, int, int]:
    branch = status_line.removeprefix("## ").strip()
    ahead = 0
    behind = 0

    if "..." in branch:
        branch = branch.split("...", 1)[0].strip()
    if "[" in status_line and "]" in status_line:
        tracked = status_line.split("[", 1)[1].split("]", 1)[0]
        ahead_match = re.search(r"ahead (\d+)", tracked)
        behind_match = re.search(r"behind (\d+)", tracked)
        ahead = int(ahead_match.group(1)) if ahead_match else 0
        behind = int(behind_match.group(1)) if behind_match else 0
    return branch or "-", ahead, behind


def get_tracking_counts(path: str) -> tuple[int, int]:
    upstream = subprocess.run(
        ["git", "-C", path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        capture_output=True,
        text=True,
    )
    if upstream.returncode != 0:
        return 0, 0

    counts = subprocess.run(
        ["git", "-C", path, "rev-list", "--left-right", "--count", "@{upstream}...HEAD"],
        capture_output=True,
        text=True,
    )
    if counts.returncode != 0:
        return 0, 0

    parts = counts.stdout.strip().split()
    if len(parts) != 2:
        return 0, 0

    behind, ahead = (int(parts[0]), int(parts[1]))
    return ahead, behind


def check_repo(path: str) -> tuple[str, str, str, int, int]:
    target = abbreviate(path)
    if not os.path.isdir(path):
        return "NOT_FOUND", target, "-", 0, 0

    is_repo = subprocess.run(["git", "-C", path, "rev-parse", "--is-inside-work-tree"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if is_repo.returncode != 0:
        return "NOT_REPO", target, "-", 0, 0

    subprocess.run(["git", "-C", path, "fetch", "-q", "--all", "--prune"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    status = subprocess.run(["git", "-C", path, "status", "-sb"], capture_output=True, text=True).stdout
    status_first = status.splitlines()[0] if status.splitlines() else "## -"
    branch, ahead, behind = parse_branch_tracking(status_first)
    tracked_ahead, tracked_behind = get_tracking_counts(path)
    if tracked_ahead > 0 or tracked_behind > 0:
        ahead, behind = tracked_ahead, tracked_behind
    dirty = subprocess.run(["git", "-C", path, "status", "--porcelain"], capture_output=True, text=True).stdout
    dirty_lines = dirty.splitlines()

    if ahead > 0 or behind > 0:
        return "UPDATES", target, branch, ahead, behind
    if dirty_lines:
        return "DIRTY", target, branch, ahead, behind
    return "CLEAN", target, branch, ahead, behind


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def visible_text(text: str) -> str:
    return ANSI_RE.sub("", text)


def line_fit(text: str, width: int) -> str:
    plain = visible_text(text)
    if len(plain) <= width:
        return text
    return plain[: max(0, width - 3)] + "..."


def format_branch(branch: str) -> str:
    return branch if len(branch) <= 5 else branch[:5] + "..."


def draw_top(width: int) -> str:
    return f"{COLORS['border']}╭{'─' * width}╮{COLORS['nc']}"


def draw_top_with_title(width: int, title: str) -> str:
    plain = title.strip()
    decorated = f" {plain} "
    if len(decorated) >= width:
        decorated = decorated[:width]
        return f"{COLORS['border']}╭{decorated}╮{COLORS['nc']}"
    left = (width - len(decorated)) // 2
    right = width - len(decorated) - left
    return f"{COLORS['border']}╭{'─' * left}{decorated}{'─' * right}╮{COLORS['nc']}"


def draw_separator(width: int) -> str:
    return f"{COLORS['border']}├{'─' * width}┤{COLORS['nc']}"


def draw_bottom(width: int) -> str:
    return f"{COLORS['border']}╰{'─' * width}╯{COLORS['nc']}"


def draw_row(text: str, width: int) -> str:
    text = line_fit(text, width - 2)
    visible = visible_text(text)
    return f"{COLORS['border']}│{COLORS['nc']} {text}{' ' * (width - 2 - len(visible))} {COLORS['border']}│{COLORS['nc']}"


def draw_selected_row(text: str, width: int) -> str:
    text = line_fit(text, width - 2)
    visible = visible_text(text)
    padded = f"{text}{' ' * (width - 2 - len(visible))}"
    return f"{COLORS['border']}│{COLORS['nc']}{COLORS['bg_select']} {padded} {COLORS['nc']}{COLORS['border']}│{COLORS['nc']}"


def wrap_chunks(parts: list[str], width: int) -> list[str]:
    rows: list[str] = []
    cur = ""
    for part in parts:
        if not cur:
            cur = part
            continue
        candidate = f"{cur} | {part}"
        if len(visible_text(candidate)) <= width:
            cur = candidate
        else:
            rows.append(cur)
            cur = part
    if cur:
        rows.append(cur)
    return rows


def centered(text: str, width: int) -> str:
    text = line_fit(text, width)
    visible = visible_text(text)
    left = max(0, (width - len(visible)) // 2)
    right = max(0, width - len(visible) - left)
    return f"{' ' * left}{text}{' ' * right}"


def draw_progress_row(bar: str, done: int, total: int, width: int) -> str:
    left = "Progress"
    right = f"{done}/{total}"

    if width <= len(left) + len(right) + 2:
        return line_fit(f"{left} {bar} {right}", width)

    bar_max = max(1, width - len(left) - len(right) - 4)
    bar_text = line_fit(bar, bar_max)

    row = [" "] * width

    row[0:len(left)] = list(left)
    right_start = width - len(right)
    row[right_start:width] = list(right)

    bar_len = len(visible_text(bar_text))
    bar_start = max(0, (width - bar_len) // 2)
    bar_end = bar_start + bar_len

    left_limit = len(left) + 2
    right_limit = right_start - 2
    if bar_start < left_limit:
        bar_start = left_limit
        bar_end = bar_start + bar_len
    if bar_end > right_limit:
        bar_end = right_limit
        bar_start = max(left_limit, bar_end - bar_len)

    if bar_end > bar_start:
        row[bar_start:bar_end] = list(bar_text[: bar_end - bar_start])

    return "".join(row)


def render(states: list[tuple[str, str, str, int, int]], width: int, categories: list[str], selected_idx: int | None = None) -> list[str]:
    done = sum(1 for s, *_ in states if s != "PENDING")
    total = len(states)
    clean = sum(1 for s, *_ in states if s == "CLEAN")
    updates = sum(1 for s, *_ in states if s == "UPDATES")
    dirty = sum(1 for s, *_ in states if s == "DIRTY")
    missing_not_found = sum(1 for s, *_ in states if s == "NOT_FOUND")
    missing_not_repo = sum(1 for s, *_ in states if s == "NOT_REPO")
    missing = missing_not_found + missing_not_repo
    pending = total - done

    bw = max(20, min(36, width - 24))
    fill = int(done * bw / total) if total else bw
    bar = "█" * fill + "░" * (bw - fill)

    max_target = max(len(t) for _, t, *_ in states) if states else 24
    status_col = max(len(s) for s in REPO_STATES)
    branch_col = len("branch")
    ahead_col = len("ahead")
    behind_col = len("behind")

    target_rows = []
    for idx, (state, target, branch, ahead, behind) in enumerate(states):
        category = categories[idx]
        color = COLORS["cyan"]
        if state == "CLEAN":
            color = COLORS["green"]
        elif state in {"UPDATES", "DIRTY"}:
            color = COLORS["red"]
        elif state in {"PULLING", "PUSHING", "DELETING"}:
            color = COLORS["blue"]
        elif state in {"NOT_FOUND", "NOT_REPO"}:
            color = COLORS["yellow"]

        target_rows.append((idx, category, target, branch, ahead, behind, state, color))

    summary_label_col = max(len(lbl) for lbl in ("Healthy", "Attention", "Missing", "Pending"))
    summary_rows = [
        f"{'Pending':<{summary_label_col}}   {pending}",
        f"{'Healthy':<{summary_label_col}}   {clean}",
        f"{'Missing':<{summary_label_col}}   {missing} ({missing_not_found} not found, {missing_not_repo} not repo)",
        f"{'Attention':<{summary_label_col}}   {updates + dirty} ({updates} updates, {dirty} dirty)",
    ]

    progress_row = draw_progress_row(bar, done, total, width - 2)

    category_groups: dict[str, list[tuple[str, str, int, int, str, str]]] = {}
    for idx, category, target, branch, ahead, behind, state, color in target_rows:
        category_groups.setdefault(category, []).append((idx, target, branch, ahead, behind, state, color))

    grouped_target_rows = []
    for category in dict.fromkeys(categories):
        rows = category_groups.get(category)
        if not rows:
            continue
        grouped_target_rows.append(("header", f"{COLORS['category']}[{category}]{COLORS['nc']}"))
        grouped_target_rows.extend(("target", row) for row in rows)

    title = "check-repo"
    right_columns_min = branch_col + 1 + ahead_col + 1 + behind_col + 1 + status_col
    right_padding = 2
    min_line_width = 2 + 2 + max_target + 2 + right_columns_min + right_padding
    right_header = f"{'branch':>{branch_col}} {'ahead':>{ahead_col}} {'behind':>{behind_col}} {'status':>{status_col}}"
    header_line = f"{'Targets':<{max_target + 2}} {right_header}"
    content_rows = [title, progress_row, header_line, *[r[1] if r[0] == "header" else f"  {r[1][1]} {format_branch(r[1][2])} {r[1][5]}" for r in grouped_target_rows], *summary_rows]
    computed_width = max(width, min_line_width, max(len(visible_text(r)) for r in content_rows) + 2)

    out = []
    out.append(draw_top(computed_width))
    out.append(draw_row(centered(title, computed_width - 2), computed_width))
    out.append(draw_separator(computed_width))
    out.append(draw_row(draw_progress_row(bar, done, total, computed_width - 2), computed_width))
    out.append(draw_separator(computed_width))

    target_text_width = max_target
    header_left_width = max(0, computed_width - 2 - len(right_header) - 2 - right_padding)
    header_left = f"{'Targets':<{header_left_width}}"
    out.append(draw_row(f"{header_left}  {right_header}{' ' * right_padding}", computed_width))
    for row_type, row in grouped_target_rows:
        if row_type == "header":
            label = visible_text(row)
            bar_len = max(1, computed_width - 2 - len(label) - 4)
            header_text = f" {row}  {COLORS['border']}{'─' * bar_len}{COLORS['nc']} "
            out.append(draw_row(header_text, computed_width))
            continue

        idx, target, branch, ahead, behind, state, color = row
        target_display = target
        cursor = f"{COLORS['cyan']}›{COLORS['nc']}" if selected_idx == idx else " "
        branch_short = format_branch(branch)
        branch_colored = f"{COLORS['cyan']}{branch_short:>{branch_col}}{COLORS['nc']}"
        ahead_colored = f"{COLORS['green'] if ahead > 0 else COLORS['nc']}{ahead:>{ahead_col}}{COLORS['nc']}"
        behind_colored = f"{COLORS['red'] if behind > 0 else COLORS['nc']}{behind:>{behind_col}}{COLORS['nc']}"
        right_columns = f"{branch_colored} {ahead_colored} {behind_colored} {color}{state:>{status_col}}{COLORS['nc']}"
        left_width = max(0, computed_width - 2 - len(visible_text(right_columns)) - 2 - right_padding)
        left_text = f"{cursor} {target_display}"
        left_text = line_fit(left_text, left_width).ljust(left_width)
        line = f"{left_text}  {right_columns}{' ' * right_padding}"
        if selected_idx == idx:
            plain_right_columns = f"{branch_short:>{branch_col}} {ahead:>{ahead_col}} {behind:>{behind_col}} {state:>{status_col}}"
            plain_left_width = max(0, computed_width - 2 - len(plain_right_columns) - 2 - right_padding)
            plain_left_text = line_fit(f"› {target_display}", plain_left_width).ljust(plain_left_width)
            selected_line = f"{plain_left_text}  {plain_right_columns}{' ' * right_padding}"
            out.append(draw_selected_row(selected_line, computed_width))
        else:
            out.append(draw_row(line, computed_width))

    out.append(draw_separator(computed_width))
    out.append(draw_row(f"{COLORS['cyan']}{summary_rows[0]}{COLORS['nc']}", computed_width))
    out.append(draw_row(f"{COLORS['green']}{summary_rows[1]}{COLORS['nc']}", computed_width))
    out.append(draw_row(f"{COLORS['yellow']}{summary_rows[2]}{COLORS['nc']}", computed_width))
    out.append(draw_row(f"{COLORS['red']}{summary_rows[3]}{COLORS['nc']}", computed_width))
    out.append(draw_bottom(computed_width))
    return out



def is_wsl() -> bool:
    if os.getenv("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/version", "r", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def load_repo_targets() -> list[tuple[str, str]]:
    with open(get_config_path(), "r", encoding="utf-8") as f:
        config = json.load(f)

    targets = [("default", os.path.expanduser(d)) for d in config.get("default", [])]

    system = platform.system().lower()
    if system == "darwin":
        targets.extend(("macos", os.path.expanduser(d)) for d in config.get("macos", []))
    elif system == "linux":
        if is_wsl():
            targets.extend(("wsl", os.path.expanduser(d)) for d in config.get("wsl", []))
        else:
            targets.extend(("linux", os.path.expanduser(d)) for d in config.get("linux", []))

    return targets


def normalize_user_path(path: str) -> str:
    expanded = os.path.expanduser(path.strip())
    home = os.path.expanduser("~")
    if expanded.startswith(home + os.sep):
        return "~" + expanded[len(home):]
    return expanded


def save_repo_target(path: str, category: str) -> bool:
    with open(get_config_path(), "r", encoding="utf-8") as f:
        config = json.load(f)
    normalized = normalize_user_path(path)
    expanded = os.path.expanduser(normalized)
    config.setdefault(category, [])
    if expanded in [os.path.expanduser(p) for p in config[category]]:
        return False
    config[category].append(normalized)
    with open(get_config_path(), "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    return True


def get_keypress() -> str:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            ch2 = sys.stdin.read(1)
            ch3 = sys.stdin.read(1)
            if ch2 == "[" and ch3 == "A":
                return "UP"
            if ch2 == "[" and ch3 == "B":
                return "DOWN"
            return "ESC"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def scan_all(dirs: list[str], categories: list[str], width: int, selected_idx: int | None = None, render_live: bool = True) -> tuple[list[tuple[str, str, str, int, int]], int]:
    states = [("PENDING", abbreviate(d), "-", 0, 0) for d in dirs]
    with ThreadPoolExecutor(max_workers=min(16, len(dirs))) as ex:
        futures = {ex.submit(check_repo, d): i for i, d in enumerate(dirs)}
        printed_lines = 0
        while True:
            if render_live:
                lines = render(states, width, categories, selected_idx=selected_idx)
                if sys.stdout.isatty() and printed_lines:
                    sys.stdout.write(f"\033[{printed_lines}A")
                sys.stdout.write("\n".join(lines) + "\n")
                sys.stdout.flush()
                printed_lines = len(lines)
            if all(s != "PENDING" for s, *_ in states):
                break
            for fut in list(futures):
                if fut.done():
                    i = futures.pop(fut)
                    states[i] = fut.result()
            time.sleep(0.08)
    return states, printed_lines


def legend_box(width: int) -> list[str]:
    sep = f" {COLORS['border']}|{COLORS['nc']} "
    rows = [
        centered(
            f"{COLORS['cyan']}q:{COLORS['nc']} quit | "
            f"{COLORS['cyan']}p:{COLORS['nc']} pull | "
            f"{COLORS['cyan']}P:{COLORS['nc']} push | "
            f"{COLORS['cyan']}r:{COLORS['nc']} refresh",
            width - 2,
        ).replace(" | ", sep),
        centered(
            f"{COLORS['cyan']}a:{COLORS['nc']} add a repo | "
            f"{COLORS['cyan']}d:{COLORS['nc']} delete a repo | "
            f"{COLORS['cyan']}t:{COLORS['nc']} toggle display",
            width - 2,
        ).replace(" | ", sep),
    ]
    out = [draw_top_with_title(width, "commands")]
    out.extend(draw_row(r, width) for r in rows)
    out.append(draw_bottom(width))
    return out


def delete_repo_target(path: str, category: str) -> bool:
    with open(get_config_path(), "r", encoding="utf-8") as f:
        config = json.load(f)
    expanded = os.path.expanduser(path.strip())
    updated = [p for p in config.get(category, []) if os.path.expanduser(p) != expanded]
    if len(updated) == len(config.get(category, [])):
        return False
    config[category] = updated
    with open(get_config_path(), "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    return True


def main():
    parser = argparse.ArgumentParser(
        prog="check_repos.py",
        description="Check git repositories and optionally run interactive TUI mode.",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="help", help="Show this help message and exit.")
    parser.add_argument("-i", "--interactive", action="store_true", help="Enable interactive live-updating TUI mode.")
    args, _ = parser.parse_known_args()

    show_all_categories = False

    def visible_targets() -> list[tuple[str, str]]:
        all_targets = load_repo_targets()
        if show_all_categories:
            with open(get_config_path(), "r", encoding="utf-8") as f:
                config = json.load(f)
            merged: list[tuple[str, str]] = []
            for cat in ("default", "linux", "macos", "wsl"):
                merged.extend((cat, os.path.expanduser(d)) for d in config.get(cat, []))
            return merged
        return all_targets

    targets = visible_targets()
    dirs = [d for _, d in targets]
    categories = [c for c, _ in targets]
    width = max(max(len(abbreviate(d)) for d in dirs) + 34, 64)

    if not args.interactive:
        live_render = sys.stdout.isatty()
        states, _ = scan_all(dirs, categories, width, selected_idx=None, render_live=live_render)
        if not live_render:
            lines = render(states, width, categories, selected_idx=None)
            sys.stdout.write("\n".join(lines) + "\n")
        return

    states = [("PENDING", abbreviate(d), "-", 0, 0) for d in dirs]
    printed_lines = 0
    status_lines: list[str] = [f"{COLORS['cyan']}Starting check...{COLORS['nc']}"]
    selected_idx: int | None = None
    interactive = args.interactive and sys.stdin.isatty() and sys.stdout.isatty()

    def selectable_indices() -> list[int]:
        return list(range(len(states)))

    def next_select(current: int, direction: int) -> int:
        picks = selectable_indices()
        if not picks:
            return current
        if current not in picks:
            return picks[0]
        pos = picks.index(current)
        return picks[(pos + direction) % len(picks)]

    def status_box(messages: list[str]) -> list[str]:
        rows = messages[-4:]
        while len(rows) < 4:
            rows.insert(0, "")
        return [
            draw_top_with_title(width, "status"),
            *[draw_row(msg, width) for msg in rows],
            draw_bottom(width),
        ]

    def render_ui() -> list[str]:
        lines = render(states, width, categories, selected_idx=selected_idx)
        lines.extend(legend_box(width))
        lines.extend(status_box(status_lines))
        return lines

    def show_action_state(index: int, action_state: str, status_message: str) -> None:
        nonlocal printed_lines
        st, tgt, br, ah, bh = states[index]
        states[index] = (action_state, tgt, br, ah, bh)
        status_lines.append(status_message)
        lines = render_ui()
        clear_screen()
        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()
        printed_lines = len(lines)

    def run_scan(show_full_ui: bool) -> None:
        nonlocal states, printed_lines
        states = [("PENDING", abbreviate(d), "-", 0, 0) for d in dirs]
        with ThreadPoolExecutor(max_workers=min(16, len(dirs))) as ex:
            futures = {ex.submit(check_repo, d): i for i, d in enumerate(dirs)}
            while True:
                lines = render_ui() if show_full_ui else render(states, width, categories, selected_idx=None)
                clear_screen()
                sys.stdout.write("\n".join(lines) + "\n")
                sys.stdout.flush()
                printed_lines = len(lines)
                if all(s != "PENDING" for s, *_ in states):
                    break
                for fut in list(futures):
                    if fut.done():
                        i = futures.pop(fut)
                        states[i] = fut.result()
                time.sleep(0.08)

    run_scan(show_full_ui=interactive)
    if not interactive:
        return
    selected_idx = 0
    status_lines.append(f"{COLORS['cyan']}Ready.{COLORS['nc']} Use commands above.")

    while True:
        lines = render_ui()
        clear_screen()
        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()
        printed_lines = len(lines)

        key = get_keypress()
        if key == "q":
            return
        if key in {"j", "DOWN"}:
            if selected_idx is None:
                selected_idx = 0
            selected_idx = next_select(selected_idx, 1)
        elif key in {"k", "UP"}:
            if selected_idx is None:
                selected_idx = 0
            selected_idx = next_select(selected_idx, -1)
        elif key in {"p", "P"}:
            if selected_idx is None:
                selected_idx = 0
            blocked_states = {"CLEAN", "NOT_FOUND", "NOT_REPO"}
            if states[selected_idx][0] in blocked_states:
                state_name = states[selected_idx][0]
                status_lines.append(f"{COLORS['yellow']}Action blocked:{COLORS['nc']} selected repo is {state_name}.")
                continue
            repo = dirs[selected_idx]
            status_hint = "PULLING" if key == "p" else "PUSHING"
            show_action_state(selected_idx, status_hint, f"{COLORS['blue']}{status_hint}...{COLORS['nc']} {abbreviate(repo)}")
            cmd = ["git", "-C", repo, "pull", "--ff-only"] if key == "p" else ["git", "-C", repo, "push"]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            states[selected_idx] = check_repo(repo)
            status_lines.append(f"{COLORS['blue']}{status_hint} complete{COLORS['nc']} {abbreviate(repo)}")
        elif key == "r":
            status_lines.append(f"{COLORS['blue']}REFRESHING...{COLORS['nc']}")
            run_scan(show_full_ui=True)
            selected_idx = None
            status_lines.append(f"{COLORS['cyan']}Refreshed all repositories.{COLORS['nc']}")
        elif key == "a":
            mapping = {"1": "default", "2": "linux", "3": "macos", "4": "wsl"}
            status_lines.append(
                f"{COLORS['cyan']}c:{COLORS['nc']} cancel | "
                f"{COLORS['cyan']}1:{COLORS['nc']} default | "
                f"{COLORS['cyan']}2:{COLORS['nc']} linux | "
                f"{COLORS['cyan']}3:{COLORS['nc']} macos | "
                f"{COLORS['cyan']}4:{COLORS['nc']} wsl"
            )
            lines = render_ui()
            clear_screen()
            sys.stdout.write("\n".join(lines) + "\n")
            sys.stdout.flush()
            printed_lines = len(lines)
            raw = sys.stdin.readline()
            category = mapping.get(raw.strip().lower(), None) if raw else None
            if raw and raw.strip().lower() in {"c", "cancel", "q", "quit"}:
                category = None
            if not category:
                status_lines.append(f"{COLORS['yellow']}Add canceled.{COLORS['nc']}")
                continue
            status_lines.append(f"{COLORS['cyan']}path ({category}):{COLORS['nc']} type and press return")
            lines = render_ui()
            clear_screen()
            sys.stdout.write("\n".join(lines) + "\n")
            sys.stdout.flush()
            printed_lines = len(lines)
            new_path = sys.stdin.readline().strip()
            new_repo = os.path.expanduser(new_path) if new_path else ""
            status_lines.append(f"{COLORS['blue']}ADDING...{COLORS['nc']} {abbreviate(new_repo or '')}")
            if new_path and save_repo_target(new_path, category):
                previous = {
                    (categories[i], dirs[i]): states[i]
                    for i in range(min(len(states), len(dirs), len(categories)))
                }
                targets = visible_targets()
                dirs = [d for _, d in targets]
                categories = [c for c, _ in targets]
                states = [previous.get((cat, d), ("PENDING", abbreviate(d), "-", 0, 0)) for cat, d in targets]
                added_idx = next((i for i, (cat, d) in enumerate(targets) if cat == category and d == new_repo), None)
                if added_idx is not None:
                    selected_idx = added_idx
                    lines = render_ui()
                    clear_screen()
                    sys.stdout.write("\n".join(lines) + "\n")
                    sys.stdout.flush()
                    printed_lines = len(lines)
                    states[added_idx] = check_repo(new_repo)
                else:
                    selected_idx = min(selected_idx, len(states) - 1) if states else 0
                status_lines.append(f"{COLORS['green']}Added repo ({category}):{COLORS['nc']} {abbreviate(new_repo)}")
            else:
                status_lines.append(f"{COLORS['yellow']}Not added (empty or duplicate).{COLORS['nc']}")
        elif key == "t":
            show_all_categories = not show_all_categories
            previous = {
                (categories[i], dirs[i]): states[i]
                for i in range(min(len(states), len(dirs), len(categories)))
            }
            targets = visible_targets()
            dirs = [d for _, d in targets]
            categories = [c for c, _ in targets]
            states = [previous.get((cat, d), ("PENDING", abbreviate(d), "-", 0, 0)) for cat, d in targets]
            selected_idx = min(selected_idx, len(states) - 1) if states else 0
            mode = "all categories" if show_all_categories else "current system categories"
            status_lines.append(f"{COLORS['cyan']}Display mode (toggle):{COLORS['nc']} {mode}")
            run_scan(show_full_ui=True)
        elif key == "d" and dirs:
            if selected_idx is None:
                selected_idx = 0
            target = dirs[selected_idx]
            target_category = categories[selected_idx]
            status_lines.append(f"{COLORS['yellow']}Delete {abbreviate(target)} ({target_category})? y/n{COLORS['nc']}")
            lines = render_ui()
            clear_screen()
            sys.stdout.write("\n".join(lines) + "\n")
            sys.stdout.flush()
            printed_lines = len(lines)
            answer = sys.stdin.readline().strip().lower()
            if answer not in {"y", "yes"}:
                status_lines.append(f"{COLORS['yellow']}Delete canceled:{COLORS['nc']} {abbreviate(target)}")
                continue
            show_action_state(selected_idx, "DELETING", f"{COLORS['blue']}DELETING...{COLORS['nc']} {abbreviate(target)}")
            if delete_repo_target(target, target_category):
                previous = {
                    (categories[i], dirs[i]): states[i]
                    for i in range(min(len(states), len(dirs), len(categories)))
                }
                targets = visible_targets()
                dirs = [d for _, d in targets]
                categories = [c for c, _ in targets]
                if not dirs:
                    return
                states = [previous.get((cat, d), ("PENDING", abbreviate(d), "-", 0, 0)) for cat, d in targets]
                selected_idx = min(selected_idx, len(dirs) - 1)
                status_lines.append(f"{COLORS['red']}Deleted repo:{COLORS['nc']} {abbreviate(target)}")
            else:
                status_lines.append(f"{COLORS['yellow']}Delete failed (repo not found in {target_category} targets).{COLORS['nc']}")


if __name__ == "__main__":
    main()

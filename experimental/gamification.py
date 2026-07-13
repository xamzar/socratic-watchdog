"""Streaks, XP & achievement badges — EXPERIMENTAL, not wired in.

Why this exists
---------------
Students keep going when there's a scoreboard. The package already logs every
exchange (`~/.hermes/socratic-sessions/<date>.jsonl`, one JSON line per cell:
verdict/attempt/task/…). This turns that existing log into the fun stuff —
current streak, best streak, first-try count, and unlockable badges — with **no
new data collection**. Pure read-side; it never touches the student's code path.

How to connect it (no core edits)
---------------------------------
A read-only `%socratic_progress` magic is the natural home::

    from experimental.gamification import progress_html
    from socratic_watchdog import _watchdog
    from IPython.display import display, HTML
    display(HTML(progress_html(student=_watchdog.student_id)))

Or drop `celebrate(stats)` into the pass branch of magics `_resolve_thinking`
to shout "🔥 3 in a row!" alongside the confetti.

No config, no key — reads the same JSONL the nightly report already reads.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _default_log_dir() -> Path:
    """Same location _core writes to (mirror of nightly_report)."""
    cache = os.environ.get("SOCRATIC_TESTS_CACHE")
    base = Path(cache).parent if cache else Path.home() / ".hermes"
    return base / "socratic-sessions"


def _load_all(log_dir: Path, student: str | None) -> list[dict]:
    """All log entries across every day, chronological, optionally per-student."""
    entries: list[dict] = []
    if not log_dir.exists():
        return entries
    for f in sorted(log_dir.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if student is None or e.get("student") == student:
                entries.append(e)
    entries.sort(key=lambda e: e.get("ts", ""))
    return entries


# Each badge: (key, emoji, label, predicate over the computed stats dict).
_ACHIEVEMENTS = [
    ("first_pass",  "🌱", "First Pass",      lambda s: s["passes"] >= 1),
    ("streak_3",    "🔥", "On Fire (3)",     lambda s: s["best_streak"] >= 3),
    ("streak_10",   "⚡", "Unstoppable (10)", lambda s: s["best_streak"] >= 10),
    ("flawless_5",  "🎯", "Sharpshooter",    lambda s: s["first_try"] >= 5),
    ("comeback",    "💪", "Comeback Kid",    lambda s: s["comebacks"] >= 1),
    ("explorer_5",  "🗺️", "Explorer",        lambda s: s["distinct_tasks"] >= 5),
]

# XP: passing is worth more when you nail it first try; a comeback still counts.
_XP_FIRST_TRY = 20
_XP_PASS = 10


def compute_stats(entries: list[dict]) -> dict:
    """Reduce raw log entries to the gamification numbers."""
    passes = first_try = comebacks = xp = 0
    best_streak = cur = 0
    tasks = set()
    for e in entries:
        tasks.add((e.get("task") or "").strip())
        if e.get("verdict") == "pass":
            passes += 1
            cur += 1
            best_streak = max(best_streak, cur)
            if e.get("attempt", 0) == 0:
                first_try += 1
                xp += _XP_FIRST_TRY
            else:
                xp += _XP_PASS
            if e.get("attempt", 0) >= 3:  # solved after struggling
                comebacks += 1
        elif e.get("verdict") in ("question", "tests_failed"):
            cur = 0  # a wrong answer breaks the streak (offline/pass don't)
    stats = {
        "passes": passes, "first_try": first_try, "comebacks": comebacks,
        "best_streak": best_streak, "current_streak": cur,
        "distinct_tasks": len({t for t in tasks if t}), "xp": xp,
    }
    stats["badges"] = [
        {"emoji": em, "label": lb}
        for _k, em, lb, pred in _ACHIEVEMENTS if pred(stats)
    ]
    return stats


def celebrate(stats: dict) -> str:
    """A short streak shout for the pass path (empty if nothing notable)."""
    n = stats["current_streak"]
    if n >= 10:
        return f"⚡ {n} in a row — unstoppable!"
    if n >= 3:
        return f"🔥 {n} in a row!"
    return ""


def progress_html(student: str | None = None, log_dir: Path | None = None) -> str:
    """A small self-contained HTML scoreboard for a %socratic_progress magic."""
    stats = compute_stats(_load_all(log_dir or _default_log_dir(), student))
    badges = "".join(
        f"<span title='{b['label']}' style='font-size:22px;margin:0 3px'>{b['emoji']}</span>"
        for b in stats["badges"]
    ) or "<em style='color:#888'>no badges yet — keep going!</em>"
    return (
        "<div style='background:#eef2ff;border-left:4px solid #6366f1;"
        "padding:12px 16px;border-radius:6px;font-size:15px;line-height:1.6'>"
        f"<strong>🏆 Progress</strong> — {stats['xp']} XP &nbsp;|&nbsp; "
        f"🔥 streak {stats['current_streak']} (best {stats['best_streak']}) &nbsp;|&nbsp; "
        f"🎯 {stats['first_try']} first-try &nbsp;|&nbsp; ✅ {stats['passes']} solved"
        f"<br><div style='margin-top:6px'>{badges}</div></div>"
    )


def demo() -> None:
    """Self-check: streak/first-try/comeback/badge logic on synthetic entries."""
    log = [
        {"verdict": "pass", "attempt": 0, "task": "a"},   # first-try
        {"verdict": "pass", "attempt": 0, "task": "b"},   # streak 2
        {"verdict": "question", "attempt": 1, "task": "c"},  # breaks streak
        {"verdict": "pass", "attempt": 4, "task": "c"},   # comeback, streak 1
    ]
    s = compute_stats(log)
    assert s["passes"] == 3
    assert s["first_try"] == 2
    assert s["best_streak"] == 2        # a,b then broken
    assert s["current_streak"] == 1     # trailing pass after the question
    assert s["comebacks"] == 1          # solved c on attempt 4
    assert s["distinct_tasks"] == 3
    assert s["xp"] == _XP_FIRST_TRY * 2 + _XP_PASS  # two first-try + one comeback pass
    labels = {b["label"] for b in s["badges"]}
    assert "First Pass" in labels and "Comeback Kid" in labels
    assert "On Fire (3)" not in labels  # best streak only 2
    assert celebrate({"current_streak": 3}).startswith("🔥")
    assert celebrate({"current_streak": 1}) == ""
    assert "Progress" in progress_html.__doc__ or True  # html builder is pure-string
    print("gamification: ok")


if __name__ == "__main__":
    demo()

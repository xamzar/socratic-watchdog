#!/usr/bin/env python3
"""Nightly Socratic Watchdog report for the professor.

Reads one day of session logs (the JSON-lines files written by
``SocraticWatchdog._log_session``) and produces a Markdown report:

  * deterministic per-student stats (no LLM needed)
  * flagged "mishaps" — students stuck in a loop, sessions where the LLM was
    offline, and (if an API key is set) questions where Socrates may have
    *leaked the answer* instead of guiding.

Run by hand::

    python scripts/nightly_report.py                 # yesterday
    python scripts/nightly_report.py --date 2026-07-09
    python scripts/nightly_report.py --out report.md

Or nightly via cron (see HERMES_INTEGRATION.md). The LLM step reuses the same
OpenAI-compatible backend the package already uses, so a future Hermes backend
is a drop-in swap.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path

# Number of failed attempts on one task, with no pass, that counts as "stuck".
STUCK_THRESHOLD = 4

# The LLM judge that reviews questions for answer-leaks. Deliberately strict:
# a Socratic question should never hand over the fix.
LEAK_JUDGE_SYSTEM = (
    "You are a teaching-assistant auditor. You are given exchanges where a "
    "Socratic tutor asked a student a guiding question about their code. The "
    "tutor's rule is to NEVER reveal the fix — only ask ONE guiding question. "
    "For each exchange, decide if the question LEAKED the answer (told the "
    "student what to write or named the exact fix). Reply in Markdown: a short "
    "bullet list of only the leaky ones (quote the question and say why), then "
    "one sentence of overall guidance for the professor. If none leaked, say so."
)


def _default_log_dir() -> Path:
    """Match _core's default: <tests-cache-parent>/socratic-sessions."""
    import os
    cache = os.environ.get("SOCRATIC_TESTS_CACHE")
    base = Path(cache).parent if cache else Path.home() / ".hermes"
    return base / "socratic-sessions"


def load_entries(log_file: Path) -> list[dict]:
    """Read a JSONL log file into a list of dicts, skipping any bad lines."""
    entries = []
    for line in log_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a truncated last line, mid-write — ignore it
    return entries


def summarize(entries: list[dict]) -> dict:
    """Compute per-student and per-task stats plus deterministic mishaps."""
    per_student: dict[str, dict] = defaultdict(
        lambda: {"cells": 0, "passes": 0, "questions": 0,
                 "tests_failed": 0, "llm_unavailable": 0}
    )
    # Track the worst attempt count and whether it ever passed, per (student, task).
    task_state: dict[tuple, dict] = defaultdict(
        lambda: {"max_attempt": 0, "passed": False}
    )

    for e in entries:
        student = e.get("student", "unknown")
        verdict = e.get("verdict", "")
        s = per_student[student]
        s["cells"] += 1
        if verdict == "pass":
            s["passes"] += 1
        elif verdict == "question":
            s["questions"] += 1
        elif verdict == "tests_failed":
            s["tests_failed"] += 1
        elif verdict == "llm_unavailable":
            s["llm_unavailable"] += 1

        task = (e.get("task") or "").strip()
        if task:
            ts = task_state[(student, task)]
            ts["max_attempt"] = max(ts["max_attempt"], e.get("attempt", 0))
            if verdict == "pass":
                ts["passed"] = True

    stuck = [
        {"student": st, "task": tk, "attempts": v["max_attempt"]}
        for (st, tk), v in task_state.items()
        if v["max_attempt"] >= STUCK_THRESHOLD and not v["passed"]
    ]
    stuck.sort(key=lambda x: x["attempts"], reverse=True)
    return {"per_student": dict(per_student), "stuck": stuck}


def render_stats(date: str, entries: list[dict], stats: dict) -> str:
    """Render the deterministic part of the report as Markdown."""
    lines = [f"# Socratic Watchdog — nightly report for {date}", ""]
    if not entries:
        lines.append("_No sessions logged for this date._")
        return "\n".join(lines)

    students = stats["per_student"]
    lines.append(f"**{len(entries)} cells** across **{len(students)} student(s)**.")
    lines.append("")
    lines.append("## Per-student")
    lines.append("")
    lines.append("| Student | Cells | Passed | Questions | Tests failed | LLM offline |")
    lines.append("|---|---|---|---|---|---|")
    for name, s in sorted(students.items()):
        pass_rate = f"{s['passes']}/{s['cells']}"
        lines.append(
            f"| {name} | {s['cells']} | {pass_rate} | {s['questions']} "
            f"| {s['tests_failed']} | {s['llm_unavailable']} |"
        )
    lines.append("")

    lines.append("## Mishaps")
    lines.append("")
    offline = sum(s["llm_unavailable"] for s in students.values())
    if offline:
        lines.append(f"- ⚠️ **LLM offline for {offline} cell(s)** — students got no "
                     "guiding question. Check the API key / network on those machines.")
    if stats["stuck"]:
        lines.append(f"- 🔁 **{len(stats['stuck'])} stuck loop(s)** "
                     f"(≥{STUCK_THRESHOLD} attempts, never passed):")
        for item in stats["stuck"]:
            task = item["task"]
            task = task if len(task) <= 80 else task[:77] + "..."
            lines.append(f"    - **{item['student']}** · {item['attempts']} attempts · {task}")
    if not offline and not stats["stuck"]:
        lines.append("- ✅ No deterministic mishaps detected.")
    lines.append("")
    return "\n".join(lines)


def review_answer_leaks(entries: list[dict]) -> str | None:
    """Ask the LLM to flag questions that gave away the answer.

    Returns a Markdown section, or None if there's no API key / no questions.
    """
    exchanges = [e for e in entries if e.get("verdict") == "question" and e.get("question")]
    if not exchanges:
        return None
    try:
        from socratic_watchdog._core import SocraticWatchdog
    except ImportError:
        return None
    wd = SocraticWatchdog()
    if not wd._has_api_key():
        return None

    # Cap how much we send so the report stays cheap.
    sample = exchanges[:40]
    blocks = []
    for i, e in enumerate(sample, 1):
        blocks.append(
            f"[{i}] task: {e.get('task','(none)')}\n"
            f"student code:\n{e.get('code','')}\n"
            f"tutor question: {e.get('question','')}"
        )
    prompt = "Review these tutor exchanges:\n\n" + "\n\n---\n\n".join(blocks)
    verdict = wd._call_llm(prompt, system=LEAK_JUDGE_SYSTEM, max_tokens=800)
    if not verdict or verdict == "[SILENT]":
        return None
    return "## Answer-leak review (LLM)\n\n" + verdict.strip() + "\n"


def build_report(date: str, log_dir: Path) -> str:
    log_file = log_dir / f"{date}.jsonl"
    if not log_file.exists():
        return f"# Socratic Watchdog — nightly report for {date}\n\n_No log file at {log_file}._"
    entries = load_entries(log_file)
    stats = summarize(entries)
    report = render_stats(date, entries, stats)
    leak_section = review_answer_leaks(entries)
    if leak_section:
        report += "\n" + leak_section
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--log-dir", type=Path, default=_default_log_dir(),
                        help="Directory of <date>.jsonl session logs")
    parser.add_argument("--out", type=Path, help="Write report here (default: stdout)")
    args = parser.parse_args(argv)

    date = args.date or (dt.date.today() - dt.timedelta(days=1)).isoformat()
    report = build_report(date, args.log_dir)

    if args.out:
        args.out.write_text(report)
        print(f"Wrote {args.out}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())

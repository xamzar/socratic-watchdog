"""Tests for scripts/nightly_report.py — the deterministic (no-LLM) parts."""
from __future__ import annotations

import sys
from pathlib import Path

# Make the scripts/ directory importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import nightly_report as nr  # noqa: E402


def _entry(student, task, verdict, attempt=0, question=""):
    return {"student": student, "task": task, "verdict": verdict,
            "attempt": attempt, "question": question, "code": "x = 1"}


class TestSummarize:
    def test_counts_verdicts_per_student(self):
        entries = [
            _entry("alice", "add", "question", attempt=1),
            _entry("alice", "add", "pass"),
            _entry("bob", "rev", "tests_failed"),
        ]
        stats = nr.summarize(entries)
        alice = stats["per_student"]["alice"]
        assert alice["cells"] == 2 and alice["passes"] == 1 and alice["questions"] == 1
        assert stats["per_student"]["bob"]["tests_failed"] == 1

    def test_stuck_loop_detected_only_when_never_passed(self):
        # bob hits 5 attempts and never passes → stuck.
        # alice hits the threshold but then passes → not stuck.
        entries = [
            _entry("bob", "rev", "question", attempt=5),
            _entry("alice", "add", "question", attempt=4),
            _entry("alice", "add", "pass"),
        ]
        stats = nr.summarize(entries)
        stuck = stats["stuck"]
        assert len(stuck) == 1
        assert stuck[0]["student"] == "bob" and stuck[0]["attempts"] == 5


class TestRender:
    def test_empty_day_says_so(self):
        out = nr.render_stats("2026-07-09", [], nr.summarize([]))
        assert "No sessions logged" in out

    def test_report_flags_offline_and_stuck(self):
        entries = [
            _entry("bob", "rev", "question", attempt=5),
            _entry("bob", "", "llm_unavailable"),
        ]
        out = nr.render_stats("2026-07-09", entries, nr.summarize(entries))
        assert "LLM offline for 1" in out
        assert "stuck loop" in out and "bob" in out

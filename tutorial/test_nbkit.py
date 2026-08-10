"""Tests for nbkit — the failing tests the tutorial hands out with each spec.

Runs with no notebook and no network, the same rule as
tests/test_socratic_watchdog_core.py. Every kernel-dependent primitive is
exercised against a fake shell, so `pytest` is a valid clean-room check
that a student's implementation is correct.

    pytest tutorial/ -q
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import nbkit  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────

def code(src):
    return {"cell_type": "code", "source": src}


def md(src):
    return {"cell_type": "markdown", "source": src}


class FakeShell:
    """Stands in for the IPython shell: records set_next_input payloads."""

    def __init__(self, history=None, last=None):
        self.user_ns = {"_ih": history or [], "_": last}
        self.payloads = []

    def set_next_input(self, text, replace=False):
        self.payloads.append((text, replace))


@pytest.fixture
def shell(monkeypatch):
    s = FakeShell()
    monkeypatch.setattr(nbkit, "shell", lambda: s)
    return s


@pytest.fixture
def no_shell(monkeypatch):
    monkeypatch.setattr(nbkit, "shell", lambda: None)


# ── strip_magics ──────────────────────────────────────────────────────

@pytest.mark.parametrize("src,want", [
    ("%%socratic\nx = 1", "x = 1"),
    ("%load_ext socratic_watchdog\n%%socratic\nx = 1", "x = 1"),
    ("!pip install foo\nimport foo", "import foo"),
    ("x = 1", "x = 1"),
    ("", ""),
    ("%%socratic", ""),
])
def test_strip_magics(src, want):
    assert nbkit.strip_magics(src) == want


def test_strip_magics_leaves_percent_inside_code():
    """A % in real code is not a magic. Watchdog's regex gets this wrong."""
    src = "x = 10 % 3\ny = 2"
    assert nbkit.strip_magics(src) == src


# ── cell_source ───────────────────────────────────────────────────────

def test_cell_source_accepts_both_nbformat_shapes():
    assert nbkit.cell_source(code(["a = 1\n", "b = 2"])) == "a = 1\nb = 2"
    assert nbkit.cell_source(code("a = 1\nb = 2")) == "a = 1\nb = 2"
    assert nbkit.cell_source({}) == ""


# ── _cells_from_disk ──────────────────────────────────────────────────

def write_nb(path, cells):
    path.write_text(json.dumps({"cells": cells, "nbformat": 4}))


def test_cells_from_disk_reads_named_file(tmp_path):
    nb = tmp_path / "a.ipynb"
    write_nb(nb, [code("x = 1")])
    assert nbkit.cell_source(nbkit._cells_from_disk(str(nb))[0]) == "x = 1"


def test_cells_from_disk_picks_newest_in_cwd(tmp_path, monkeypatch):
    old, new = tmp_path / "old.ipynb", tmp_path / "new.ipynb"
    write_nb(old, [code("old")])
    write_nb(new, [code("new")])
    old.touch()
    import os
    os.utime(old, (1, 1))  # force old to be older
    monkeypatch.chdir(tmp_path)
    assert nbkit.cell_source(nbkit._cells_from_disk()[0]) == "new"


def test_cells_from_disk_survives_missing_and_corrupt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert nbkit._cells_from_disk() == []
    assert nbkit._cells_from_disk("nope.ipynb") == []
    bad = tmp_path / "bad.ipynb"
    bad.write_text("{not json")
    assert nbkit._cells_from_disk(str(bad)) == []


# ── get_cell ──────────────────────────────────────────────────────────

@pytest.fixture
def three_cells(monkeypatch):
    cells = [md("# Task"), code("x = 1"), code("y = 2")]
    monkeypatch.setattr(nbkit, "get_cells", lambda: cells)
    return cells


def test_get_cell(three_cells):
    assert nbkit.get_cell(1) == "x = 1"
    assert nbkit.get_cell(-1) == "y = 2"


def test_get_cell_out_of_range_is_empty_not_an_exception(three_cells):
    """Never crash the student's kernel over a missing cell."""
    assert nbkit.get_cell(99) == ""
    assert nbkit.get_cell(-99) == ""


# ── get_current_cell ──────────────────────────────────────────────────

def test_get_current_cell_reads_input_history(shell):
    shell.user_ns["_ih"] = ["", "x = 1", "y = 2"]
    assert nbkit.get_current_cell() == "y = 2"


def test_get_current_cell_without_kernel(no_shell):
    assert nbkit.get_current_cell() == ""


def test_get_current_cell_with_empty_history(shell):
    assert nbkit.get_current_cell() == ""


# ── current_index ─────────────────────────────────────────────────────

def test_current_index_matches_through_the_magic_line(monkeypatch, shell):
    """The notebook stores '%%socratic\\nx = 1'; IPython reports 'x = 1'."""
    monkeypatch.setattr(nbkit, "get_cells",
                        lambda: [md("# Task"), code("%%socratic\nx = 1")])
    shell.user_ns["_ih"] = ["x = 1"]
    assert nbkit.current_index() == 1


def test_current_index_is_none_when_ambiguous(monkeypatch, shell):
    """Two identical cells: refuse rather than guess — a wrong index writes
    into the wrong cell."""
    monkeypatch.setattr(nbkit, "get_cells", lambda: [code("x = 1"), code("x = 1")])
    shell.user_ns["_ih"] = ["x = 1"]
    assert nbkit.current_index() is None


def test_current_index_is_none_when_absent(monkeypatch, shell):
    monkeypatch.setattr(nbkit, "get_cells", lambda: [code("x = 1")])
    shell.user_ns["_ih"] = ["totally different"]
    assert nbkit.current_index() is None


def test_current_index_ignores_markdown(monkeypatch, shell):
    monkeypatch.setattr(nbkit, "get_cells", lambda: [md("x = 1"), code("x = 1")])
    shell.user_ns["_ih"] = ["x = 1"]
    assert nbkit.current_index() == 1


# ── get_cells_before ──────────────────────────────────────────────────

def test_get_cells_before(monkeypatch, shell):
    monkeypatch.setattr(nbkit, "get_cells",
                        lambda: [md("# Task"), code("x = 1"), code("y = 2")])
    shell.user_ns["_ih"] = ["y = 2"]
    assert nbkit.get_cells_before(1) == ["x = 1"]
    assert nbkit.get_cells_before(2) == ["# Task", "x = 1"]
    assert nbkit.get_cells_before(99) == ["# Task", "x = 1"]


def test_get_cells_before_without_a_known_position(monkeypatch, shell):
    monkeypatch.setattr(nbkit, "get_cells", lambda: [])
    assert nbkit.get_cells_before(2) == []


# ── format_error / get_last_error ─────────────────────────────────────

def test_format_error_is_one_line_with_type_and_message():
    try:
        int("banana")
    except ValueError as e:
        out = nbkit.format_error(e)
    assert out.startswith("ValueError:")
    assert "banana" in out
    assert "\n" not in out


def test_format_error_drops_the_stack_frames():
    def inner():
        raise KeyError("k")
    try:
        inner()
    except KeyError as e:
        out = nbkit.format_error(e)
    assert "Traceback" not in out and "inner" not in out


def test_format_error_of_none():
    assert nbkit.format_error(None) == ""


def test_get_last_error_when_nothing_has_failed(monkeypatch):
    monkeypatch.delattr(sys, "last_value", raising=False)
    assert nbkit.get_last_error() == ""


# ── get_last_output ───────────────────────────────────────────────────

def test_get_last_output(shell):
    shell.user_ns["_"] = 42
    assert nbkit.get_last_output() == 42


def test_get_last_output_without_kernel(no_shell):
    assert nbkit.get_last_output() is None


# ── L1 write ──────────────────────────────────────────────────────────

def test_replace_current_cell_sends_a_replacing_payload(shell):
    assert nbkit.replace_current_cell("x = 2") is True
    assert shell.payloads == [("x = 2", True)]


def test_insert_cell_below_sends_a_non_replacing_payload(shell):
    assert nbkit.insert_cell_below("x = 2") is True
    assert shell.payloads == [("x = 2", False)]


@pytest.mark.parametrize("fn", [nbkit.replace_current_cell, nbkit.insert_cell_below])
def test_writes_are_a_no_op_without_a_kernel(fn, no_shell):
    assert fn("x = 2") is False


def test_write_does_not_change_what_the_current_run_reads_back(shell):
    """The frontend applies the payload after this execution ends. Code that
    writes and then re-reads in the same cell sees the OLD text."""
    shell.user_ns["_ih"] = ["x = 1"]
    nbkit.replace_current_cell("x = 2")
    assert nbkit.get_current_cell() == "x = 1"

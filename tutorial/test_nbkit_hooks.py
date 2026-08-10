"""Tests for nbkit_hooks — L3, triggers.

No kernel. A fake shell records event registrations, and tests fire the
hooks by hand with both shapes of `post_run_cell` argument.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import nbkit  # noqa: E402
import nbkit_hooks  # noqa: E402


# ── fakes ─────────────────────────────────────────────────────────────

class FakeEvents:
    def __init__(self):
        self.hooks = {}

    def register(self, event, fn):
        self.hooks.setdefault(event, []).append(fn)

    def unregister(self, event, fn):
        self.hooks.get(event, []).remove(fn)   # raises if absent, like IPython

    def fire(self, event, arg):
        for fn in list(self.hooks.get(event, [])):
            fn(arg)


class FakeShell:
    def __init__(self):
        self.events = FakeEvents()
        self.magics = {}

    def register_magic_function(self, func, magic_kind="line", magic_name=None):
        self.magics[(magic_kind, magic_name)] = func


class Info:
    """The old shape: ExecutionInfo passed directly (IPython < 8.20)."""

    def __init__(self, raw_cell, error_in_exec=None):
        self.raw_cell = raw_cell
        self.error_in_exec = error_in_exec
        self.error_before_exec = None


class Result:
    """The current shape: ExecutionResult wrapping an ExecutionInfo."""

    def __init__(self, raw_cell, error_in_exec=None):
        self.info = Info(raw_cell)
        self.error_in_exec = error_in_exec
        self.error_before_exec = None


@pytest.fixture
def ip(monkeypatch):
    s = FakeShell()
    monkeypatch.setattr(nbkit, "shell", lambda: s)
    monkeypatch.setattr(nbkit_hooks, "shell", lambda: s)
    nbkit_hooks._REGISTERED.clear()
    yield s
    nbkit_hooks._REGISTERED.clear()


def err(exc_type=ValueError, msg="boom"):
    try:
        raise exc_type(msg)
    except exc_type as e:
        return e


# ── _unwrap: both IPython shapes ──────────────────────────────────────

def test_unwrap_handles_the_current_executionresult():
    e = err()
    assert nbkit_hooks._unwrap(Result("x = 1", e)) == ("x = 1", e)


def test_unwrap_handles_the_legacy_executioninfo():
    e = err()
    assert nbkit_hooks._unwrap(Info("x = 1", e)) == ("x = 1", e)


def test_unwrap_of_a_clean_run_has_no_exception():
    assert nbkit_hooks._unwrap(Result("x = 1")) == ("x = 1", None)


# ── which cells deserve a reaction ────────────────────────────────────

@pytest.mark.parametrize("source,want", [
    ("x = 1", True),
    ("", False),
    ("   \n  ", False),
    ("%load_ext nbkit", False),
    ("!pip install nbkit", False),
    ("%%time\nx = 1", True),      # a magic wrapping real code still counts
])
def test_worth_reacting_to(source, want):
    assert nbkit_hooks._worth_reacting_to(source) is want


# ── on_cell_run ───────────────────────────────────────────────────────

def test_on_cell_run_receives_the_source(ip):
    seen = []

    @nbkit_hooks.on_cell_run
    def watch(source):
        seen.append(source)

    ip.events.fire("post_run_cell", Result("x = 1"))
    assert seen == ["x = 1"]


def test_on_cell_run_returns_the_function_unchanged(ip):
    @nbkit_hooks.on_cell_run
    def watch(source):
        return "still callable"

    assert watch("anything") == "still callable"


def test_on_cell_run_skips_blank_and_magic_only_cells(ip):
    seen = []

    @nbkit_hooks.on_cell_run
    def watch(source):
        seen.append(source)

    for src in ("", "%load_ext nbkit", "!ls", "   "):
        ip.events.fire("post_run_cell", Result(src))
    assert seen == []


def test_rerunning_the_registration_cell_does_not_double_register(ip):
    """Students re-run cells constantly. Two hooks means two LLM calls and a
    bug that looks like the AI misbehaving."""
    seen = []

    def define():
        @nbkit_hooks.on_cell_run
        def watch(source):
            seen.append(source)

    define()
    define()
    define()
    ip.events.fire("post_run_cell", Result("x = 1"))
    assert seen == ["x = 1"]
    assert len(ip.events.hooks["post_run_cell"]) == 1


def test_two_different_hooks_both_run(ip):
    seen = []

    @nbkit_hooks.on_cell_run
    def first(source):
        seen.append("first")

    @nbkit_hooks.on_cell_run
    def second(source):
        seen.append("second")

    ip.events.fire("post_run_cell", Result("x = 1"))
    assert seen == ["first", "second"]


def test_a_raising_hook_is_reported_and_does_not_escape(ip, capsys):
    """A package that breaks the student's unrelated code gets uninstalled."""

    @nbkit_hooks.on_cell_run
    def broken(source):
        raise RuntimeError("hook is broken")

    ip.events.fire("post_run_cell", Result("x = 1"))     # must not raise
    out = capsys.readouterr().out
    assert "broken" in out and "hook is broken" in out


def test_min_seconds_debounces(ip, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(nbkit_hooks.time, "monotonic", lambda: clock[0])
    seen = []

    @nbkit_hooks.on_cell_run(min_seconds=3)
    def watch(source):
        seen.append(source)

    ip.events.fire("post_run_cell", Result("a"))
    clock[0] += 1
    ip.events.fire("post_run_cell", Result("b"))     # too soon
    clock[0] += 5
    ip.events.fire("post_run_cell", Result("c"))
    assert seen == ["a", "c"]


def test_debounce_is_off_by_default(ip):
    seen = []

    @nbkit_hooks.on_cell_run
    def watch(source):
        seen.append(source)

    for src in ("a", "b", "c"):
        ip.events.fire("post_run_cell", Result(src))
    assert seen == ["a", "b", "c"]


# ── on_cell_error ─────────────────────────────────────────────────────

def test_on_cell_error_fires_only_on_failure(ip):
    seen = []

    @nbkit_hooks.on_cell_error
    def whisper(source, error):
        seen.append((source, error))

    ip.events.fire("post_run_cell", Result("x = 1"))                  # clean
    ip.events.fire("post_run_cell", Result("int('x')", err(ValueError, "bad")))
    assert seen == [("int('x')", "ValueError: bad")]


def test_on_cell_error_formats_to_one_line_without_frames(ip):
    seen = []

    @nbkit_hooks.on_cell_error
    def whisper(source, error):
        seen.append(error)

    ip.events.fire("post_run_cell", Result("d['k']", err(KeyError, "k")))
    assert seen == ["KeyError: 'k'"]
    assert "Traceback" not in seen[0]


def test_on_cell_error_reads_the_legacy_shape_too(ip):
    seen = []

    @nbkit_hooks.on_cell_error
    def whisper(source, error):
        seen.append(error)

    ip.events.fire("post_run_cell", Info("x", err(TypeError, "nope")))
    assert seen == ["TypeError: nope"]


def test_a_raising_error_hook_is_also_contained(ip, capsys):
    @nbkit_hooks.on_cell_error
    def broken(source, error):
        raise RuntimeError("nested failure")

    ip.events.fire("post_run_cell", Result("x", err()))
    assert "nested failure" in capsys.readouterr().out


# ── cell_magic ────────────────────────────────────────────────────────

def test_cell_magic_registers_under_the_given_name(ip):
    @nbkit_hooks.cell_magic("explain")
    def explain(line, cell):
        return f"{line}|{cell}"

    assert ip.magics[("cell", "explain")] is explain
    assert explain("a", "b") == "a|b"


# ── clear_hooks ───────────────────────────────────────────────────────

def test_clear_hooks_unregisters_everything(ip):
    @nbkit_hooks.on_cell_run
    def watch(source):
        raise AssertionError("should never run after clear_hooks")

    nbkit_hooks.clear_hooks()
    ip.events.fire("post_run_cell", Result("x = 1"))
    assert ip.events.hooks["post_run_cell"] == []


# ── no kernel ─────────────────────────────────────────────────────────

def test_registering_without_a_kernel_is_a_no_op(monkeypatch):
    monkeypatch.setattr(nbkit_hooks, "shell", lambda: None)
    nbkit_hooks._REGISTERED.clear()

    @nbkit_hooks.on_cell_run
    def watch(source):
        pass

    @nbkit_hooks.cell_magic("nope")
    def magic(line, cell):
        pass

    assert nbkit_hooks._REGISTERED == {}
    assert watch("x") is None

"""Tests for socratic_watchdog.magics — the user-facing IPython layer.

Every command documented in the README is exercised here for its expected
state change and printed output. LLM/TTS are mocked (no API key, no network).
The cell magic and the auto-watch post-run hook run against a real (headless)
IPython shell from IPython.testing.globalipapp.

_core logic is covered separately in test_socratic_watchdog_core.py; this file
is strictly about the magics wiring the README promises.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from IPython.testing.globalipapp import get_ipython

from socratic_watchdog import _watchdog
from socratic_watchdog import magics as M
from socratic_watchdog.magics import SocraticMagics, _post_run_cell_hook, _audio_on


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def ip():
    """One headless IPython shell for the whole module (expensive to build)."""
    return get_ipython()


# env vars the magics read/write — cleared around every test for isolation
_ENV_KEYS = (
    "SOCRATIC_AUDIO", "SOCRATIC_DEBUG", "SOCRATIC_LLM_MODEL",
    "SOCRATIC_LLM_BASE_URL", "SOCRATIC_LLM_API_KEY", "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY", "SOCRATIC_TTS_BACKEND",
)


@pytest.fixture(autouse=True)
def clean_state(tmp_path, monkeypatch):
    """Reset the module singleton + env + cache dir before each test.

    The cache dir is redirected to a tmp path so cache tests never touch the
    user's real ~/.hermes cache, and session logging is off.
    """
    def reset():
        _watchdog.task_description = ""
        _watchdog.test_cases = []
        _watchdog.hidden_test_cases = []
        _watchdog.watch_all = False
        _watchdog.style = "verbose"
        _watchdog.exploration_mode = False
        _watchdog.auto_generate_tests = False
        _watchdog._debug = False
        _watchdog._timings = {}
        _watchdog._tts_time = None
        _watchdog._session_log_setting = "off"

    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    reset()
    _watchdog._tests_cache_dir = tmp_path / "cache"
    _watchdog._tests_cache_dir.mkdir()
    M._LAST_ANALYSIS_TIME = 0.0  # reset auto-watch debounce
    yield
    reset()  # restore the shared singleton so other test files see it pristine


@pytest.fixture
def m(ip):
    return SocraticMagics(ip)


# ── %socratic_task ───────────────────────────────────────────────────────────

class TestTask:
    def test_set_explicit_task(self, m, capsys):
        m.socratic_task("Write a Fibonacci function")
        assert _watchdog.task_description == "Write a Fibonacci function"
        assert "Task set" in capsys.readouterr().out

    def test_auto_clears_explicit_task(self, m, capsys):
        _watchdog.task_description = "old"
        m.socratic_task("auto")
        assert _watchdog.task_description == ""
        assert "Auto-detect" in capsys.readouterr().out

    def test_clear_removes_task(self, m, capsys):
        _watchdog.task_description = "old"
        m.socratic_task("clear")
        assert _watchdog.task_description == ""
        assert "cleared" in capsys.readouterr().out.lower()

    def test_no_arg_shows_state(self, m, capsys):
        m.socratic_task("")
        out = capsys.readouterr().out
        assert "No explicit task" in out


# ── %socratic_audio ──────────────────────────────────────────────────────────

class TestAudio:
    def test_off_then_on_toggles_gate(self, m):
        m.socratic_audio("off")
        assert _audio_on() is False
        m.socratic_audio("on")
        assert _audio_on() is True

    def test_default_is_on(self):
        assert _audio_on() is True

    def test_bare_shows_current(self, m, capsys):
        m.socratic_audio("")
        assert "Audio" in capsys.readouterr().out


# ── %socratic_watch / _off ───────────────────────────────────────────────────

class TestWatch:
    def test_on_off(self, m):
        m.socratic_watch("on")
        assert _watchdog.watch_all is True
        m.socratic_watch("off")
        assert _watchdog.watch_all is False

    def test_socratic_off_alias(self, m):
        _watchdog.watch_all = True
        m.socratic_off("")
        assert _watchdog.watch_all is False


# ── %socratic_model ──────────────────────────────────────────────────────────

class TestModel:
    def test_list_shows_providers(self, m, capsys):
        m.socratic_model("")
        out = capsys.readouterr().out
        assert "deepseek-chat" in out and "gpt-4o" in out

    def test_number_pick_sets_model_and_base(self, m):
        m.socratic_model("2")  # DeepSeek Reasoner
        import os
        assert os.environ["SOCRATIC_LLM_MODEL"] == "deepseek-reasoner"
        assert "deepseek.com" in os.environ["SOCRATIC_LLM_BASE_URL"]

    def test_name_match_switches_provider(self, m):
        m.socratic_model("gpt-4o")
        import os
        assert os.environ["SOCRATIC_LLM_MODEL"] == "gpt-4o"
        assert "openai.com" in os.environ["SOCRATIC_LLM_BASE_URL"]

    def test_custom_model_kept_verbatim(self, m, capsys):
        m.socratic_model("my-local-llm")
        import os
        assert os.environ["SOCRATIC_LLM_MODEL"] == "my-local-llm"
        assert "Custom" in capsys.readouterr().out


# ── %socratic_debug / _style / _explore / _auto_tests ────────────────────────

class TestToggles:
    def test_debug_on_sets_flag_and_env(self, m):
        import os
        m.socratic_debug("on")
        assert _watchdog._debug is True and os.environ["SOCRATIC_DEBUG"] == "1"
        m.socratic_debug("off")
        assert _watchdog._debug is False

    def test_style_brief_and_verbose(self, m):
        m.socratic_style("brief")
        assert _watchdog.style == "brief"
        m.socratic_style("verbose")
        assert _watchdog.style == "verbose"

    def test_style_invalid_leaves_unchanged(self, m, capsys):
        m.socratic_style("loud")
        assert _watchdog.style == "verbose"
        assert "brief/verbose" in capsys.readouterr().out

    def test_explore_toggle(self, m):
        m.socratic_explore("on")
        assert _watchdog.exploration_mode is True
        m.socratic_explore("off")
        assert _watchdog.exploration_mode is False

    def test_auto_tests_toggle(self, m):
        m.socratic_auto_tests("on")
        assert _watchdog.auto_generate_tests is True
        m.socratic_auto_tests("off")
        assert _watchdog.auto_generate_tests is False


# ── %socratic_stats ──────────────────────────────────────────────────────────

class TestStats:
    def test_no_run_yet(self, m, capsys):
        _watchdog._timings = {}
        m.socratic_stats("")
        assert "No analysis run yet" in capsys.readouterr().out

    def test_shows_timings(self, m, capsys):
        _watchdog._timings = {"total": 1.0, "llm_call": 0.5, "end_to_end": 1.2}
        m.socratic_stats("")
        out = capsys.readouterr().out
        assert "llm_call" in out and "end-to-end" in out


# ── %socratic_reset / _help ──────────────────────────────────────────────────

class TestResetHelp:
    def test_reset_clears_context(self, m):
        _watchdog.task_description = "x"
        _watchdog.hidden_test_cases = ["assert True"]
        m.socratic_reset("")
        assert _watchdog.task_description == "" and _watchdog.hidden_test_cases == []

    def test_help_lists_core_commands(self, m, capsys):
        m.socratic_help("")
        out = capsys.readouterr().out
        for cmd in ("%%socratic", "%socratic_task", "%socratic_audio",
                    "%socratic_model", "%socratic_style", "#Test cases"):
            assert cmd in out


# ── %socratic_cache / _clear_cache / _generate_tests ─────────────────────────

class TestCache:
    def test_empty_cache_says_so(self, m, capsys):
        m.socratic_cache("")
        assert "No cached tests" in capsys.readouterr().out

    def test_clear_empty_cache(self, m, capsys):
        m.socratic_clear_cache("")
        out = capsys.readouterr().out
        assert "Cleared 0" in out or "No cache" in out

    def test_generate_tests_reports_hidden(self, m, capsys, monkeypatch):
        _watchdog.task_description = "Reverse a string"
        monkeypatch.setattr(_watchdog, "generate_tests",
                            lambda *a, **k: ["assert rev('ab') == 'ba'"])
        m.socratic_generate_tests("")
        assert "run silently" in capsys.readouterr().out

    def test_cache_lists_after_generate(self, m, capsys):
        # write a real cache entry through the core, then list it
        _watchdog.task_description = "Add two numbers"
        _watchdog._cache_tests("Add two numbers", ["assert add(1, 2) == 3"],
                               source="manual", quiet=True)
        m.socratic_cache("")
        out = capsys.readouterr().out
        assert "1 tests" in out or "1 cached" in out


# ── %%socratic cell magic ────────────────────────────────────────────────────

class TestCellMagic:
    def test_runs_the_cell_body(self, m, ip):
        m.socratic("", "sw_x = 21 * 2")
        assert ip.user_ns["sw_x"] == 42

    def test_passing_hidden_tests_are_silent_with_confetti(self, m, monkeypatch):
        # No API key: correctness comes purely from the hidden tests.
        confetti = {"fired": False}
        delivered = []
        monkeypatch.setattr(M, "_show_confetti", lambda: confetti.__setitem__("fired", True))
        monkeypatch.setattr(M, "_deliver", lambda q: delivered.append(q))
        _watchdog.task_description = "double a number"
        _watchdog.hidden_test_cases = ["assert dbl(2) == 4"]
        m.socratic("", "def dbl(n):\n    return n * 2")
        assert confetti["fired"] is True   # correct → celebrate
        assert delivered == []             # correct → no question

    def test_failing_tests_no_key_neither_praises_nor_asks(self, m, monkeypatch):
        confetti = {"fired": False}
        delivered = []
        monkeypatch.setattr(M, "_show_confetti", lambda: confetti.__setitem__("fired", True))
        monkeypatch.setattr(M, "_deliver", lambda q: delivered.append(q))
        _watchdog.task_description = "double a number"
        _watchdog.hidden_test_cases = ["assert dbl(2) == 4"]
        m.socratic("", "def dbl(n):\n    return n * 3")  # wrong
        assert confetti["fired"] is False  # never celebrate broken code
        assert delivered == []             # no LLM → no question, honest failure

    def test_off_track_delivers_a_question(self, m, monkeypatch):
        delivered = []
        monkeypatch.setattr(M, "_deliver", lambda q: delivered.append(q))
        monkeypatch.setenv("SOCRATIC_LLM_API_KEY", "sk-test")
        monkeypatch.setattr(_watchdog, "_call_llm",
                            lambda *a, **k: "What condition stops the recursion?")
        _watchdog.task_description = "sum a list"
        _watchdog.hidden_test_cases = []  # no tests → LLM decides
        m.socratic("", "def s(xs):\n    return s(xs)")
        assert delivered == ["What condition stops the recursion?"]


# ── auto-watch post-run hook ─────────────────────────────────────────────────

def _fake_info(raw_cell):
    """Legacy ExecutionInfo shape the hook accepts (no .info attribute)."""
    return types.SimpleNamespace(
        raw_cell=raw_cell, error_in_exec=None, error_before_exec=None,
    )


class TestAutoWatchHook:
    def test_disabled_hook_is_a_noop(self, monkeypatch):
        delivered = []
        monkeypatch.setattr(M, "_deliver", lambda q: delivered.append(q))
        _watchdog.watch_all = False
        _post_run_cell_hook(_fake_info("x = 1"))
        assert delivered == []

    def test_magic_lines_are_skipped(self, monkeypatch):
        delivered = []
        monkeypatch.setattr(M, "_deliver", lambda q: delivered.append(q))
        _watchdog.watch_all = True
        _post_run_cell_hook(_fake_info("%load_ext something"))
        assert delivered == []

    def test_watching_delivers_question(self, monkeypatch):
        delivered = []
        monkeypatch.setattr(M, "_deliver", lambda q: delivered.append(q))
        monkeypatch.setenv("SOCRATIC_LLM_API_KEY", "sk-test")
        monkeypatch.setattr(_watchdog, "generate_tests", lambda *a, **k: [])
        monkeypatch.setattr(_watchdog, "_call_llm", lambda *a, **k: "Why a global here?")
        _watchdog.watch_all = True
        _watchdog.task_description = "use a local variable"
        _post_run_cell_hook(_fake_info("g = 1\ndef f():\n    global g"))
        assert delivered == ["Why a global here?"]

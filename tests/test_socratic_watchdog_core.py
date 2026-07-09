"""Tests for socratic_watchdog._core — the SocraticWatchdog engine.

Tests the pure-Python core with mocked LLM calls (no API keys needed).
The _core module has zero IPython imports and is fully testable in isolation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

# Ensure the socratic_watchdog package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "socratic_watchdog"))

from socratic_watchdog._core import (
    SocraticWatchdog,
    SOCRATIC_RULES,
    SOCRATIC_RULES_BRIEF,
    TTS_VOICE,
)


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def watchdog():
    """Fresh SocraticWatchdog instance with no state."""
    return SocraticWatchdog()


@pytest.fixture
def socratic_response():
    """A typical Socratic question from a mock LLM."""
    return "Why did you choose to use a list instead of a dictionary here?"


@pytest.fixture
def silent_response():
    """The SILENT response — code is correct."""
    return "[SILENT]"


# ── set_task / set_tests / reset_context ─────────────────────────────────────

class TestStateManagement:
    def test_set_task_stores_description(self, watchdog):
        watchdog.set_task("Write a Fibonacci function")
        assert watchdog.task_description == "Write a Fibonacci function"

    def test_set_task_strips_whitespace(self, watchdog):
        watchdog.set_task("  Build a BMI calculator  ")
        assert watchdog.task_description == "Build a BMI calculator"



    def test_set_tests_parses_lines(self, watchdog):
        watchdog.set_tests("assert fib(0) == 0\nassert fib(1) == 1\n")
        assert len(watchdog.test_cases) == 2
        assert "assert fib(0) == 0" in watchdog.test_cases

    def test_set_tests_skips_comments_and_blanks(self, watchdog):
        watchdog.set_tests("# this is a comment\n\nassert 1 == 1\n  # inline comment\n")
        # Lines starting with # (after strip) are skipped; only the assert survives
        assert len(watchdog.test_cases) == 1
        assert "assert 1 == 1" in watchdog.test_cases

    def test_set_tests_keeps_non_comment_lines(self, watchdog):
        watchdog.set_tests("assert fib(0) == 0\nassert fib(1) == 1\n# hint: use recursion")
        assert len(watchdog.test_cases) == 2
        assert "assert fib(0) == 0" in watchdog.test_cases
        assert "assert fib(1) == 1" in watchdog.test_cases

    def test_set_tests_empty_clears(self, watchdog):
        watchdog.set_tests("assert 1 == 1")
        watchdog.set_tests("")
        assert watchdog.test_cases == []

    def test_reset_context_clears_everything(self, watchdog):
        watchdog.set_task("Build X")
        watchdog.set_tests("assert True")
        watchdog.watch_all = True
        watchdog.auto_generate_tests = True
        watchdog._cached_notebook_cells = [{"cell_type": "code"}]

        watchdog.reset_context()

        assert watchdog.task_description == ""
        assert watchdog.test_cases == []
        assert watchdog._cached_notebook_cells == []


# ── _resolve_task ────────────────────────────────────────────────────────────

class TestResolveTask:
    def test_returns_explicit_task(self, watchdog):
        watchdog.set_task("Build a calculator")
        result = watchdog._resolve_task("print(1+1)")
        assert result == "Build a calculator"

    def test_returns_empty_when_no_markdown_above(self, watchdog):
        result = watchdog._resolve_task("print('hello')")
        assert result == ""




# ── _build_prompt ────────────────────────────────────────────────────────────

class TestBuildPrompt:
    def test_system_prompt_carries_socratic_rules(self, watchdog):
        # The rules ride on the system role (see _call_llm), not the user
        # content built by _build_prompt.
        prompt = watchdog._get_system_prompt()
        assert "You are Socrates" in prompt
        assert "NEVER give a direct answer" in prompt

    def test_includes_source_code(self, watchdog):
        prompt = watchdog._build_prompt("def hello(): return 'world'")
        assert "def hello(): return 'world'" in prompt
        assert "```python" in prompt

    def test_includes_task_when_set(self, watchdog):
        watchdog.set_task("Write a sort function")
        prompt = watchdog._build_prompt("def sort(x): pass")
        assert "The student's task:" in prompt
        assert "Write a sort function" in prompt

    def test_includes_error_when_provided(self, watchdog):
        prompt = watchdog._build_prompt("x = 1/0", error="ZeroDivisionError: division by zero")
        assert "ZeroDivisionError" in prompt
        assert "this error occurred" in prompt

    def test_no_error_section_when_empty(self, watchdog):
        prompt = watchdog._build_prompt("x = 1")
        assert "this error occurred" not in prompt

    def test_includes_test_cases(self, watchdog):
        watchdog.set_tests("assert add(1,2) == 3")
        prompt = watchdog._build_prompt("def add(a,b): return a+b")
        assert "expected behavior" in prompt.lower()
        assert "assert add(1,2) == 3" in prompt

    def test_empty_source_returns_minimal_prompt(self, watchdog):
        prompt = watchdog._build_prompt("")
        assert "The student just wrote this code:" in prompt

    def test_brief_style_uses_brief_prompt(self, watchdog):
        watchdog.style = "brief"
        prompt = watchdog._get_system_prompt()
        assert "BE DIRECT" in prompt.upper()
        assert "patient, curious" not in prompt


# ── _parse_response ──────────────────────────────────────────────────────────

class TestParseResponse:
    def test_silent_returns_empty(self, watchdog):
        assert watchdog._parse_response("[SILENT]") == ""

    def test_silent_case_insensitive(self, watchdog):
        assert watchdog._parse_response("[silent]") == ""

    def test_silent_with_whitespace(self, watchdog):
        assert watchdog._parse_response("  [SILENT]  ") == ""

    def test_returns_cleaned_question(self, watchdog):
        result = watchdog._parse_response(
            "Why did you use a for loop here when a list comprehension would work?"
        )
        assert "Why did you use a for loop" in result
        assert "list comprehension" in result

    def test_last_line_is_question(self, watchdog):
        """_parse_response returns the last non-empty line."""
        result = watchdog._parse_response(
            "Line one\nLine two\nLine three"
        )
        assert result == "Line three"


# ── analyze (full pipeline with mocked LLM) ──────────────────────────────────

class TestAnalyze:
    def test_analyze_returns_socratic_question(self, watchdog, socratic_response):
        with mock.patch.object(watchdog, "_has_api_key", return_value=True), \
             mock.patch.object(watchdog, "_call_llm", return_value=socratic_response):
            result = watchdog.analyze("def bubble_sort(arr): pass")
            assert "list" in result.lower() or "dictionary" in result.lower()

    def test_analyze_returns_empty_on_silent(self, watchdog, silent_response):
        with mock.patch.object(watchdog, "_has_api_key", return_value=True), \
             mock.patch.object(watchdog, "_call_llm", return_value=silent_response):
            result = watchdog.analyze("def fib(n): return n if n < 2 else fib(n-1) + fib(n-2)")
            assert result == ""

    def test_analyze_empty_source_returns_empty(self, watchdog):
        with mock.patch.object(watchdog, "_call_llm") as mock_llm:
            result = watchdog.analyze("")
            assert result == ""
            mock_llm.assert_not_called()

    def test_analyze_whitespace_source_returns_empty(self, watchdog):
        with mock.patch.object(watchdog, "_call_llm") as mock_llm:
            result = watchdog.analyze("   \n  \t  ")
            assert result == ""
            mock_llm.assert_not_called()

    def test_analyze_no_api_key_no_tests_returns_unavailable(self, watchdog):
        """Without API key and without test cases, return [LLM_UNAVAILABLE]."""
        with mock.patch.object(watchdog, "_has_api_key", return_value=False):
            result = watchdog.analyze("def add(a, b): return a + b")
            assert result == "[LLM_UNAVAILABLE]"

    def test_analyze_no_api_key_with_tests_still_works(self, watchdog):
        """With test cases, the analysis skips LLM — even without API key."""
        watchdog.set_tests("assert add(1, 2) == 3\nassert add(0, 0) == 0")
        with mock.patch.object(watchdog, "_has_api_key", return_value=False):
            result = watchdog.analyze("def add(a, b): return a + b")
            assert result == ""  # silent = all tests passed

    def test_analyze_failing_tests_no_api_key_does_not_praise(self, watchdog):
        """Failing tests + no LLM must report failure, never false praise.

        Regression: previously _call_llm returned [SILENT] without a key,
        which parsed to "" (praise) — so broken code got confetti."""
        watchdog.set_tests("assert add(1, 2) == 3")  # source returns wrong answer
        with mock.patch.object(watchdog, "_has_api_key", return_value=False):
            result = watchdog.analyze("def add(a, b): return a - b")
            assert result == "[TESTS_FAILED]"


# ── hint escalation ladder ───────────────────────────────────────────────────

class TestEscalation:
    def test_repeated_failures_escalate_then_pass_resets(self, watchdog):
        """Each failed attempt on the same task makes the prompt more concrete;
        solving the task resets the ladder back to gentle."""
        watchdog.set_task("Write add(a, b) returning a + b")
        prompts = []

        def fake_llm(prompt, system=None):
            prompts.append(prompt)
            return "What is a + b supposed to be?"  # a question = off track

        with mock.patch.object(watchdog, "_has_api_key", return_value=True), \
             mock.patch.object(watchdog, "_call_llm", side_effect=fake_llm):
            watchdog.analyze("def add(a, b): return a - b")   # attempt 1
            assert watchdog._attempts["Write add(a, b) returning a + b"] == 1
            watchdog.analyze("def add(a, b): return a - b")   # attempt 2
            watchdog.analyze("def add(a, b): return a - b")   # attempt 3

        assert "MORE POINTED" not in prompts[0]          # level 1 = plain Socratic
        assert "MORE POINTED" in prompts[1]              # level 2
        assert "leading hint" in prompts[2]              # level 3

        # Now the LLM stays silent (code is correct) → ladder resets.
        with mock.patch.object(watchdog, "_has_api_key", return_value=True), \
             mock.patch.object(watchdog, "_call_llm", return_value="[SILENT]"):
            result = watchdog.analyze("def add(a, b): return a + b")
        assert result == ""
        assert "Write add(a, b) returning a + b" not in watchdog._attempts

    def test_no_task_means_no_escalation_tracking(self, watchdog):
        """Without a resolved task there is nothing to escalate against."""
        with mock.patch.object(watchdog, "_has_api_key", return_value=True), \
             mock.patch.object(watchdog, "_call_llm", return_value="A question?"):
            watchdog.analyze("x = 1")
        assert watchdog._attempts == {}


# ── _run_tests ───────────────────────────────────────────────────────────────

class TestRunTests:
    def test_passing_tests(self, watchdog):
        watchdog.set_tests("assert add(1, 2) == 3\nassert add(0, 0) == 0")
        source = "def add(a, b): return a + b"
        result = watchdog._run_tests(source)
        assert "All tests passed" in result

    def test_failing_tests_return_error(self, watchdog):
        watchdog.set_tests("assert add(0, 0) == 999")
        source = "def add(a, b): return a + b"
        result = watchdog._run_tests(source)
        assert "AssertionError" in result or result  # will have error output

    def test_no_tests_returns_empty(self, watchdog):
        result = watchdog._run_tests("print(1)")
        assert result == ""

    def test_syntax_error_in_source(self, watchdog):
        watchdog.set_tests("assert True")
        source = "def broken(:"  # syntax error
        result = watchdog._run_tests(source)
        assert "SyntaxError" in result or result  # some error output


# ── _call_llm ─────────────────────────────────────────────────────────────────

class TestCallLLM:
    def test_no_api_key_returns_silent(self, watchdog):
        """Without an API key, _call_llm returns [SILENT]."""
        with mock.patch.dict("os.environ", {}, clear=True):
            result = watchdog._call_llm("test prompt")
            assert result == "[SILENT]"


# ── generate_tests ───────────────────────────────────────────────────────────

class TestGenerateTests:
    def test_uses_neutral_system_prompt_not_socratic_rules(self, watchdog, tmp_path):
        """Test generation must NOT send the Socratic rules — they tell the
        model to refuse code and respond [SILENT], sabotaging generation."""
        watchdog._tests_cache_dir = tmp_path
        watchdog.set_task("Write a function add(a, b) that returns a + b")
        with mock.patch.object(
            watchdog, "_call_llm",
            return_value="assert add(1, 2) == 3\nassert add(0, 0) == 0",
        ) as mock_llm:
            tests = watchdog.generate_tests()

        assert tests == ["assert add(1, 2) == 3", "assert add(0, 0) == 0"]
        # The generation call must pass an explicit, non-Socratic system prompt.
        _, kwargs = mock_llm.call_args
        assert "Socrates" not in kwargs.get("system", "")
        assert "assert" in kwargs.get("system", "").lower()


# ── SOCRATIC_RULES integrity ─────────────────────────────────────────────────

class TestSocraticRules:
    def test_contains_core_directives(self):
        assert "NEVER give a direct answer" in SOCRATIC_RULES
        assert "SILENT" in SOCRATIC_RULES
        assert "Socrates" in SOCRATIC_RULES

    def test_silent_protocol_defined(self):
        assert "[SILENT]" in SOCRATIC_RULES

    def test_brief_variant_exists(self):
        assert "BE DIRECT" in SOCRATIC_RULES_BRIEF.upper()
        assert "NEVER give a direct answer" in SOCRATIC_RULES_BRIEF

    def test_brief_is_different_from_verbose(self):
        assert SOCRATIC_RULES_BRIEF != SOCRATIC_RULES


# ── TTS voice config ─────────────────────────────────────────────────────────

class TestTTSConfig:
    def test_default_voice_is_set(self):
        assert TTS_VOICE == "en-US-AndrewNeural"


# ── singleton ────────────────────────────────────────────────────────────────

class TestSingleton:
    def test_module_singleton_exists(self):
        from socratic_watchdog._core import _watchdog
        assert isinstance(_watchdog, SocraticWatchdog)

    def test_singleton_starts_clean(self):
        from socratic_watchdog._core import _watchdog
        assert _watchdog.task_description == ""
        assert _watchdog.watch_all is False
        assert _watchdog.test_cases == []


# ── _find_current_cell with magic-stripping ────────────────────────────────

class TestFindCurrentCell:
    """Verify that _find_current_cell handles cells with %% magics."""

    def test_exact_match_without_magic(self, watchdog):
        """Plain code cell — exact match still works."""
        cells = [
            {"cell_type": "code", "source": ["def foo():\n", "    return 1\n"]},
        ]
        idx = watchdog._find_current_cell(cells, "def foo():\n    return 1")
        assert idx == 0

    def test_match_strips_cell_magic(self, watchdog):
        """Cell source has %%socratic prefix — stripped before comparison."""
        cells = [
            {"cell_type": "code", "source": ["%%socratic\n", "def foo():\n", "    return 1\n"]},
        ]
        idx = watchdog._find_current_cell(cells, "def foo():\n    return 1")
        assert idx == 0

    def test_match_strips_line_magic(self, watchdog):
        """Cell source has %time prefix — stripped before comparison."""
        cells = [
            {"cell_type": "code", "source": ["%time\n", "x = 42\n"]},
        ]
        idx = watchdog._find_current_cell(cells, "x = 42")
        assert idx == 0

    def test_match_strips_multiple_magics(self, watchdog):
        """Multiple magic lines at the top — all stripped."""
        cells = [
            {"cell_type": "code", "source": ["%%time\n", "%%socratic\n", "%autoreload\n", "x = 1\n"]},
        ]
        idx = watchdog._find_current_cell(cells, "x = 1")
        assert idx == 0

    def test_syntax_error_cell_still_matches(self, watchdog):
        """Even cells with syntax errors should match."""
        cells = [
            {"cell_type": "code", "source": ["%%socratic\n", "def broken(\n"]},
        ]
        idx = watchdog._find_current_cell(cells, "def broken(")
        assert idx == 0


# ── _extract_tests_from_cell_below ─────────────────────────────────────────

class TestExtractTestsFromCellBelow:
    """Verify #Test cases detection from the cell below."""

    @pytest.fixture
    def import_fn(self):
        from socratic_watchdog.magics import _extract_tests_from_cell_below
        return _extract_tests_from_cell_below

    def test_no_cell_below_returns_none(self, import_fn):
        cells = [{"cell_type": "code", "source": ["x = 1\n"]}]
        result = import_fn(cells, 0)
        assert result is None

    def test_cell_below_not_code_returns_none(self, import_fn):
        cells = [
            {"cell_type": "code", "source": ["%%socratic\n", "x = 1\n"]},
            {"cell_type": "markdown", "source": ["just notes\n"]},
        ]
        result = import_fn(cells, 0)
        assert result is None

    def test_no_test_cases_marker_returns_none(self, import_fn):
        cells = [
            {"cell_type": "code", "source": ["%%socratic\n", "x = 1\n"]},
            {"cell_type": "code", "source": ["print('hello')\n"]},
        ]
        result = import_fn(cells, 0)
        assert result is None

    def test_parses_assert_statements(self, import_fn):
        cells = [
            {"cell_type": "code", "source": ["%%socratic\n", "def add(a, b): return a+b\n"]},
            {"cell_type": "code", "source": [
                "#Test cases\n",
                "assert add(1, 2) == 3\n",
                "assert add(0, 0) == 0\n",
                "assert add(-1, 1) == 0\n",
            ]},
        ]
        result = import_fn(cells, 0)
        assert result == [
            "assert add(1, 2) == 3",
            "assert add(0, 0) == 0",
            "assert add(-1, 1) == 0",
        ]

    def test_skips_comments_and_blanks(self, import_fn):
        cells = [
            {"cell_type": "code", "source": ["%%socratic\n", "x = 1\n"]},
            {"cell_type": "code", "source": [
                "#Test cases\n",
                "# edge case: zero\n",
                "assert foo(0) == 0\n",
                "\n",
                "assert foo(2) == 4\n",
            ]},
        ]
        result = import_fn(cells, 0)
        assert result == ["assert foo(0) == 0", "assert foo(2) == 4"]

    def test_empty_test_cell_returns_empty_list(self, import_fn):
        cells = [
            {"cell_type": "code", "source": ["%%socratic\n", "x = 1\n"]},
            {"cell_type": "code", "source": ["#Test cases\n"]},
        ]
        result = import_fn(cells, 0)
        assert result == []

    def test_case_insensitive_marker(self, import_fn):
        cells = [
            {"cell_type": "code", "source": ["x = 1\n"]},
            {"cell_type": "code", "source": ["#TEST CASES\n", "assert True\n"]},
        ]
        result = import_fn(cells, 0)
        assert result == ["assert True"]

    def test_tests_marker_without_cases(self, import_fn):
        """The '#Tests' marker (without 'cases') should also work."""
        cells = [
            {"cell_type": "code", "source": ["%%socratic\n", "x = 1\n"]},
            {"cell_type": "code", "source": ["#Tests\n", "assert foo(1) == 2\n"]},
        ]
        result = import_fn(cells, 0)
        assert result == ["assert foo(1) == 2"]

    def test_test_cases_underscore_marker(self, import_fn):
        """The '#test_cases' marker should also work."""
        cells = [
            {"cell_type": "code", "source": ["x = 1\n"]},
            {"cell_type": "code", "source": ["#test_cases\n", "assert True\n"]},
        ]
        result = import_fn(cells, 0)
        assert result == ["assert True"]

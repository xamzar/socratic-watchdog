"""
socratic_watchdog._core — Core Socratic analysis engine.

No IPython dependency. Works in any Python environment.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import tempfile
import textwrap
import wave
from pathlib import Path
from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════════════

TTS_VOICE = os.environ.get("SOCRATIC_TTS_VOICE", "en-US-AndrewNeural")
# espeak is the current default while iterating (fast, local, ~0.02s warm,
# robotic). edge-tts (cloud neural, ~1s) is the planned default; espeak then
# stays as the offline fallback. kokoro (local neural, ~3.8s) is also available.
# Flip this one constant to change the project-wide default.
DEFAULT_TTS_BACKEND = "espeak"
TTS_BACKEND = os.environ.get("SOCRATIC_TTS_BACKEND", DEFAULT_TTS_BACKEND)
KOKORO_VOICE = os.environ.get("SOCRATIC_KOKORO_VOICE", "af_heart")
# ^ Kokoro voice pack — "af_heart" (default, female), "am_adam", "bm_lewis", etc.
LLM_TIMEOUT = int(os.environ.get("SOCRATIC_LLM_TIMEOUT", "30"))

SOCRATIC_RULES = textwrap.dedent("""\
    You are Socrates, the ancient Greek philosopher, acting as a coding mentor.

    RULES
    1. NEVER give a direct answer. NEVER show corrected code.
    2. Ask exactly ONE guiding question per response.
    3. Reference something SPECIFIC in the student's code.
    4. ONLY respond [SILENT] when ALL of these are true:
       a) The code has NO syntax errors
       b) The code runs without runtime errors
       c) The code correctly fulfills the task
       If the code has ANY error (syntax, runtime, logical) → it is BROKEN.
       A broken submission CANNOT be [SILENT]. Always ask a question.
    5. An error means the student is STRUGGLING and needs guidance.
       Ask about the error — help them think about what went wrong.

    SILENCE protocol: only when code is flawless AND correct, respond:
    [SILENT]

    Your tone: patient, curious, slightly playful. Like a wise mentor
    walking beside the student, not a lecturer.
""")

SOCRATIC_RULES_BRIEF = textwrap.dedent("""\
    You are Socrates, the ancient Greek philosopher, acting as a coding mentor.

    RULES
    1. NEVER give a direct answer. NEVER show corrected code.
    2. Ask exactly ONE guiding question per response.
    3. Reference something SPECIFIC in the student's code.
    4. ONLY respond [SILENT] when ALL of these are true:
       a) The code has NO syntax errors
       b) The code runs without runtime errors
       c) The code correctly fulfills the task
       If the code has ANY error (syntax, runtime, logical) → it is BROKEN.
       A broken submission CANNOT be [SILENT]. Always ask a question.
    5. An error means the student is STRUGGLING and needs guidance.
       Ask about the error — help them think about what went wrong.

    SILENCE protocol: only when code is flawless AND correct, respond:
    [SILENT]

    STYLE: BE DIRECT.  No preambles.  Do NOT say "Ah", "I see", "Let me
    ask you", "Interesting", or any lead-in.  Your response is ONLY the
    question.  Just the question.  Example: "Why did you choose subtraction
    instead of addition?"  Not "Ah, I see you've used subtraction. Let me
    ask: why did you choose that?"
""")

# System prompt for the test-case generator.  The Socratic rules MUST NOT be
# used here — they tell the model to refuse to write code and to respond
# [SILENT], which directly sabotages test generation.
TEST_GEN_SYSTEM = textwrap.dedent("""\
    You are a precise Python test-case generator for beginner exercises.
    Output ONLY valid Python `assert` statements, one per line — no prose,
    no markdown, no code fences.
""")


# ═══════════════════════════════════════════════════════════════════════════
#  CORE ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class SocraticWatchdog:
    """Socratic coding mentor that analyzes notebook cells via LLM + TTS.

    Core pipeline (``analyze()``):

        1. Run pre-assigned tests (if any) — instant pass = skip LLM
        2. Build prompt with task context + source + error
        3. Call LLM via OpenAI-compatible API (DeepSeek default)
        4. Parse response: [SILENT] → praise, otherwise → guiding question
        5. TTS speaks the question or praise (espeak-ng default)

    All LLM parameters (model, base URL, API key) are read from
    environment variables on every call — hot-switchable mid-session.

    The module-level ``_watchdog`` singleton is what the IPython magics
    use.  Create a fresh instance for testing.
    """

    def __init__(self):
        self.task_description: str = ""
        """Explicit task set via ``%socratic_task``."""

        self.watch_all: bool = False
        """When True, every cell execution is analyzed automatically."""

        self.auto_generate_tests: bool = False
        """When True, ``%socratic_task`` automatically triggers test generation."""

        self.style: str = "verbose"
        """Socrates response style: 'verbose' (playful) or 'brief' (direct questions only)."""

        self.exploration_mode: bool = False
        """When True and no task is set, Socrates encourages free exploration
        of the current topic instead of asking what the student is trying to do."""

        self._exploration_history: list[dict] = []
        """Accumulated conversation in exploration mode: each entry is
        {socrates: str, student_code: str, student_error: str}.  Fed back
        into the prompt so Socrates can build on previous exchanges."""

        self.test_cases: list[str] = []
        """Pre-assigned test cases (e.g. ['assert fib(0) == 0', ...])."""

        self.hidden_test_cases: list[str] = []
        """Hidden test cases — run but never shown to the student.
        Set via ``%socratic_tests --hidden`` or auto-generated."""

        self._tests_cache_dir: Path = (
            Path(os.environ.get("SOCRATIC_TESTS_CACHE",
                                Path.home() / ".hermes" / "socratic_tests_cache"))
        )

        # internal cache: avoid re-scanning the same notebook repeatedly
        self._cached_notebook_path: str = ""
        self._cached_notebook_cells: list[dict] = []

        # timing / debugging
        self._timings: dict[str, float] = {}
        """Per-step wall-clock timings from the last ``analyze()`` call (seconds)."""
        self._tts_time: Optional[float] = None
        """Wall-clock time of the last ``speak()`` call (seconds), or None."""
        self._debug: bool = os.environ.get("SOCRATIC_DEBUG", "") == "1"
        """When True, prints timing breakdown after each analysis."""
        self._generate_timings: dict[str, float] = {}
        """Per-step timings from the last ``generate_tests()`` call."""
        self._auto_detect_time: Optional[float] = None
        """Wall-clock time of the last ``_try_auto_detect`` call, or None."""

        self._attempts: dict[str, int] = {}
        """Failed-attempt count per task (keyed by task text). Drives the hint
        escalation ladder: repeated failures on the same task make Socrates'
        questions progressively more concrete. Reset when the task passes."""

    # ── Public API ──────────────────────────────────────────────────────

    @property
    def _all_test_cases(self) -> list[str]:
        """Visible + hidden tests combined (for execution only)."""
        return self.test_cases + self.hidden_test_cases

    def set_task(self, description: str) -> None:
        """Set an explicit task (overrides auto-detection from markdown above).
        
        Clears test cases only when the task changes."""
        new_task = description.strip()
        task_changed = new_task and new_task != self.task_description
        self.task_description = new_task
        if task_changed:
            self.test_cases = []
            self.hidden_test_cases = []
        if self.auto_generate_tests and self.task_description:
            self.generate_tests()

    def set_tests(self, tests_text: str) -> None:
        """Parse and store pre-assigned test cases."""
        lines = tests_text.strip().split("\n")
        self.test_cases = [
            line.strip() for line in lines
            if line.strip() and not line.strip().startswith("#")
        ]

    def analyze(self, source: str, error: str = "") -> str:
        """Send cell source to Socrates LLM. Returns question or empty (silent).

        Populates ``self._timings`` with per-step wall-clock times.

        **Fast path:** when ``%socratic_tests`` are set and the student's code
        passes all of them, the LLM is skipped entirely — instant silent + confetti.

        Pipeline: tests → prompt → LLM → parse → result
        """
        import time
        t0 = time.monotonic()
        self._timings = {}

        # ── Guard: empty source ──
        if not source.strip():
            self._timings["total"] = 0.0
            return ""

        # ═════════════════════════════════════════════════════════════════
        #  PHASE 1 — Fast path: deterministic test check (no LLM)
        # ═════════════════════════════════════════════════════════════════
        if self._all_test_cases:
            test_result = self._run_tests(source)
            if test_result and "✅ All tests passed" in test_result:
                self._timings = {
                    "build_prompt": 0.0,
                    "llm_call":     0.0,
                    "parse":        0.0,
                    "total":        0.0,
                }
                if self._debug:
                    total = len(self._all_test_cases)
                    cached = "read" in self._generate_timings
                    tag = "cached" if cached else "fresh"
                    print(f"🧪  {total}/{total} tests passed ⚡  skipping LLM  ({tag})")
                    for tc in self._all_test_cases:
                        print(f"     {tc}")
                self._attempts.pop(self._resolve_task(source), None)  # solved → reset ladder
                return ""  # instant win — confetti, no API call
            # Tests failed — feed failure output to the LLM for better questions
            if test_result:
                if self._debug:
                    passed = sum(1 for d in self._test_details if d["status"] == "pass")
                    failed = sum(1 for d in self._test_details if d["status"] == "fail")
                    total = passed + failed
                    cached = "read" in self._generate_timings
                    tag = "cached" if cached else "fresh"
                    print(f"🧪  {passed}/{total} passed, {failed} failed — sending to LLM  ({tag})")
                    for d in self._test_details:
                        if d["status"] == "pass":
                            print(f"     ✅ {d['test']}")
                        else:
                            print(f"     ❌ {d['test']}  →  {d.get('error', '')}")
                if error:
                    error = error + "\n\n" + test_result
                else:
                    error = test_result

        # ═════════════════════════════════════════════════════════════════
        #  PHASE 2 — Build prompt with task + source + error context
        # ═════════════════════════════════════════════════════════════════
        t1 = time.monotonic()

        # No API key → we can't ask Socrates a question.  But we may still
        # have a deterministic verdict from the tests, so branch carefully:
        #   • tests ran and FAILED → say so plainly (never praise broken code)
        #   • no tests at all       → we genuinely can't verify
        # (All-tests-passed already returned "" above with confetti.)
        if not self._has_api_key():
            self._timings = {
                "build_prompt": 0.0,
                "llm_call":     0.0,
                "parse":        0.0,
                "total":        0.0,
            }
            if self._all_test_cases:
                if self._debug:
                    print("⚠️  Tests failed and no LLM — reporting failure, not asking a question")
                return "[TESTS_FAILED]"
            if self._debug:
                print("⚠️  LLM unavailable — no API key configured, no test cases set")
            return "[LLM_UNAVAILABLE]"

        # Hint escalation: count off-track attempts on this task and let the
        # prompt get more concrete each time (only when there's a real task).
        task_key = self._resolve_task(source)
        escalation = ""
        if task_key:
            self._attempts[task_key] = self._attempts.get(task_key, 0) + 1
            escalation = self._escalation_directive(self._attempts[task_key])

        prompt = self._build_prompt(source, error, escalation)
        t2 = time.monotonic()

        # ═════════════════════════════════════════════════════════════════
        #  PHASE 3 — LLM call (OpenAI-compatible HTTPS POST)
        # ═════════════════════════════════════════════════════════════════
        raw = self._call_llm(prompt)
        t3 = time.monotonic()

        if self._debug:
            model = os.environ.get("SOCRATIC_LLM_MODEL", "deepseek-chat")
            print(f"🤖  LLM: {model}  →  {round(t3 - t2, 3)}s")

        # ═════════════════════════════════════════════════════════════════
        #  PHASE 4 — Parse: extract question or detect [SILENT]
        # ═════════════════════════════════════════════════════════════════
        result = self._parse_response(raw)
        t4 = time.monotonic()

        self._timings = {
            "build_prompt": round(t2 - t1, 3),
            "llm_call":     round(t3 - t2, 3),
            "parse":        round(t4 - t3, 3),
            "total":        round(t4 - t1, 3),
        }

        # Correct now (LLM stayed [SILENT] → empty result) → reset the ladder
        # so a later stumble on this task starts gentle again.
        if not result and task_key:
            self._attempts.pop(task_key, None)

        # ── Record exchange in exploration conversation ──
        if self.exploration_mode and source.strip():
            self._exploration_history.append({
                "socrates": result or "[SILENT — praised]",
                "student_code": source.strip(),
                "student_error": error.strip() if error else "",
            })

        return result

    def speak(self, text: str) -> Optional["IPython.display.Audio"]:
        """Convert text to speech. Returns Audio widget or None on failure.

        Backend is selected by ``SOCRATIC_TTS_BACKEND`` env var (read on each call):
        - ``espeak`` (current default) — local espeak-ng, robotic, ~0.03s, no network
        - ``edge-tts`` — Microsoft neural voices, cloud, ~3s (planned default)
        - ``kokoro`` — local neural, ~3.8s, 82M model

        Records wall-clock time in ``self._tts_time``.
        """
        import time
        self._tts_time = None
        if not text.strip():
            return None
        t0 = time.monotonic()
        backend = os.environ.get("SOCRATIC_TTS_BACKEND", DEFAULT_TTS_BACKEND)
        if backend == "espeak":
            result = self._speak_espeak(text)
        elif backend == "kokoro":
            result = self._speak_kokoro(text)
        else:
            result = self._speak_edge(text)
        self._tts_time = round(time.monotonic() - t0, 3)
        if self._debug and result is not None:
            print(f"🔊  TTS: {backend}  →  {self._tts_time}s")
        return result

    def _speak_edge(self, text: str) -> Optional["IPython.display.Audio"]:
        """TTS via Microsoft Edge cloud service (neural voice)."""
        from IPython.display import Audio
        try:
            mp3 = tempfile.mktemp(suffix=".mp3")
            subprocess.run(
                ["edge-tts", "--text", text, "--voice", TTS_VOICE,
                 "--rate", "+5%", "--pitch", "+5Hz", "--write-media", mp3],
                capture_output=True, timeout=30,
            )
            if Path(mp3).exists() and Path(mp3).stat().st_size > 0:
                return Audio(mp3, autoplay=True, rate=22050)
        except Exception:
            pass
        return None

    def _speak_espeak(self, text: str) -> Optional["IPython.display.Audio"]:
        """TTS via espeak-ng — local, offline, ~6x faster than edge-tts."""
        from IPython.display import Audio
        try:
            wav = tempfile.mktemp(suffix=".wav")
            voice = os.environ.get("SOCRATIC_ESPEAK_VOICE", "en-us")
            # espeak-ng: -w writes WAV, -s is speed (words/min), -v is voice
            subprocess.run(
                ["espeak-ng", "-w", wav, "-v", voice,
                 "-s", "160", "-p", "40", text],
                capture_output=True, timeout=5,
            )
            if Path(wav).exists() and Path(wav).stat().st_size > 0:
                return Audio(wav, autoplay=True)
        except Exception:
            pass
        return None

    def _speak_kokoro(self, text: str) -> Optional["IPython.display.Audio"]:
        """TTS via Kokoro — local neural, ~2.5s, 82M model (no cloud)."""
        from IPython.display import Audio
        try:
            pipeline = _get_kokoro_pipeline()
            for _gs, _ps, audio_tensor in pipeline(text, voice=KOKORO_VOICE):
                samples = audio_tensor.numpy()
                wav_buf = io.BytesIO()
                with wave.open(wav_buf, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)  # 16-bit
                    wf.setframerate(24000)
                    wf.writeframes(
                        (samples * 32767).astype("int16").tobytes()
                    )
                wav_buf.seek(0)
                return Audio(wav_buf.read(), autoplay=True, rate=24000)
        except Exception:
            pass
        return None

    def reset_context(self) -> None:
        """Reset task description, test cases, and cached notebook data."""
        self.task_description = ""
        self.test_cases = []
        self.hidden_test_cases = []
        self._cached_notebook_path = ""
        self._cached_notebook_cells = []
        self._exploration_history = []

    def peek_cache(self, task_description: str = "") -> dict | None:
        """Return the cached test data for a task, or None if not cached.

        Returns ``{task, tests, hash, age_seconds}`` so callers can display
        the content without loading it into ``hidden_test_cases``.
        """
        import hashlib, time as _time
        task = (task_description or self.task_description).strip()
        if not task:
            return None
        cache_key = hashlib.sha256(task.encode()).hexdigest()[:16]
        cache_file = self._tests_cache_dir / f"{cache_key}.json"
        if not cache_file.exists():
            return None
        try:
            cached = json.loads(cache_file.read_text())
            return {
                "task": cached.get("task", task),
                "tests": cached.get("tests", []),
                "hash": cache_key,
                "age_seconds": round(_time.time() - cache_file.stat().st_mtime),
            }
        except Exception:
            return None

    def list_cache(self) -> list[dict]:
        """Return a list of all cached test entries.

        Each entry: ``{task, tests, hash, age_seconds, test_count}``.
        Sorted newest-first.
        """
        import time as _time
        entries = []
        if not self._tests_cache_dir.exists():
            return entries
        for cache_file in sorted(
            self._tests_cache_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            try:
                cached = json.loads(cache_file.read_text())
                entries.append({
                    "task": cached.get("task", ""),
                    "tests": cached.get("tests", []),
                    "hash": cache_file.stem,
                    "age_seconds": round(_time.time() - cache_file.stat().st_mtime),
                    "test_count": len(cached.get("tests", [])),
                })
            except Exception:
                pass
        return entries

    def generate_tests(self, task_description: str = "", quiet: bool = False) -> list[str]:
        """Generate hidden test cases from the task description via LLM.

        Results are cached on disk (keyed by task description hash) so the
        LLM is called only once per task.  Returns the test case lines.

        Populates ``self._generate_timings`` with per-step wall-clock times
        (printed when ``self._debug`` is True).  Pass ``quiet=True`` to
        suppress the status prints (caller emits its own indicator).
        """
        import hashlib, time as _time
        t0 = _time.monotonic()
        self._generate_timings = {}

        task = (task_description or self.task_description).strip()
        if not task:
            if not quiet:
                print("🧠  No task set — can't generate tests. Use %socratic_task first.")
            self._generate_timings["total"] = 0.0
            return []

        # ── Hash + cache path ──
        cache_key = hashlib.sha256(task.encode()).hexdigest()[:16]
        self._tests_cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = self._tests_cache_dir / f"{cache_key}.json"
        t1 = _time.monotonic()

        # ── Cache lookup ──
        hit = cache_file.exists()
        t2 = _time.monotonic()

        # ── Cache hit ──
        if hit:
            try:
                cached = json.loads(cache_file.read_text())
                self.hidden_test_cases = cached.get("tests", [])
                t3 = _time.monotonic()

                self._generate_timings = {
                    "setup":   round(t1 - t0, 4),
                    "lookup":  round(t2 - t1, 4),
                    "read":    round(t3 - t2, 4),
                    "total":   round(t3 - t0, 4),
                }

                if not quiet:
                    print(f"🧠  Loaded {len(self.hidden_test_cases)} cached hidden tests for this task.")
                if self._debug and not quiet:
                    for tc in self.hidden_test_cases:
                        print(f"     {tc}")
                return self.hidden_test_cases
            except Exception:
                pass  # corrupt cache → regenerate

        # ── Cache miss — ask LLM ──
        gen_prompt = textwrap.dedent(f"""\
            You are a test-case generator for a beginner programming exercise.

            TASK: {task}

            Write 4-6 Python assert statements that test whether a correct
            solution works.  Use ONLY small integers (-10 to 100).
            Test the basic logic — normal inputs, zero, and one negative input.
            Do NOT test floating-point numbers, large values, or edge cases
            that a beginner would not think about.

            Output ONLY the assert statements, one per line — no markdown,
            no explanations, no code fences.

            Example format:
            assert greet("Alice") == "Hello, Alice"
            assert greet("") == "Hello, "
            assert greet("Bob") == "Hello, Bob"
        """)

        t_llm_start = _time.monotonic()
        try:
            raw = self._call_llm(gen_prompt, system=TEST_GEN_SYSTEM)
            if raw == "[SILENT]":
                if not quiet:
                    print("⚠️  Could not generate tests (LLM unavailable).")
                self._generate_timings = {
                    "setup": round(t1 - t0, 4),
                    "lookup": round(t2 - t1, 4),
                    "llm_err": round(_time.monotonic() - t_llm_start, 4),
                    "total": round(_time.monotonic() - t0, 4),
                }
                return []
        except Exception:
            if not quiet:
                print("⚠️  Could not generate tests (LLM error).")
            self._generate_timings = {
                "setup": round(t1 - t0, 4),
                "lookup": round(t2 - t1, 4),
                "llm_err": round(_time.monotonic() - t_llm_start, 4),
                "total": round(_time.monotonic() - t0, 4),
            }
            return []
        t_llm_end = _time.monotonic()

        # ── Parse: keep only assert lines ──
        tests = []
        for line in raw.split("\n"):
            stripped = line.strip()
            if stripped.startswith("assert "):
                tests.append(stripped)
        t_parse_end = _time.monotonic()

        if not tests:
            if not quiet:
                print("⚠️  LLM returned no parseable assert statements.")
            self._generate_timings = {
                "setup":   round(t1 - t0, 4),
                "lookup":  round(t2 - t1, 4),
                "llm":     round(t_llm_end - t_llm_start, 4),
                "parse":   round(t_parse_end - t_llm_end, 4),
                "total":   round(t_parse_end - t0, 4),
            }
            return []

        # ── Cache write ──
        cache_file.write_text(json.dumps({"task": task, "tests": tests}, indent=2))
        self.hidden_test_cases = tests
        t_write_end = _time.monotonic()

        self._generate_timings = {
            "setup":   round(t1 - t0, 4),
            "lookup":  round(t2 - t1, 4),
            "llm":     round(t_llm_end - t_llm_start, 4),
            "parse":   round(t_parse_end - t_llm_end, 4),
            "write":   round(t_write_end - t_parse_end, 4),
            "total":   round(t_write_end - t0, 4),
        }

        if not quiet:
            print(f"🧠  Generated and cached {len(tests)} hidden tests for this task.")
        if self._debug and not quiet:
            for tc in tests:
                print(f"     {tc}")
        return tests

    def _load_cached_tests(self, task: str, /) -> Optional[list[str]]:
        """Check the on-disk cache for tests matching this task.

        Returns the cached test lines (list), or ``None`` if no cache hit.
        This works for both human-written and AI-generated tests — they
        share the same cache namespace (keyed by task hash).
        """
        import hashlib
        cache_key = hashlib.sha256(task.strip().encode()).hexdigest()[:16]
        cache_file = self._tests_cache_dir / f"{cache_key}.json"
        if not cache_file.exists():
            return None
        try:
            cached = json.loads(cache_file.read_text())
            return cached.get("tests", [])
        except Exception:
            return None

    def _cache_tests(self, task: str, tests: list[str], source: str = "manual",
                     quiet: bool = False) -> None:
        """Write tests to the on-disk cache, keyed by task hash.

        ``source`` is a label for debugging: ``"manual"`` for human-written
        tests, ``"generated"`` for AI-generated.  Pass ``quiet=True`` to
        suppress the confirmation print (caller emits its own indicator).
        """
        import hashlib
        cache_key = hashlib.sha256(task.strip().encode()).hexdigest()[:16]
        self._tests_cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = self._tests_cache_dir / f"{cache_key}.json"
        cache_file.write_text(json.dumps({
            "task": task.strip(),
            "tests": tests,
            "source": source,
        }, indent=2))
        if not quiet:
            print(f"🧠  Cached {len(tests)} {source} tests for this task.")

    def detect_task_from_notebook(self, current_source: str) -> Optional[str]:
        """Try to find the markdown cell just above the current code cell.

        Only returns text if the markdown contains a task-trigger word
        (e.g. "Task", "Exercise", "Write a function").  This prevents
        unrelated markdown (headings, notes) from being treated as a task.

        Uses ``jupyter-mcp-cli`` if available (DIVE platform), otherwise
        returns ``None``.
        """
        _TRIGGERS = ["task"]

        try:
            cells = self._get_notebook_cells()
            if not cells:
                return None

            # Find which cell we're currently running
            current_idx = self._find_current_cell(cells, current_source)
            if current_idx is None or current_idx == 0:
                return None

            # Scan upward for the task markdown, skipping helper cells
            # (pure-magic / blank code) but stopping at real code.
            cell_above = None
            j = current_idx - 1
            while j >= 0:
                prev = cells[j]
                if prev.get("cell_type") == "markdown":
                    cell_above = prev
                    break
                prev_src = "".join(prev.get("source", []))
                if any(ln.strip() and not ln.strip().startswith(("%", "!"))
                       for ln in prev_src.splitlines()):
                    break
                j -= 1
            if cell_above is not None and cell_above.get("cell_type") == "markdown":
                text = "".join(cell_above.get("source", []))
                # Skip very short cells (titles, dividers)
                if len(text.strip()) > 30:
                    # Must contain a task-trigger word
                    text_lower = text.lower()
                    if not any(t in text_lower for t in _TRIGGERS):
                        return None
                    # Clean up: remove HTML comments, strip whitespace
                    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
                    return text.strip()
        except Exception:
            pass
        return None

    def _get_system_prompt(self) -> str:
        """Return the Socratic system prompt based on the current style."""
        if self.style == "brief":
            return SOCRATIC_RULES_BRIEF
        return SOCRATIC_RULES

    def _resolve_task(self, source: str) -> str:
        """Return the best task description available for this cell.

        Always tries auto-detection from the markdown cell above.
        Explicit %socratic_task overrides auto-detection.
        """
        if self.task_description:
            return self.task_description
        detected = self.detect_task_from_notebook(source)
        if detected:
            return detected
        return ""

    def _escalation_directive(self, level: int) -> str:
        """Extra prompt guidance based on how many times the student has failed
        this same task. Level 1 = normal Socratic question; higher levels get
        progressively more concrete, capping at a direct (but not spoon-fed)
        explanation so a stuck student is never left looping forever.
        """
        if level <= 1:
            return ""
        if level == 2:
            return ("\n\nThe student has already tried this task and is still "
                    "off track. Ask a MORE POINTED question that narrows toward "
                    "the specific problem — still a single question, no answer.")
        if level == 3:
            return ("\n\nThe student has tried several times and is stuck. Give "
                    "a strong leading hint that names the concept or the exact "
                    "line involved, framed as a question. Do NOT write the fix.")
        return ("\n\nThe student has tried many times and is likely frustrated. "
                "It is now OK to state plainly what is wrong and which concept "
                "fixes it (one or two sentences), then ask them to apply it. "
                "Do NOT paste a full corrected solution.")

    def _build_prompt(self, source: str, error: str = "", escalation: str = "") -> str:
        # The Socratic system rules are sent via the system role in
        # _call_llm — don't duplicate them in the user content here.
        parts: list[str] = []

        # Task context (explicit or auto-detected)
        task = self._resolve_task(source)
        if task:
            parts.append(f"The student's task: {task}")
            parts.append("")

        # Pre-assigned test cases
        if self.test_cases:
            parts.append("The expected behavior (test cases):")
            parts.append("```python")
            for tc in self.test_cases:
                parts.append(tc)
            parts.append("```")
            # Try to run them against the student's code
            test_results = self._run_tests(source)
            if test_results:
                parts.append("")
                parts.append("Test results against the student's code:")
                parts.append("```")
                parts.append(test_results)
                parts.append("```")
            parts.append("")

        parts.append("The student just wrote this code:")
        parts.append("```python")
        parts.append(source.rstrip())
        parts.append("```")

        if error:
            parts.append("")
            parts.append("When they ran it, this error occurred:")
            parts.append("```")
            parts.append(error.strip())
            parts.append("```")

        # ── Exploration mode (no task, student is experimenting) ──
        task = self._resolve_task(source)
        if not task and self.exploration_mode:
            # Include recent conversation history so Socrates builds on it
            if self._exploration_history:
                parts.append("")
                parts.append("Recent conversation (Socrates and the student):")
                for entry in self._exploration_history[-5:]:  # last 5 exchanges
                    parts.append(f"Socrates: {entry['socrates']}")
                    parts.append("Student ran:\n```python\n" + entry['student_code'] + "\n```")
                    if entry.get('student_error'):
                        parts.append(f"Error: {entry['student_error']}")
                parts.append("")

            parts.append(
                "EXPLORATION MODE: The student has NO specific task — they are "
                "freely exploring and experimenting.  Do NOT ask abstract "
                "philosophical questions about 'the nature of' anything.  "
                "Do NOT ask what they are 'supposed' to do.  "
                "Give ONE concrete, actionable suggestion: 'Try adding X', "
                "'What happens if you call .Y()?', 'Delete Z and see what changes', "
                "'Can you combine this with ...?', 'What if you used a loop here?'.  "
                "Be a lab partner: playful, curious, and always pointing at "
                "something specific in their code they can actually DO next."
            )
            parts.append("")

        if escalation:
            parts.append(escalation)

        parts.append("")
        parts.append(
            "DECISION (read carefully):\n"
            "• If the code has ANY error (syntax, runtime, traceback) → the student is OFF TRACK.\n"
            "  Ask ONE Socratic question about the error.\n"
            "• If the code runs but does NOT fulfill the task → the student is OFF TRACK.\n"
            "  Ask ONE Socratic question about their approach.\n"
            "• ONLY if the code runs perfectly AND fulfills the task → respond [SILENT].\n"
            "\n"
            "REMEMBER: an error = broken = ASK A QUESTION. Do not say [SILENT]."
        )
        return "\n".join(parts)

    def _has_api_key(self) -> bool:
        """Return True if an LLM API key is configured (any provider)."""
        return bool(
            os.environ.get("SOCRATIC_LLM_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )

    def _call_llm(self, prompt: str, system: Optional[str] = None) -> str:
        """Call the LLM API to analyze student code.

        Sends ``prompt`` to the OpenAI-compatible chat completions endpoint.
        No subprocess, no CLI — a straightforward HTTPS POST.

        ``system`` overrides the system-role message; defaults to the Socratic
        rules.  Callers that are NOT doing Socratic analysis (e.g. test-case
        generation) must pass their own neutral system prompt.

        Returns the model's message content, or ``"[SILENT]"`` on failure.

        Environment variables (checked in order, first wins):

        * **Model**: SOCRATIC_LLM_MODEL → OPENAI_MODEL → ``deepseek-chat``
        * **Base URL**: SOCRATIC_LLM_BASE_URL → OPENAI_BASE_URL → ``https://api.deepseek.com``
        * **API Key**: SOCRATIC_LLM_API_KEY → DEEPSEEK_API_KEY → OPENAI_API_KEY
        """
        try:
            import json as _json
            import urllib.request as _req
            base_url = (
                os.environ.get("SOCRATIC_LLM_BASE_URL")
                or os.environ.get("OPENAI_BASE_URL")
                or "https://api.deepseek.com"
            )
            api_key = (
                os.environ.get("SOCRATIC_LLM_API_KEY")
                or os.environ.get("DEEPSEEK_API_KEY")
                or os.environ.get("OPENAI_API_KEY")
                or ""
            )
            model = (
                os.environ.get("SOCRATIC_LLM_MODEL")
                or os.environ.get("OPENAI_MODEL")
                or "deepseek-chat"
            )
            if not api_key:
                return "[SILENT]"
            body = _json.dumps({
                "model": model,
                "messages": [
                    {"role": "system", "content": system or self._get_system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 200,
                "temperature": 0.7,
            }).encode()
            req = _req.Request(
                f"{base_url.rstrip('/')}/chat/completions",
                data=body,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {api_key}"},
                method="POST",
            )
            with _req.urlopen(req, timeout=LLM_TIMEOUT) as resp:
                data = _json.loads(resp.read())
                return data["choices"][0]["message"]["content"]
        except Exception:
            return "[SILENT]"

    def _parse_response(self, raw: str) -> str:
        """Extract the Socratic question from the LLM response.

        The LLM returns either a single question line or ``[SILENT]``
        when the code is correct.  Since we call the API directly, the
        response is clean message content — no CLI metadata to filter.
        """
        if "[SILENT]" in raw.upper():
            return ""
        if "[LLM_UNAVAILABLE]" in raw.upper():
            return "[LLM_UNAVAILABLE]"
        # Take the last non-empty line as the question
        for line in reversed(raw.strip().split("\n")):
            stripped = line.strip()
            if stripped:
                return stripped.strip('"').strip("'")
        return ""

    # ── Notebook introspection (jupyter-mcp-cli) ────────────────────────

    def _get_notebook_cells(self) -> list[dict]:
        """Fetch all cells of the currently active notebook via jupyter-mcp-cli."""
        # Check cache: if we already have cells, return them
        if self._cached_notebook_cells:
            return self._cached_notebook_cells

        try:
            # 1. Get the active notebook path
            result = subprocess.run(
                ["jupyter-mcp-cli", "get_active_notebook"],
                capture_output=True, text=True, timeout=5,
            )
            notebook_path = result.stdout.strip()
            if not notebook_path or "error" in notebook_path.lower():
                return []

            self._cached_notebook_path = notebook_path

            # 2. Read all cells
            result = subprocess.run(
                ["jupyter-mcp-cli", "read_notebook_cells",
                 "--arg", f"notebook_path={notebook_path}"],
                capture_output=True, text=True, timeout=5,
            )
            data = json.loads(result.stdout)
            cells = data.get("cells", [])
            self._cached_notebook_cells = cells
            return cells
        except Exception:
            return []

    def _find_current_cell(self, cells: list[dict],
                           current_source: str) -> Optional[int]:
        """Find the index of the current code cell in the notebook cell list.

        Matches by source content similarity.

        IPython strips the cell magic line (%%socratic) from the source
        handed to the magic, but jupyter-mcp-cli returns the FULL cell
        source.  We strip magic lines here so exact matching works.
        """
        current_normalized = current_source.strip()
        best_idx = None
        best_score = 0

        for idx, cell in enumerate(cells):
            if cell.get("cell_type") != "code":
                continue
            cell_source_raw = "".join(cell.get("source", [])).strip()
            # Strip cell/line magic prefix lines for fair comparison
            cell_source = re.sub(
                r'^(%%.*|%.*)\n', '', cell_source_raw, flags=re.MULTILINE
            ).strip()
            # Exact match
            if cell_source == current_normalized:
                return idx
            # Partial match (first 80% overlap)
            if current_normalized:
                min_len = min(len(cell_source), len(current_normalized))
                overlap = sum(
                    1 for a, b in zip(cell_source, current_normalized)
                    if a == b
                )
                score = overlap / max(len(cell_source), 1)
                if score > best_score:
                    best_score = score
                    best_idx = idx

        # Return best match if score > 0.7
        return best_idx if best_score > 0.7 else None

    def _run_tests(self, source: str) -> str:
        """Run pre-assigned test cases against the student's code.

        Returns test results text, or empty string if tests can't be run.
        Populates ``self._test_details`` with per-test pass/fail info for debug.
        """
        all_tests = self._all_test_cases
        if not all_tests:
            return ""

        self._test_details: list[dict] = []
        passed = 0
        failed = 0

        try:
            for tc in all_tests:
                script = source.rstrip() + "\n\n" + tc + "\n"
                result = subprocess.run(
                    ["python3", "-c", script],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    passed += 1
                    self._test_details.append({"test": tc, "status": "pass"})
                else:
                    failed += 1
                    err = (result.stderr or result.stdout).strip()
                    # Extract the last meaningful line (AssertionError, NameError, etc.)
                    err_lines = err.split("\n")
                    last = err_lines[-1].strip() if err_lines else str(result.returncode)
                    self._test_details.append({"test": tc, "status": "fail", "error": last})

            if failed == 0:
                return "✅ All tests passed"
            else:
                # Build a summary string for the LLM prompt
                lines = [f"{passed}/{len(all_tests)} passed, {failed} failed:"]
                for d in self._test_details:
                    if d["status"] == "pass":
                        lines.append(f"  ✅ {d['test']}")
                    else:
                        lines.append(f"  ❌ {d['test']}  →  {d.get('error', '')}")
                return "\n".join(lines)
        except Exception:
            return ""


# Singleton
_watchdog = SocraticWatchdog()

# ── Kokoro pipeline (lazy-load — model download on first use) ──────────

_kokoro_pipeline = None


def _get_kokoro_pipeline():
    """Return the cached Kokoro KPipeline, loading it on first call."""
    global _kokoro_pipeline
    if _kokoro_pipeline is None:
        from kokoro import KPipeline
        _kokoro_pipeline = KPipeline(lang_code="a")
    return _kokoro_pipeline

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
TTS_BACKEND = os.environ.get("SOCRATIC_TTS_BACKEND", "espeak")
# ^ "espeak" (default, local, ~0.02s warm, robotic), "kokoro" (local neural, ~3.8s),
#   or "edge-tts" (cloud neural, ~1s)
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


# ═══════════════════════════════════════════════════════════════════════════
#  CORE ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class SocraticWatchdog:
    """Watches notebook cell executions and speaks Socratic questions."""

    def __init__(self):
        self.task_description: str = ""
        """Explicit task set via ``%socratic_task``."""

        self.watch_all: bool = False
        """When True, every cell execution is analyzed automatically."""

        self.auto_detect_task: bool = False
        """When True, detect the task from the markdown cell above each code cell."""

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

    # ── Public API ──────────────────────────────────────────────────────

    @property
    def _all_test_cases(self) -> list[str]:
        """Visible + hidden tests combined (for execution only)."""
        return self.test_cases + self.hidden_test_cases

    def set_task(self, description: str) -> None:
        self.task_description = description.strip()
        self.auto_detect_task = False  # explicit task overrides auto-detect

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
        """
        import time
        t0 = time.monotonic()
        self._timings = {}

        if not source.strip():
            self._timings["total"] = 0.0
            return ""

        # ── Fast path: deterministic test check (no LLM) ────────────────
        if self._all_test_cases:
            test_result = self._run_tests(source)
            if test_result and "✅ All tests passed" in test_result:
                self._timings = {
                    "build_prompt": 0.0,
                    "llm_call":     0.0,
                    "parse":        0.0,
                    "total":        0.0,
                }
                return ""  # instant win — confetti, no API call
            # Tests failed — feed failure output to the LLM for better questions
            if test_result:
                if error:
                    error = error + "\n\n" + test_result
                else:
                    error = test_result

        t1 = time.monotonic()
        prompt = self._build_prompt(source, error)
        t2 = time.monotonic()

        raw = self._call_llm(prompt)
        t3 = time.monotonic()

        result = self._parse_response(raw)
        t4 = time.monotonic()

        self._timings = {
            "build_prompt": round(t2 - t1, 3),
            "llm_call":     round(t3 - t2, 3),
            "parse":        round(t4 - t3, 3),
            "total":        round(t4 - t1, 3),
        }

        if self._debug:
            print(self._format_timings())

        return result

    def _format_timings(self) -> str:
        """Return a one-line timing summary of the last analysis."""
        if not self._timings:
            return ""
        t = self._timings
        return (
            f"⏱  build={t['build_prompt']}s  "
            f"llm={t['llm_call']}s  "
            f"parse={t['parse']}s  "
            f"total={t['total']}s"
        )

    def speak(self, text: str) -> Optional["IPython.display.Audio"]:
        """Convert text to speech. Returns Audio widget or None on failure.

        Backend is selected by ``SOCRATIC_TTS_BACKEND`` env var (read on each call):
        - ``edge-tts`` (default) — Microsoft neural voices, cloud, ~3s
        - ``kokoro`` — local neural, ~3.8s, 82M model
        - ``espeak`` — local espeak-ng, robotic, ~0.03s, no network

        Records wall-clock time in ``self._tts_time``.
        """
        import time
        self._tts_time = None
        if not text.strip():
            return None
        t0 = time.monotonic()
        backend = os.environ.get("SOCRATIC_TTS_BACKEND", "edge-tts")
        if backend == "espeak":
            result = self._speak_espeak(text)
        elif backend == "kokoro":
            result = self._speak_kokoro(text)
        else:
            result = self._speak_edge(text)
        self._tts_time = round(time.monotonic() - t0, 3)
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
        self.auto_detect_task = False
        self.test_cases = []
        self.hidden_test_cases = []
        self._cached_notebook_path = ""
        self._cached_notebook_cells = []

    def generate_tests(self, task_description: str = "") -> list[str]:
        """Generate hidden test cases from the task description via LLM.

        Results are cached on disk (keyed by task description hash) so the
        LLM is called only once per task.  Returns the test case lines.
        """
        import hashlib

        task = (task_description or self.task_description).strip()
        if not task:
            print("🧠  No task set — can't generate tests. Use %socratic_task first.")
            return []

        cache_key = hashlib.sha256(task.encode()).hexdigest()[:16]
        self._tests_cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = self._tests_cache_dir / f"{cache_key}.json"

        # ── Cache hit ──
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text())
                self.hidden_test_cases = cached.get("tests", [])
                print(f"🧠  Loaded {len(self.hidden_test_cases)} cached hidden tests for this task.")
                return self.hidden_test_cases
            except Exception:
                pass  # corrupt cache → regenerate

        # ── Cache miss — ask LLM ──
        gen_prompt = textwrap.dedent(f"""\
            You are a test-case generator for a programming exercise.

            TASK: {task}

            Write 4-6 Python assert statements that thoroughly test a correct
            solution.  Cover edge cases, normal cases, and corner cases.
            Output ONLY the assert statements, one per line — no markdown,
            no explanations, no code fences.

            Example format:
            assert foo(0) == 0
            assert foo(-1) == 1
            assert foo(100) == 5050
        """)

        try:
            raw = self._call_llm_direct(gen_prompt)
            if raw == "[SILENT]":
                # Direct API failed, try hermes
                raw = self._call_llm_hermes(gen_prompt)
            if raw == "[SILENT]":
                print("⚠️  Could not generate tests (LLM unavailable).")
                return []
        except Exception:
            print("⚠️  Could not generate tests (LLM error).")
            return []

        # Parse: keep only assert lines
        tests = []
        for line in raw.split("\n"):
            stripped = line.strip()
            if stripped.startswith("assert ") and not stripped.startswith("assert ("):
                # Single-line assert
                tests.append(stripped)
            elif stripped.startswith("assert "):
                # Multi-line assert — already starts with assert, keep it
                tests.append(stripped)

        if not tests:
            print("⚠️  LLM returned no parseable assert statements.")
            return []

        # Cache
        cache_file.write_text(json.dumps({"task": task, "tests": tests}, indent=2))
        self.hidden_test_cases = tests
        print(f"🧠  Generated and cached {len(tests)} hidden tests for this task.")
        return tests

    def detect_task_from_notebook(self, current_source: str) -> Optional[str]:
        """Try to find the markdown cell just above the current code cell.

        Uses ``jupyter-mcp-cli`` if available (DIVE platform), otherwise
        returns ``None``.
        """
        try:
            cells = self._get_notebook_cells()
            if not cells:
                return None

            # Find which cell we're currently running
            current_idx = self._find_current_cell(cells, current_source)
            if current_idx is None or current_idx == 0:
                return None

            # Look at the cell directly above
            cell_above = cells[current_idx - 1]
            if cell_above.get("cell_type") == "markdown":
                text = "".join(cell_above.get("source", []))
                # Skip very short cells (titles, dividers)
                if len(text.strip()) > 30:
                    # Clean up: remove HTML comments, strip whitespace
                    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
                    return text.strip()
        except Exception:
            pass
        return None

    # ── Internals ───────────────────────────────────────────────────────

    def _resolve_task(self, source: str) -> str:
        """Return the best task description available for this cell.

        Priority: explicit task > auto-detected > empty.
        """
        if self.task_description:
            return self.task_description
        if self.auto_detect_task:
            detected = self.detect_task_from_notebook(source)
            if detected:
                return detected
        return ""

    def _build_prompt(self, source: str, error: str = "") -> str:
        parts = [SOCRATIC_RULES, ""]

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

    def _call_llm(self, prompt: str) -> str:
        """Call LLM to analyze student code.

        Priority: direct API (faster, more reliable) → hermes CLI fallback.
        Controlled by ``SOCRATIC_LLM_BACKEND`` env var:
        - ``direct`` (default) — calls DeepSeek/OpenAI API directly via HTTPS
        - ``hermes`` — uses ``hermes chat -q`` CLI
        """
        backend = os.environ.get("SOCRATIC_LLM_BACKEND", "direct")

        if backend == "hermes":
            # Hermes CLI first, direct API fallback
            result = self._call_llm_hermes(prompt)
            if result == "[SILENT]":
                return self._call_llm_direct(prompt)
            return result

        # Default: direct API — faster, no hermes overhead
        result = self._call_llm_direct(prompt)
        if result == "[SILENT]":
            # Direct API failed (no key, network error) — try hermes as fallback
            return self._call_llm_hermes(prompt)
        return result

    def _call_llm_hermes(self, prompt: str) -> str:
        """LLM via Hermes CLI (``hermes chat -q``)."""
        try:
            # Use HERMES_PROFILE env var, or default to 'dev'
            profile = os.environ.get("HERMES_PROFILE", "dev")
            result = subprocess.run(
                ["hermes", "chat", "-q", prompt, "--safe-mode", "-p", profile],
                capture_output=True, text=True, timeout=LLM_TIMEOUT,
            )
            return (result.stdout or result.stderr or "")
        except FileNotFoundError:
            return "[SILENT]"
        except subprocess.TimeoutExpired:
            return "[SILENT]"

    def _call_llm_direct(self, prompt: str) -> str:
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
                    {"role": "system", "content": SOCRATIC_RULES},
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
        # Hermes echoes the full prompt in its output, which includes [SILENT]
        # in the Socratic rules. Only look at the actual response body.
        # Split on the "───" separator that hermes prints before the response.
        if "───" in raw:
            raw = raw.split("───", 1)[1]

        if "[SILENT]" in raw.upper():
            return ""
        cleaned = re.sub(r"[╭─╮│╰─╯┌┐└┘├┤┬┴┼━│☰●]", "", raw)
        lines = []
        for line in cleaned.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            if any(skip in stripped.lower() for skip in
                   ["resume this session", "session:", "duration:",
                    "messages:", "tool call", "running", "thinking",
                    "hermes", "system", "tool", "safe mode", "notice"]):
                continue
            lines.append(stripped)
        if not lines:
            return ""
        return lines[-1].strip().strip('"').strip("'")

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
        """
        current_normalized = current_source.strip()
        best_idx = None
        best_score = 0

        for idx, cell in enumerate(cells):
            if cell.get("cell_type") != "code":
                continue
            cell_source = "".join(cell.get("source", [])).strip()
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
        """
        all_tests = self._all_test_cases
        if not all_tests:
            return ""

        try:
            # Build a test script: student's code + test cases
            script = source.rstrip() + "\n\n"
            for tc in all_tests:
                script += tc + "\n"
            script += "\nprint('ALL_TESTS_PASSED')\n"

            result = subprocess.run(
                ["python3", "-c", script],
                capture_output=True, text=True, timeout=10,
            )
            if "ALL_TESTS_PASSED" in result.stdout:
                return "✅ All tests passed"
            else:
                # Return the error output
                err = (result.stderr or result.stdout).strip()
                if err:
                    # Truncate long tracebacks
                    lines = err.split("\n")
                    return "\n".join(lines[-8:])
                return ""
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

"""
socratic_watchdog.magics — IPython magics and hooks for Socratic Watchdog.

Loaded automatically by ``%load_ext socratic_watchdog`` via ``__init__.py``.

Architecture
------------
The module has three layers:

1. **View helpers** — ``_show_thinking()``, ``_resolve_thinking()``,
   ``_deliver()``, ``_show_confetti()``.  Render HTML boxes and animations.

2. **SocraticMagics class** — all ``%socratic_*`` line magics and the
   ``%%socratic`` cell magic.  Each magic calls into ``_watchdog`` methods.

3. **Post-run cell hook** — ``_post_run_cell_hook()`` registered via
   ``ipython.events.register('post_run_cell', ...)``.  Fires after every
   cell when ``%socratic_watch on`` is active.
"""

from __future__ import annotations

import glob
import json
import os
import random
import re
import textwrap
import time

from IPython import get_ipython
from IPython.core.magic import Magics, magics_class, line_magic, cell_magic
from IPython.display import display as ipy_display, HTML

from ._core import _watchdog, DEFAULT_TTS_BACKEND, _task_markdown_above

# ── Shared helpers ────────────────────────────────────────────────────

def _print_debug_table(watchdog) -> None:
    """Print a formatted timing table for debug mode.

    Consolidates auto-detect, cache, cell, LLM, and TTS timings into
    a single aligned table instead of scattered one-liners.
    """
    # Three sources of timing data, all populated by the last analyze() call:
    analyze_timings = watchdog._timings           # cell run, LLM call, TTS deliver
    gen_timings = watchdog._generate_timings       # test cache read / LLM generation
    auto_detect_secs = watchdog._auto_detect_time  # markdown-above scan (or None)

    rows = []  # each row is (label, seconds_or_None, note) — rendered below

    def add_row(label: str, seconds: float | None, note: str = "") -> None:
        rows.append((label, seconds, note))

    # ── Where the tests came from (disk cache vs. fresh LLM generation) ──
    if gen_timings and gen_timings.get("total", 0) > 0:
        if "read" in gen_timings:
            test_count = len(watchdog.hidden_test_cases)
            add_row("cache · read", gen_timings.get("read"), f"→ {test_count} tests")
        elif "llm" in gen_timings:
            add_row("generate · LLM", gen_timings.get("llm"), "fresh")

    # ── Auto-detect (always fires, always ~0.004s — too small to list) ──

    # ── Running the student's cell ──
    if "run_cell" in analyze_timings:
        add_row("cell execution", analyze_timings["run_cell"], "")

    # ── LLM analysis: either a real call, or a note on why it was skipped ──
    llm_time = analyze_timings.get("llm_call", 0)
    if llm_time > 0:
        add_row("LLM analysis", llm_time, "")
    elif len(watchdog._all_test_cases) > 0:
        # Tests decided the outcome, so the LLM was skipped — say which way.
        details = getattr(watchdog, "_test_details", [])
        if details and any(d["status"] == "fail" for d in details):
            add_row("LLM analysis", None, "✗ tests failed")
        else:
            add_row("LLM analysis", None, "⚡ all pass")
    else:
        add_row("LLM analysis", None, "⚡ no tests")

    # ── Text-to-speech ──
    tts_time = analyze_timings.get("deliver", 0)
    if tts_time > 0:
        backend = os.environ.get("SOCRATIC_TTS_BACKEND", DEFAULT_TTS_BACKEND)
        add_row("TTS", tts_time, backend)

    # ── Grand total: end-to-end plus the two phases measured separately ──
    total = analyze_timings.get("end_to_end", 0)
    if auto_detect_secs is not None:
        total += auto_detect_secs
    if gen_timings and gen_timings.get("total", 0) > 0:
        total += gen_timings["total"]
    total = round(total, 4)

    # ── Render the box-drawing table ──
    box_width = 54
    top = "┌─ Socratic Debug " + "─" * (box_width - 19) + "┐"
    separator = "├" + "─" * (box_width - 2) + "┤"
    bottom = "└" + "─" * (box_width - 2) + "┘"

    lines = [top]
    for label, seconds, note in rows:
        left = f"│ {label:<24}"
        right = f"{seconds:.4f}s" if isinstance(seconds, (int, float)) and seconds > 0 else "       —"
        if note:
            right = f"{right}  {note}"
        padding = box_width - 2 - len(left) - len(right)
        lines.append(left + " " * max(1, padding) + right + " │")

    lines.append(separator)
    left = f"│ {'TOTAL':<24}"
    right = f"{total:.4f}s"
    padding = box_width - 2 - len(left) - len(right) - 2
    lines.append(left + " " * max(1, padding) + right + "  │")
    lines.append(bottom)

    print("\n".join(lines))


def _announce_test_source(
    provenance: str | None, count: int,
    *, has_task: bool, has_api_key: bool,
) -> None:
    """Print ONE universal line telling the user where this cell's tests came
    from — or why there are none.

    ``provenance`` is one of ``"cache"``, ``"cell"``, ``"llm"`` (or ``None``
    when no tests were resolved).  The remaining states are derived from
    whether a task was found and whether an LLM is reachable.
    """
    if provenance == "cache":
        print(f"🧪  Tests: {count} loaded from cache")
    elif provenance == "cell":
        print(f"🧪  Tests: {count} read from the cell below  →  cached")
    elif provenance == "llm":
        print(f"🧪  Tests: {count} generated by the LLM  →  cached")
    elif not has_task:
        print("🧪  Tests: no task detected above — nothing to verify against")
    elif has_api_key:
        print("🧪  Tests: none generated — Socrates will judge via the LLM")
    else:
        print("🧪  Tests: none, and no LLM available — can't verify this cell "
              "(set DEEPSEEK_API_KEY, or add a #Tests cell below)")


def _show_thinking():
    """Show a "Socrates is thinking..." indicator while the LLM works.

    Returns a display handle.  The caller must pass it to
    ``_resolve_thinking(handle, question)`` when analysis completes.

    Uses a nanosecond-timestamp display_id so every cell gets its own
    isolated handle — no cross-cell output bleeding.
    """
    return ipy_display(
        HTML("<i>🏛️  Socrates is thinking…</i>"),
        display_id=f"socratic-thinking-{__import__('time').monotonic_ns()}",
    )


# ── Praise bank ────────────────────────────────────────────────────────

_SOCRATIC_PRAISES = [
    "Good job, my friend. Carry on.",
    "Well done! You are thinking clearly.",
    "Excellent. You understand this well.",
    "That is correct — you are on the path of wisdom.",
    "Nicely done. Your reasoning is sound.",
    "You have grasped this beautifully.",
    "Right you are! The truth shines through your code.",
    "Splendid work. I see a sharp mind at work.",
    "Perfect. You have honored the logic.",
    "Correct. You are learning the art of thinking.",
    "Bravo! The code speaks truth.",
    "You have done well, my curious friend.",
    "That is precisely right. Keep this clarity.",
    "A fine solution. Your mind is fertile ground.",
    "Yes! You are walking the path of reason.",
    "Marvelous. The logic flows like a clear stream.",
    "You have found the way. Well reasoned.",
    "Excellent craftsmanship. The code is sound.",
    "Right again. Your understanding deepens.",
    "Well argued. The code proves your thinking.",
    "You see the truth now. Well done.",
    "A worthy effort — and correct at that.",
    "The light of understanding shines upon you.",
    "Your logic is unassailable. Good work.",
    "Yes, that is the way. You are learning well.",
    "Correct and elegant. I am impressed.",
    "You have pierced the veil. Well done.",
    "A clear mind produces clear code. Excellent.",
    "That is the answer of a true thinker.",
    "Well reasoned, my friend. You are growing.",
    "The code does not lie — and it says you are right.",
    "You have tamed the problem. Good work.",
    "Flawless. The logic holds true.",
    "You have built something correct. Be proud.",
    "That is proper thinking. Well executed.",
    "A tidy solution. Your mind is disciplined.",
    "Right on the mark. The truth is yours.",
    "You see the pattern now. Excellent.",
    "Correct. The path of wisdom is long but you walk it well.",
    "Well played. The code is your ally today.",
    "You have earned my respect with that one.",
    "The logic is crystalline. Good job.",
    "Yes! That is the shape of truth.",
    "Your code sings with correctness.",
    "A fine piece of reasoning. Well done.",
    "You are becoming a true craftsman.",
    "The answer is correct and the method is sound.",
    "You have pleased the gods of logic today.",
    "Spot on. Your thinking is sharp.",
    "Wise choice. The code reflects clarity.",
    "You have passed this trial. Well done.",
    "Elegant and correct. A rare combination.",
    "The student becomes the master. Good work.",
    "Your mind is a well-tuned instrument.",
    "Correct. You honor the craft of coding.",
    "That is the work of a clear thinker.",
    "You have solved it with grace. Well done.",
    "The logic holds. You are on solid ground.",
    "Right. You are building good mental habits.",
    "A beautiful solution. Simple and true.",
    "You have found clarity. Hold onto it.",
    "Well deduced. Your reasoning is admirable.",
    "The code is correct and the thinking is pure.",
    "You make it look easy. Good work.",
    "That is wisdom applied. Well done.",
    "Correct. Every line speaks of understanding.",
    "You are thinking like a true philosopher.",
    "The problem bows to your logic. Excellent.",
    "Right again. Consistency is the mark of mastery.",
    "A sound mind writes sound code. Well done.",
    "You have crossed the river of confusion.",
    "The truth is on your side today.",
    "Well done. You have sharpened your mind.",
    "Correct. Each victory makes you stronger.",
    "You have reasoned well. I am pleased.",
    "That is the way of the wise coder.",
    "Your logic is a fortress. Good job.",
    "Yes! The code and the truth are one.",
    "You have found the golden thread. Well done.",
    "A correct solution from a clear mind.",
    "You are learning to see. That is the highest skill.",
    "Well crafted. The code reflects deep understanding.",
    "Right. You are building a cathedral of knowledge.",
    "The problem is solved. The mind is at peace.",
    "Correct. You walk in the footsteps of great thinkers.",
    "A worthy answer. Your journey continues well.",
    "You have earned this victory. Well done.",
    "The code is a mirror of right thinking.",
    "Excellent. Each step forward is a step toward wisdom.",
    "You have silenced the doubt. Good work.",
    "That is the answer of one who truly understands.",
    "Right. The path ahead is bright.",
    "A flawless execution. You are on fire today.",
    "Correct. The universe rewards clear thinking.",
    "You have bent the problem to your will. Well done.",
    "The logic is a beautiful thing. Good job.",
    "Yes. You are becoming the coder you wish to be.",
    "Well done. The journey of a thousand miles continues.",
    "A perfect note in the symphony of code.",
    "Correct. Your mind is a garden well tended.",
    "You have proven yourself once more. Excellent.",
]


def _resolve_thinking(handle, question: str):
    """Replace thinking indicator with final result.

    Three paths:

    * **question given** → clear the thinking indicator so ``_deliver()``
      can show its own styled amber question box.
    * **no question** (silent) → code is correct!  Replace the indicator
      with a green praise box + confetti + TTS.
    * **[LLM_UNAVAILABLE]** → no API key configured, can't verify correctness.
      Show a warning instead of false praise.
    """
    try:
        if question == "[LLM_UNAVAILABLE]":
            handle.update(HTML(
                "<div style='"
                "background:#fef3c7; border-left:4px solid #f59e0b;"
                "padding:12px 16px; margin:8px 0; border-radius:4px;"
                "font-size:15px; line-height:1.5;'"
                ">"
                "<strong>⚠️  Socrates is offline</strong><br>"
                "No LLM API key configured — can't verify your code.<br>"
                "Set <code>DEEPSEEK_API_KEY</code> in <code>.env</code> "
                "or add a <code>#Test cases</code> cell below for local verification."
                "</div>"
            ))
        elif question == "[TESTS_FAILED]":
            # Deterministic verdict: the hidden tests failed.  No LLM was
            # available to phrase a Socratic question, so we report the
            # failure plainly instead of praising broken code.
            handle.update(HTML(
                "<div style='"
                "background:#fee2e2; border-left:4px solid #ef4444;"
                "padding:12px 16px; margin:8px 0; border-radius:4px;"
                "font-size:15px; line-height:1.5;'"
                ">"
                "<strong>❌  Not passing yet</strong><br>"
                "Your code didn't pass the hidden tests.<br>"
                "Set an LLM API key for a guiding question, or check your logic."
                "</div>"
            ))
        elif question:
            # Clear — _deliver() handles the question display
            handle.update(HTML(""))
        else:
            praise = random.choice(_SOCRATIC_PRAISES)
            handle.update(HTML(
                "<div style='"
                "background:#d1fae5; border-left:4px solid #10b981;"
                "padding:12px 16px; margin:8px 0; border-radius:4px;"
                "font-size:15px; line-height:1.5;'"
                ">"
                f"<strong>🏛️  Socrates says:</strong><br>{praise}"
                "</div>"
            ))
            # Confetti!
            _show_confetti()
            # Also speak the praise (if audio is on)
            if _audio_on():
                try:
                    audio = _watchdog.speak(praise)
                    if audio is not None:
                        ipy_display(audio)
                except Exception:
                    pass
    except Exception:
        pass


def _show_confetti():
    """Shoot confetti!  A lightweight canvas animation (~3 s)."""
    ipy_display(HTML("""\
<canvas id="socratic-confetti" style="
    position:fixed; top:0; left:0; width:100vw; height:100vh;
    pointer-events:none; z-index:9999;
"></canvas>
<script>
(function(){
  var c=document.getElementById("socratic-confetti");
  var ctx=c.getContext("2d");
  c.width=window.innerWidth; c.height=window.innerHeight;
  var particles=[];
  var colors=["#f59e0b","#ef4444","#3b82f6","#10b981","#8b5cf6","#ec4899","#f97316"];
  for(var i=0;i<120;i++){
    particles.push({
      x:Math.random()*c.width, y:Math.random()*c.height*-0.5,
      w:4+Math.random()*8, h:4+Math.random()*12,
      color:colors[Math.floor(Math.random()*colors.length)],
      vx:(Math.random()-0.5)*3, vy:1+Math.random()*4,
      rot:Math.random()*360, rv:(Math.random()-0.5)*8
    });
  }
  var start=Date.now();
  function draw(){
    ctx.clearRect(0,0,c.width,c.height);
    var elapsed=(Date.now()-start)/1000;
    for(var i=0;i<particles.length;i++){
      var p=particles[i];
      ctx.save();
      ctx.translate(p.x,p.y);
      ctx.rotate(p.rot*Math.PI/180);
      ctx.fillStyle=p.color;
      ctx.fillRect(-p.w/2,-p.h/2,p.w,p.h);
      ctx.restore();
      p.x+=p.vx; p.y+=p.vy;
      p.vy+=0.03; p.rot+=p.rv;
    }
    if(elapsed<3.5) requestAnimationFrame(draw);
    else { c.remove(); }
  }
  draw();
})();
</script>"""))


def _extract_error(result) -> str:
    """Return a single-line traceback string from a cell execution result."""
    if result.error_in_exec:
        import traceback
        return "".join(traceback.format_exception_only(
            type(result.error_in_exec), result.error_in_exec
        ))
    if result.error_before_exec:
        import traceback
        return "".join(traceback.format_exception_only(
            type(result.error_before_exec), result.error_before_exec
        ))
    return ""


def _extract_error_from_info(info) -> str:
    """Same as _extract_error but for IPython post_run_cell event info objects."""
    if info.error_in_exec:
        import traceback
        return "".join(traceback.format_exception_only(
            type(info.error_in_exec), info.error_in_exec
        ))
    if info.error_before_exec:
        import traceback
        return "".join(traceback.format_exception_only(
            type(info.error_before_exec), info.error_before_exec
        ))
    return ""


def _audio_on() -> bool:
    """Check if audio output is enabled (SOCRATIC_AUDIO env var)."""
    return os.environ.get("SOCRATIC_AUDIO", "on").strip().lower() != "off"


def _deliver(question: str) -> None:
    """Display the Socratic question as a styled subtitle + TTS audio.

    Shows a highlighted subtitle box so non-native speakers can read
    along with the audio.
    """
    # Styled subtitle box — easy to read, visually distinct
    ipy_display(HTML(
        "<div style='"
        "background:#fef3c7; border-left:4px solid #f59e0b;"
        "padding:12px 16px; margin:8px 0; border-radius:4px;"
        "font-size:15px; line-height:1.5;'"
        ">"
        "<strong>🏛️  Socrates asks:</strong><br>"
        f"{question}"
        "</div>"
    ))
    if not _audio_on():
        return
    try:
        audio = _watchdog.speak(question)
        if audio is not None:
            ipy_display(audio)
    except Exception:
        pass


def _try_auto_detect(source: str) -> tuple[str | None, list[str] | None]:
    """Try to auto-detect the task from the markdown cell above.

    Returns (task_text, tests_or_none) where:
    - ``task_text`` is the markdown content (or None if not found)
    - ``tests_or_none`` is:
        * ``None`` → no ``#Test cases`` cell below → caller should auto-generate
        * ``[]``   → could not find notebook (no-op)
        * ``[list]`` → parsed test assertions from the cell below

    Tries jupyter-mcp-cli first, then scans .ipynb files in cwd.
    """
    # Try live notebook cells (Colab frontend, or jupyter-mcp-cli / DIVE).
    # Must scan the cell below for #Test cases here too — otherwise Colab
    # (which has no local .ipynb for the glob fallback below) always misses
    # them and needlessly auto-generates tests with the LLM.
    try:
        cells = _watchdog._get_notebook_cells()
        if cells:
            idx = _watchdog._find_current_cell(cells, source)
            if idx is not None:
                task = _task_markdown_above(cells, idx)
                if task:
                    return (task, _extract_tests_from_cell_below(cells, idx))
    except Exception:
        pass

    # Fallback: scan notebook files in cwd for the current cell
    # IPython strips the cell magic line (%%socratic) from the `cell`
    # parameter, but the notebook file stores the FULL source.  We must
    # strip %% and % magic lines from the notebook source before comparing.
    source_stripped = source.strip()
    try:
        for nb_path in glob.glob('*.ipynb'):
            nb = json.load(open(nb_path))
            cells = nb.get('cells', [])
            for idx, cell in enumerate(cells):
                if cell.get('cell_type') != 'code':
                    continue
                cell_src_raw = ''.join(cell.get('source', []))
                # Strip cell magics (%%socratic, %%time, etc.) and line
                # magics (%some_magic) from the leading lines so the
                # remaining code matches what IPython hands the magic.
                cell_src = re.sub(
                    r'^(%%.*|%.*)\n', '', cell_src_raw, flags=re.MULTILINE
                ).strip()
                if cell_src != source_stripped:
                    continue
                # Found our cell — reuse the shared upward-scan for the task
                # markdown.  None → no task above; break (not return) so we
                # still check other notebooks with the same cell content.
                task = _task_markdown_above(cells, idx)
                if task is None:
                    break
                tests = _extract_tests_from_cell_below(cells, idx)
                return (task, tests)
    except Exception:
        pass
    return (None, None)


def _extract_tests_from_cell_below(
    cells: list[dict], current_idx: int
) -> list[str] | None:
    """Scan the cell below the current code cell for ``#Test cases``.

    Returns:
    - ``None`` → cell below does NOT contain ``#Test cases`` → auto-generate
    - ``[list]`` → parsed assert lines (might be empty if the cell is just
      a heading with no actual test code)
    """
    # Look at the cell directly below
    if current_idx + 1 >= len(cells):
        return None  # no cell below → auto-generate

    below = cells[current_idx + 1]
    if below.get('cell_type') != 'code':
        return None  # cell below isn't code → auto-generate

    text = ''.join(below.get('source', [])).strip()
    if not text:
        return None

    # Does it contain a test-cell marker?  Supports: #Test cases, #Tests, #test_cases
    markers = ['#test cases', '#tests', '#test_cases']
    if not any(m in text.lower() for m in markers):
        return None  # no marker → auto-generate

    # Parse assert statements from the cell
    tests = []
    for line in text.split('\n'):
        stripped = line.strip()
        # Skip comments and blank lines
        if stripped.startswith('#') or not stripped:
            continue
        if stripped.startswith('assert '):
            tests.append(stripped)

    return tests  # may be empty if no parseable asserts


# ── Debounce (for auto-watch mode) ────────────────────────────────────

_LAST_ANALYSIS_TIME: float = 0.0
# Debounce window for auto-watch mode. Override with SOCRATIC_DEBOUNCE (seconds).
try:
    _DEBOUNCE_SECONDS: float = float(os.environ.get("SOCRATIC_DEBOUNCE", "3.0"))
except ValueError:
    _DEBOUNCE_SECONDS = 3.0


def _should_analyze() -> bool:
    global _LAST_ANALYSIS_TIME
    now = time.monotonic()
    if now - _LAST_ANALYSIS_TIME >= _DEBOUNCE_SECONDS:
        _LAST_ANALYSIS_TIME = now
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
#  MAGICS
# ═══════════════════════════════════════════════════════════════════════════

@magics_class
class SocraticMagics(Magics):
    """%%socratic, %socratic_task, %socratic_generate_tests, %socratic_watch, and friends."""

    # ── socratic_task ──────────────────────────────────────────────────

    @line_magic
    def socratic_task(self, line: str) -> None:
        """Set the task description. Use 'auto' to detect from markdown above.

        Usage::

            %socratic_task Write a function that sorts a list
            %socratic_task auto           # detect from notebook markdown
            %socratic_task clear          # remove the task
            %socratic_task                # show current task
        """
        arg = line.strip().lower()
        if not arg:
            if _watchdog.task_description:
                print(f"🧠 Task: “{_watchdog.task_description}”")
            else:
                print("🧠 No explicit task — auto-detecting from the markdown cell above each %%socratic cell.")
            print("   Set with:  %socratic_task <goal>  or  %socratic_task auto")
            return

        if arg == "auto":
            _watchdog.task_description = ""
            _watchdog._cached_notebook_cells = []  # force re-scan
            print("🧠  Auto-detect enabled. I'll read the task from the markdown cell above each code cell.")
        elif arg == "clear":
            _watchdog.reset_context()
            print("🧠  Task cleared.")
        else:
            _watchdog.set_task(line)  # explicit task (disables auto-detect)
            print(f"🧠  Task set: “{line}”")


    # ── socratic_generate_tests ────────────────────────────────────────

    @line_magic
    def socratic_generate_tests(self, line: str) -> None:
        """Auto-generate hidden test cases from the task description.

        Calls the LLM once, caches results on disk (keyed by task hash),
        and loads them as hidden tests.  Subsequent runs are instant.

        Usage::

            %socratic_task Write a function that checks if a number is even
            %socratic_generate_tests
        """
        tests = _watchdog.generate_tests()
        if tests:
            print("🧠  These tests will run silently — students only see confetti or questions.")
        else:
            print("🧠  No tests generated.")

    # ── socratic_watch ─────────────────────────────────────────────────

    @line_magic
    def socratic_watch(self, line: str) -> None:
        """Toggle automatic watching of every cell.

        Usage::

            %socratic_watch on
            %socratic_watch off
        """
        mode = line.strip().lower()
        if mode == "on":
            _watchdog.watch_all = True
            print("🧠  Socrates is now watching every cell. Use %socratic_off to stop.")
        elif mode == "off":
            _watchdog.watch_all = False
            print("🧠  Socrates is no longer watching.")
        else:
            state = "on" if _watchdog.watch_all else "off"
            print(f"🧠  Auto-watch: {state}  |  Use on/off to toggle")

    @line_magic
    def socratic_off(self, line: str) -> None:
        """Quick alias to stop watching."""
        _watchdog.watch_all = False
        print("🧠  Watcher off.")

    @line_magic
    def socratic_reset(self, line: str) -> None:
        """Reset Socrates's task context."""
        _watchdog.reset_context()
        print("🧠  Context reset. Socrates sees a clean slate.")

    @line_magic
    def socratic_stats(self, line: str) -> None:
        """Show timing breakdown from the last analysis.

        Reports wall-clock time for each step: build_prompt, llm_call,
        parse_response, and total. Also shows TTS backend + time.
        """
        t = _watchdog._timings
        if not t or not t.get("total"):
            print("⏱  No analysis run yet. Use %%socratic on a cell first.")
            return

        print(f"⏱  **Last analysis timings**")
        print(f"   run_cell     : {t.get('run_cell', '?'):>8}s")
        print(f"   build_prompt : {t.get('build_prompt', '?'):>8}s")
        print(f"   llm_call     : {t.get('llm_call', '?'):>8}s")
        print(f"   parse        : {t.get('parse', '?'):>8}s")
        print(f"   tts+display  : {t.get('deliver', '?'):>8}s")
        print(f"   ─────────────────────")
        print(f"   end-to-end   : {t.get('end_to_end', '?'):>8}s")

        # Show TTS timing if available
        if _watchdog._tts_time is not None:
            backend = os.environ.get("SOCRATIC_TTS_BACKEND", DEFAULT_TTS_BACKEND)
            print(f"   tts ({backend:<8}): {_watchdog._tts_time:>8.3f}s")

    @line_magic
    def socratic_audio(self, line: str) -> None:
        """Toggle TTS audio on or off.

        Usage::

            %socratic_audio on
            %socratic_audio off
            %socratic_audio       # show current state
        """
        mode = line.strip().lower()
        if mode in ("on", "off"):
            os.environ["SOCRATIC_AUDIO"] = mode
            print(f"🔇  Audio {'ON' if mode == 'on' else 'OFF'}")
        else:
            current = os.environ.get("SOCRATIC_AUDIO", "on")
            print(f"🔇  Audio: {current}  |  Use on/off to toggle")

    @line_magic
    def socratic_model(self, line: str) -> None:
        """Select the LLM model from a curated list (or type a custom one).

        Usage::

            %socratic_model          # show numbered list + current model
            %socratic_model 2        # pick #2 from the list
            %socratic_model deepseek-chat   # custom model name (keeps current base URL)
        """
        line = line.split("#")[0]
        parts = line.strip().split()

        # ── Curated providers ──
        known = [
            ("DeepSeek Chat",     "deepseek-chat",     "https://api.deepseek.com"),
            ("DeepSeek Reasoner", "deepseek-reasoner", "https://api.deepseek.com"),
            ("GPT-4o",            "gpt-4o",            "https://api.openai.com/v1"),
            ("GPT-4o Mini",       "gpt-4o-mini",       "https://api.openai.com/v1"),
            ("Claude 3.5 Sonnet", "claude-3-5-sonnet-20241022", "https://api.anthropic.com/v1"),
            ("Claude 3 Haiku",    "claude-3-haiku-20240307",     "https://api.anthropic.com/v1"),
        ]

        if not parts:
            # Show picker + current
            current_model = os.environ.get("SOCRATIC_LLM_MODEL", "deepseek-chat")
            current_base  = os.environ.get("SOCRATIC_LLM_BASE_URL", "https://api.deepseek.com")
            print("🤖  Available models:\n")
            for idx, (name, model, base) in enumerate(known, 1):
                marker = " ← current" if model == current_model else ""
                print(f"    {idx}. {name}  ({model}){marker}")
            print(f"\n    Current: {current_model}")
            print(f"    Base:    {current_base}")
            print(f"\n    Pick one:  %socratic_model <number>")
            print(f"    Or custom: %socratic_model <model-name>")
            return

        # ── Try number pick ──
        try:
            idx = int(parts[0])
            if 1 <= idx <= len(known):
                name, model, base_url = known[idx - 1]
                os.environ["SOCRATIC_LLM_MODEL"] = model
                os.environ["SOCRATIC_LLM_BASE_URL"] = base_url
                print(f"🤖  {name}  ({model})")
                print(f"    {base_url}")
                return
        except ValueError:
            pass

        # ── Try name match ──
        for name, model, base_url in known:
            if parts[0].lower() in model.lower() or parts[0].lower() in name.lower():
                os.environ["SOCRATIC_LLM_MODEL"] = model
                os.environ["SOCRATIC_LLM_BASE_URL"] = base_url
                print(f"🤖  {name}  ({model})")
                print(f"    {base_url}")
                return

        # ── Custom model (keep current base URL) ──
        model = parts[0]
        os.environ["SOCRATIC_LLM_MODEL"] = model
        print(f"🤖  Custom model: {model}")

    @line_magic
    def socratic_debug(self, line: str) -> None:
        """Toggle debug mode — shows inline timing and trace info.

        Usage::

            %socratic_debug on
            %socratic_debug off
            %socratic_debug      # show current state

        When on, every cell prints:
        - 🧪  test pass/fail counts
        - 🤖  which LLM model was called + how long
        - 🔊  which TTS backend ran + how long
        - ⏱   end-to-end timing summary
        """
        mode = line.strip().lower()
        if mode in ("on", "off"):
            _watchdog._debug = (mode == "on")
            os.environ["SOCRATIC_DEBUG"] = "1" if mode == "on" else ""
            print(f"🐛  Debug {'ON' if _watchdog._debug else 'OFF'}")
        else:
            print(f"🐛  Debug: {'on' if _watchdog._debug else 'off'}  |  Use on/off")

    @line_magic
    def socratic_style(self, line: str) -> None:
        """Switch Socrates' response style.

        Usage::

            %socratic_style brief     # direct questions, no preambles
            %socratic_style verbose   # patient, playful mentor tone (default)
            %socratic_style           # show current style
        """
        mode = line.strip().lower()
        if mode in ("brief", "verbose"):
            _watchdog.style = mode
            print(f"🏛️  Style: {mode}")
        else:
            print(f"🏛️  Style: {_watchdog.style}  |  Use brief/verbose")

    @line_magic
    def socratic_explore(self, line: str) -> None:
        """Toggle exploration mode — Socrates encourages free experimentation
        when no task is set, instead of asking what the student is trying to do.

        Usage::

            %socratic_explore on
            %socratic_explore off
            %socratic_explore      # show current state
        """
        mode = line.strip().lower()
        if mode in ("on", "off"):
            _watchdog.exploration_mode = (mode == "on")
            print(f"🔍  Exploration mode {'ON' if _watchdog.exploration_mode else 'OFF'}")
        else:
            state = "on" if _watchdog.exploration_mode else "off"
            print(f"🔍  Exploration mode: {state}  |  Use on/off")

    @line_magic
    def socratic_auto_tests(self, line: str) -> None:
        """Toggle auto-generate: every ``%socratic_task`` automatically runs
        test generation + caching behind the scenes.

        Usage::

            %socratic_auto_tests on
            %socratic_auto_tests off
            %socratic_auto_tests      # show current state

        When on, setting a task like ``%socratic_task Write a sort function``
        immediately calls the LLM to generate tests and caches them.
        Subsequent cells get instant test validation — no manual
        ``%socratic_generate_tests`` needed.
        """
        mode = line.strip().lower()
        if mode in ("on", "off"):
            _watchdog.auto_generate_tests = (mode == "on")
            print(f"🧪  Auto-generate tests {'ON' if _watchdog.auto_generate_tests else 'OFF'}")
        else:
            state = "on" if _watchdog.auto_generate_tests else "off"
            print(f"🧪  Auto-generate tests: {state}  |  Use on/off")

    @line_magic
    def socratic_clear_cache(self, line: str) -> None:
        """Clear the generated-tests cache so the next ``%socratic_generate_tests``
        calls the LLM fresh (useful for demos).

        Usage::

            %socratic_clear_cache
        """
        import shutil
        if _watchdog._tests_cache_dir.exists():
            count = len(list(_watchdog._tests_cache_dir.glob("*.json")))
            shutil.rmtree(_watchdog._tests_cache_dir)
            _watchdog._tests_cache_dir.mkdir(parents=True, exist_ok=True)
            print(f"🧠  Cleared {count} cached test file(s).  Next generate_tests will call LLM.")
        else:
            print("🧠  No cache to clear.")

    @line_magic
    def socratic_cache(self, line: str) -> None:
        """Inspect the generated-tests cache.

        Usage::

            %socratic_cache              # list all cached tasks
            %socratic_cache show         # show cached tests for the current task
            %socratic_cache show <hash>  # show cached tests for a specific hash
        """
        arg = line.strip().lower()

        if arg == "show":
            # Show tests for the current task
            entry = _watchdog.peek_cache()
            if entry is None:
                if _watchdog.task_description:
                    print(f"🧠  No cached tests for: “{_watchdog.task_description[:80]}...”")
                else:
                    print("🧠  No task set. Use %%socratic or %socratic_task first.")
                return
            self._print_cache_entry(entry)
            return

        if arg.startswith("show "):
            # Show tests for a specific hash
            h = arg.split("show ")[1].strip()
            entries = _watchdog.list_cache()
            for e in entries:
                if e["hash"].startswith(h):
                    self._print_cache_entry(e)
                    return
            print(f"🧠  No cache entry with hash starting with “{h}”.")
            return

        # Default: list all cached entries
        entries = _watchdog.list_cache()
        if not entries:
            print("🧠  No cached tests.  Run %%socratic with a task to auto-generate.")
            return

        print(f"🧠  {len(entries)} cached test file(s):\n")
        # Pre-compute current task hash for the ← current marker
        current_hash = ""
        if _watchdog.task_description:
            import hashlib
            current_hash = hashlib.sha256(
                _watchdog.task_description.encode()
            ).hexdigest()[:16]

        for e in entries:
            preview = e["task"] if len(e["task"]) <= 70 else e["task"][:67] + "..."
            age = (
                f"{e['age_seconds']}s ago" if e["age_seconds"] < 120
                else f"{e['age_seconds'] // 60}m ago" if e["age_seconds"] < 7200
                else f"{e['age_seconds'] // 3600}h ago"
            )
            marker = " ← current" if e["hash"] == current_hash else ""
            print(f"    {e['hash']}  {e['test_count']:>2} tests  {age}  {preview}{marker}")

        print(f"\n    %socratic_cache show        — show tests for current task")
        print(f"    %socratic_cache show <hash> — show tests for a specific hash")
        print(f"    %socratic_clear_cache       — clear all cached tests")

    @staticmethod
    def _print_cache_entry(entry: dict) -> None:
        """Print a single cache entry with its tests."""
        age = entry["age_seconds"]
        if age < 120:
            age_str = f"{age}s ago"
        elif age < 7200:
            age_str = f"{age // 60}m ago"
        else:
            age_str = f"{age // 3600}h ago"

        print(f"🧠  Task: {entry['task'][:120]}")
        print(f"    Hash:  {entry['hash']}")
        print(f"    Age:   {age_str}")
        print(f"    Tests: {len(entry['tests'])}")
        if entry["tests"]:
            print()
            for tc in entry["tests"]:
                print(f"    {tc}")
        else:
            print("    (no test cases)")

    @line_magic
    def socratic_help(self, line: str) -> None:
        """Show usage help."""
        print(textwrap.dedent("""\
            🏛️  **Socratic Watchdog — Commands**
            ─────────────────────────────────────
            ⚠️   %%socratic MUST be the first line of a cell!
                (no comments or code before it)
            ─────────────────────────────────────
            %%socratic          Run a cell with Socratic analysis
            %socratic_task     Set the goal (or 'auto' / 'clear')
            %socratic_generate_tests  LLM writes hidden tests from the task
            %socratic_watch    Watch every cell (on/off)
            %socratic_off      Stop watching
            %socratic_reset    Clear Socrates's context
            %socratic_stats    Show timing breakdown of last analysis
            %socratic_audio    Toggle TTS audio on/off
            %socratic_model    Select LLM model (e.g. deepseek-chat, gpt-4o)
            %socratic_style    Switch brief / verbose questioning
            %socratic_debug    Toggle debug trace (timing, model, TTS info)
            %socratic_auto_tests  Auto-generate tests on every %socratic_task
            %socratic_explore   Toggle exploration mode (free experimentation)
            %socratic_cache    Inspect generated-tests cache
            %socratic_clear_cache  Clear generated-tests cache (for demos)
            %socratic_help     This help

            **Author-written tests:** put a code cell *below* your %%socratic
            cell, first line `#Test cases`, then `assert` lines. They run as
            hidden tests — pass = confetti, fail = a sharper question.

            **Quick start (explicit task)**
              1.  %socratic_task Build a BMI calculator
              2.  %%socratic
                  def bmi(w, h): return w / h**2

            **Track mode (auto-detect task)**
              1.  %socratic_task auto
              2.  Write code below a markdown task description
              3.  Socrates reads the description automatically

            Socrates speaks guiding questions (with TTS audio)
            when you go off-track, and praises you when correct.
        """))

    # ── %%socratic cell magic ──────────────────────────────────────────

    @cell_magic
    def socratic(self, line: str, cell: str) -> None:
        """Execute a cell then have Socrates analyse it.

        Flow: run cell → show thinking → analyze → resolve (praise or question)

        Always auto-detects the task from the markdown cell above
        (if it contains the word "Task").  Explicit %socratic_task overrides.
        """
        ip = get_ipython()

        # ═════════════════════════════════════════════════════════════════
        #  PHASE 1 — Resolve tests (cache → cell-below → AI-generate)
        # ═════════════════════════════════════════════════════════════════
        auto_detected = False
        t_auto_start = time.monotonic()
        tests_from_below = None  # may be set by _try_auto_detect
        if not _watchdog.task_description:
            task_detected, tests_from_below = _try_auto_detect(cell)
            if task_detected:
                _watchdog.set_task(task_detected)
                auto_detected = True
        _watchdog._auto_detect_time = round(time.monotonic() - t_auto_start, 4)

        # Show what task Socrates is working against — before test resolution
        # so the user sees task first, then cache/generate status.
        resolved = _watchdog._resolve_task(cell)
        if resolved:
            preview = resolved if len(resolved) <= 120 else resolved[:117] + "..."
            print(f"🧠  Task: {preview}")

        # Resolve tests in priority order: cache → human-written → AI-generate.
        # Whatever the source, record it in `provenance` so we can emit ONE
        # universal indicator below (instead of scattered per-source prints).
        provenance: str | None = None
        prov_count = 0
        if _watchdog.task_description and not _watchdog._all_test_cases:
            # Step 1: on-disk cache for this task
            t_cache_start = time.monotonic()
            cached = _watchdog._load_cached_tests(_watchdog.task_description)
            if cached is not None:
                _watchdog.hidden_test_cases = cached
                _watchdog._generate_timings = {
                    "read": round(time.monotonic() - t_cache_start, 4),
                }
                _watchdog._generate_timings["total"] = _watchdog._generate_timings["read"]
                provenance, prov_count = "cache", len(cached)
            # Step 2: human-written tests in the cell below (cache miss only)
            elif tests_from_below:
                _watchdog.test_cases = tests_from_below
                _watchdog._cache_tests(
                    _watchdog.task_description, tests_from_below,
                    source="manual", quiet=True,
                )
                provenance, prov_count = "cell", len(tests_from_below)
            # Step 3: neither cache nor usable cell-below tests — ask the LLM
            else:
                generated = _watchdog.generate_tests(quiet=True)
                if generated:
                    provenance, prov_count = "llm", len(generated)
        elif _watchdog.task_description and _watchdog._all_test_cases:
            # Tests were preloaded (e.g. by set_task's auto_generate_tests).
            # Infer the source from generate_tests' timing breadcrumbs so the
            # indicator and debug table stay accurate.
            prov_count = len(_watchdog._all_test_cases)
            provenance = "cache" if "read" in _watchdog._generate_timings else "llm"

        _announce_test_source(
            provenance, prov_count,
            has_task=bool(_watchdog.task_description),
            has_api_key=_watchdog._has_api_key(),
        )

        t_cell_start = time.monotonic()
        result = ip.run_cell(cell)
        t_cell_end = time.monotonic()

        error = _extract_error(result)

        # Show "Socrates is thinking..." during LLM call + TTS
        handle = None
        try:
            handle = _show_thinking()
        except Exception:
            pass
        question = ""
        try:
            question = _watchdog.analyze(cell, error)
            t_analyze_end = time.monotonic()

            if question and question not in ("[LLM_UNAVAILABLE]", "[TESTS_FAILED]"):
                _deliver(question)
                t_deliver_end = time.monotonic()
            else:
                t_deliver_end = t_analyze_end
        finally:
            if handle is not None:
                _resolve_thinking(handle, question)

        # Record full end-to-end timings
        _watchdog._timings["run_cell"] = round(t_cell_end - t_cell_start, 3)
        _watchdog._timings["deliver"] = round(t_deliver_end - t_analyze_end, 3)
        _watchdog._timings["end_to_end"] = round(t_deliver_end - t_cell_start, 3)

        # Print the debug table BEFORE clearing state, so it reflects the
        # tests that actually ran this cell (count, pass/fail), not zeros.
        if _watchdog._debug:
            _print_debug_table(_watchdog)

        # Clear auto-detected task AND its tests so the next cell re-detects
        # its own markdown.  Without clearing the tests, a later cell that has
        # no task of its own would still run this cell's hidden tests.
        if auto_detected:
            _watchdog.task_description = ""
            _watchdog.test_cases = []
            _watchdog.hidden_test_cases = []


# ═══════════════════════════════════════════════════════════════════════════
#  POST-RUN CELL HOOK  (fired when auto-watch is on)
# ═══════════════════════════════════════════════════════════════════════════

def _post_run_cell_hook(result) -> None:
    """IPython event hook — fires after every cell execution."""
    if not _watchdog.watch_all:
        return

    # IPython ≥ 8.20 passes an ExecutionResult object whose .info attribute
    # holds the ExecutionInfo (which carries .raw_cell).  Older IPython
    # passed the ExecutionInfo directly.  Handle both.
    if hasattr(result, "info") and hasattr(result.info, "raw_cell"):
        info = result.info   # ExecutionResult  →  unwrap
    else:
        info = result        # ExecutionInfo (legacy)
        result = None        # no separate error container

    source = info.raw_cell.strip()
    if not source:
        return
    if source.startswith(("%", "!")):
        return

    if not _should_analyze():
        return

    # Always try auto-detection from markdown above (if no explicit task).
    # Also checks the cell below for human-written tests.
    tests_from_below = None
    if not _watchdog.task_description:
        task_detected, tests_from_below = _try_auto_detect(source)
        if task_detected:
            _watchdog.set_task(task_detected)
            if tests_from_below is not None and tests_from_below:
                _watchdog.test_cases = tests_from_below
                _watchdog._cache_tests(
                    _watchdog.task_description, tests_from_below, source="manual"
                )
            elif tests_from_below is None:
                # No cell below — check cache before AI-generate
                cached = _watchdog._load_cached_tests(_watchdog.task_description)
                if cached is not None:
                    _watchdog.hidden_test_cases = cached

    # Auto-generate hidden tests if we have a task but no tests yet
    if _watchdog.task_description and not _watchdog._all_test_cases:
        _watchdog.generate_tests()

    error = _extract_error_from_info(result or info)
    t0 = time.monotonic()

    # Show "Socrates is thinking..." during LLM call + TTS
    handle = None
    try:
        handle = _show_thinking()
    except Exception:
        pass
    question = ""
    try:
        question = _watchdog.analyze(source, error)
        t1 = time.monotonic()
        if question and question not in ("[LLM_UNAVAILABLE]", "[TESTS_FAILED]"):
            _deliver(question)
            t2 = time.monotonic()
        else:
            t2 = t1
    finally:
        if handle is not None:
            _resolve_thinking(handle, question)

    _watchdog._timings["deliver"] = round(t2 - t1, 3)
    _watchdog._timings["end_to_end"] = round(t2 - t0, 3)

    if _watchdog._debug:
        t = _watchdog._timings
        print(
            f"⏱  [auto] llm={t['llm_call']}s  "
            f"tts={t.get('deliver', 0)}s  "
            f"→ {t['end_to_end']}s total"
        )

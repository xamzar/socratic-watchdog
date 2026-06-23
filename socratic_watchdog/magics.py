"""
socratic_watchdog.magics — IPython magics and hooks for Socratic Watchdog.

Loaded automatically by ``%load_ext socratic_watchdog`` via ``__init__.py``.
"""

from __future__ import annotations

import os
import random
import textwrap
import time

from IPython import get_ipython
from IPython.core.magic import Magics, magics_class, line_magic, cell_magic
from IPython.display import display as ipy_display, HTML

from ._core import _watchdog

# ── Shared helpers ────────────────────────────────────────────────────

def _show_thinking():
    """Show a 'Socrates is thinking...' indicator while the LLM works.

    Returns a display handle. The caller should pass it to
    ``_resolve_thinking(handle, question)`` when analysis completes.
    """
    return ipy_display(
        HTML("<i>🏛️  Socrates is thinking…</i>"),
        display_id="socratic-thinking",
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

def _random_praise() -> str:
    """Return a random Socratic praise phrase."""
    return random.choice(_SOCRATIC_PRAISES)


def _resolve_thinking(handle, question: str):
    """Replace thinking indicator with final result.

    If question is given: clear so ``_deliver()`` can show its styled box.
    If no question (silent): show a green subtitle box with a random
    Socratic praise + TTS audio.
    """
    try:
        if question:
            # Clear — _deliver() handles the question display
            handle.update(HTML(""))
        else:
            praise = _random_praise()
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
            # Also speak the praise
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
    try:
        audio = _watchdog.speak(question)
        if audio is not None:
            ipy_display(audio)
            if _watchdog._debug and _watchdog._tts_time is not None:
                print(f"⏱  tts={_watchdog._tts_time}s  (backend={os.environ.get('SOCRATIC_TTS_BACKEND', 'espeak')})")
    except Exception:
        pass


def _try_auto_detect(source: str) -> str | None:
    """Try to auto-detect the task from the markdown cell above.

    Returns the detected task text or None.
    """
    try:
        task = _watchdog.detect_task_from_notebook(source)
        if task:
            # Note: we don't set task_description (that's for explicit tasks).
            # auto_detect_task flag controls whether we attempt detection.
            return task
    except Exception:
        pass
    return None


# ── Debounce (for auto-watch mode) ────────────────────────────────────

_LAST_ANALYSIS_TIME: float = 0.0
_DEBOUNCE_SECONDS: float = 3.0


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
    """%%socratic, %socratic_task, %socratic_tests, %socratic_watch, and friends."""

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
            if _watchdog.auto_detect_task:
                print("🧠 Auto-detect: ON (task read from markdown above each cell)")
            elif _watchdog.task_description:
                print(f"🧠 Task: “{_watchdog.task_description}”")
            else:
                print("🧠 No task set.")
            print("   Set with:  %socratic_task <goal>  or  %socratic_task auto")
            return

        if arg == "auto":
            _watchdog.task_description = ""
            _watchdog.auto_detect_task = True
            _watchdog._cached_notebook_cells = []  # force re-scan
            print("🧠  Auto-detect enabled. I'll read the task from the markdown cell above each code cell.")
        elif arg == "clear":
            _watchdog.reset_context()
            print("🧠  Task cleared.")
        else:
            _watchdog.set_task(line)  # explicit task (disables auto-detect)
            print(f"🧠  Task set: “{line}”")

    # ── socratic_tests (for track authors) ─────────────────────────────

    @line_magic
    def socratic_tests(self, line: str) -> None:
        """Set pre-assigned test cases that Socrates checks against.

        The track author can embed test cases so Socrates knows the exact
        expected behavior — not just 'looks correct' but 'passes the spec.'

        Usage::

            %%socratic_tests
            assert fib(0) == 0
            assert fib(1) == 1
            assert fib(5) == 5

            %%socratic_tests --hidden
            assert fib(0) == 0
            assert fib(5) == 5

        ``--hidden`` stores tests in hidden_test_cases — they still run
        but students never see them.  Call with no body to clear.
        """
        # --hidden flag: toggle hidden mode for this cell
        hidden = line.strip().lower() == "--hidden"

        # Use cell body (the multi-line content after the magic line).
        # We re-read the cell source through IPython's history.
        ip = get_ipython()
        cells = ip.user_ns.get("_ih", [])
        cell_body = ""
        if cells:
            # Last history entry is the current cell
            raw = cells[-1] if isinstance(cells, list) else ""
            # Strip the magic line
            lines = raw.split("\n")
            if lines and ("socratic_tests" in lines[0]):
                cell_body = "\n".join(lines[1:])

        if not cell_body.strip():
            _watchdog.test_cases = []
            _watchdog.hidden_test_cases = []
            print("🧠  Test cases cleared.")
            return

        tests = [
            l.strip() for l in cell_body.strip().split("\n")
            if l.strip() and not l.strip().startswith("#")
        ]

        if hidden:
            _watchdog.hidden_test_cases = tests
            print(f"🧠  {len(tests)} hidden test case(s) set (students won't see them).")
        else:
            _watchdog.test_cases = tests
            print(f"🧠  {len(tests)} test case(s) set:")
            for tc in _watchdog.test_cases:
                print(f"     {tc}")

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
            backend = os.environ.get("SOCRATIC_TTS_BACKEND", "edge-tts")
            print(f"   tts ({backend:<8}): {_watchdog._tts_time:>8.3f}s")

    @line_magic
    def socratic_help(self, line: str) -> None:
        """Show usage help."""
        print(textwrap.dedent("""\
            🏛️  **Socratic Watchdog — Commands**
            ─────────────────────────────────────
            %%socratic          Run a cell with Socratic analysis
            %socratic_task     Describe your coding goal
                                (or 'auto' to read from markdown above)
            %socratic_tests    Set expected test cases (track author)
            %socratic_watch    Watch every cell (on/off)
            %socratic_off      Stop watching
            %socratic_reset    Clear Socrates's context
            %socratic_stats    Show timing breakdown of last analysis
            %socratic_help     This help

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

        If ``%socratic_task auto`` is set, the task is read from the
        markdown cell directly above this one.

        Usage::

            %%socratic
            def greet(name):
                return f"Hello, {name}!"
        """
        ip = get_ipython()

        # Auto-detect task from markdown above (if enabled and no explicit task)
        auto_detected = False
        if _watchdog.auto_detect_task and not _watchdog.task_description:
            detected = _try_auto_detect(cell)
            if detected:
                _watchdog.task_description = detected
                auto_detected = True

        t_cell_start = time.monotonic()
        result = ip.run_cell(cell)
        t_cell_end = time.monotonic()

        # Show what task Socrates is working against (so the user can spot
        # stale / leaked task context before the LLM call).
        resolved = _watchdog._resolve_task(cell)
        if resolved:
            preview = resolved if len(resolved) <= 120 else resolved[:117] + "..."
            print(f"🧠  Task: {preview}")

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

            if question:
                _deliver(question)
                t_deliver_end = time.monotonic()
            else:
                t_deliver_end = t_analyze_end
        finally:
            if handle is not None:
                _resolve_thinking(handle, question)

        # Clear auto-detected task so the next %%socratic cell re-detects
        # its own markdown (avoids leaking "Palindrome Checker" into a
        # "Fibonacci" cell).
        if auto_detected:
            _watchdog.task_description = ""

        # Record full end-to-end timings
        _watchdog._timings["run_cell"] = round(t_cell_end - t_cell_start, 3)
        _watchdog._timings["deliver"] = round(t_deliver_end - t_analyze_end, 3)
        _watchdog._timings["end_to_end"] = round(t_deliver_end - t_cell_start, 3)

        if _watchdog._debug:
            t = _watchdog._timings
            print(
                f"⏱  cell={t['run_cell']}s  "
                f"build={t['build_prompt']}s  "
                f"llm={t['llm_call']}s  "
                f"parse={t['parse']}s  "
                f"tts={t.get('deliver', 0)}s  "
                f"→ total={t['end_to_end']}s"
            )


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

    # Auto-detect task from markdown above (if enabled, no explicit task)
    if _watchdog.auto_detect_task and not _watchdog.task_description:
        detected = _try_auto_detect(source)
        if detected:
            _watchdog.task_description = detected

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
        if question:
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
            f"⏱  [auto] build={t['build_prompt']}s  "
            f"llm={t['llm_call']}s  "
            f"tts={t.get('deliver', 0)}s  "
            f"→ total={t['end_to_end']}s"
        )

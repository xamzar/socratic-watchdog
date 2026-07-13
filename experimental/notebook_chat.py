"""In-notebook AI chat (`%ask`) with full-notebook context — EXPERIMENTAL.

Why this exists
---------------
Two colleague asks converge here:
  * "make it easy to talk to the AI in the notebook ... replace jupyter-ai magic
     commands" — a plain ``%ask <question>`` that just answers, no Socratic
     silence, no test harness.
  * "the model should have the complete view of the entire notebook, including
     the test cells to come" — so the answer is grounded in what the student is
     actually looking at, not just the current cell.

This is the versatile side the colleague flagged: the package stops being
"TTS that quizzes you" and becomes "an in-notebook AI you can ask anything,
by text or (with stt_whisper) by voice, that talks back (with any TTS backend)."
That's also the argument for renaming the package — TTS is the least of it.

It deliberately does NOT touch _core.py or magics.py. It *reuses* them:
  * ``_watchdog._call_llm`` — the existing OpenAI-compatible HTTPS client, so
    API key / base URL / model config is identical to the rest of the package
    (SOCRATIC_LLM_API_KEY etc). Set an endpoint to a Hermes gateway and this
    talks to Hermes with zero code change — same seam as HERMES_INTEGRATION.md.
  * ``_watchdog._get_notebook_cells`` — reads every cell (above AND below the
    current one, test cells included).
  * ``_watchdog.speak`` — optional spoken answer via whatever TTS backend is set.

How to connect it (opt-in, no core edits)
------------------------------------------
    %load_ext socratic_watchdog          # normal load
    from experimental.notebook_chat import load_ipython_extension as chat
    chat(get_ipython())                  # registers %ask / %%ask
    %ask why does my fib function recurse forever?
    %ask --speak explain list comprehensions

Voice input: pair with experimental/stt_whisper.py — transcribe the mic to text
and pass it to ``ask(...)`` for a full verbal loop.
"""
from __future__ import annotations

CHAT_SYSTEM = (
    "You are a helpful programming teaching assistant embedded in a Jupyter "
    "notebook. Unlike Socratic mode, you MAY answer directly and show code. "
    "Be concise. You are given the full notebook (all cells, including tests "
    "the student may not have run yet) as context — use it, but do not just "
    "dump the solution to a graded exercise; explain."
)


def build_notebook_context(max_chars: int = 6000) -> str:
    """Render every notebook cell (code + markdown, above and below) as context.

    Includes test cells 'to come' so the model sees the whole picture. Truncates
    to ``max_chars`` from the end (most recent cells matter most).
    """
    from socratic_watchdog import _watchdog
    try:
        cells = _watchdog._get_notebook_cells()
    except Exception:
        cells = []
    lines = []
    for i, cell in enumerate(cells):
        kind = cell.get("cell_type", "code")
        src = (cell.get("source") or "").rstrip()
        if src:
            lines.append(f"# --- cell {i} ({kind}) ---\n{src}")
    ctx = "\n\n".join(lines)
    return ctx[-max_chars:] if len(ctx) > max_chars else ctx


def ask(question: str, speak: bool = False):
    """Ask the LLM a free-form question grounded in the whole notebook.

    Returns the answer string. If ``speak`` and IPython is present, also plays
    the answer through the configured TTS backend.
    """
    from socratic_watchdog import _watchdog
    if not question.strip():
        return ""
    ctx = build_notebook_context()
    prompt = (f"Notebook so far:\n\n{ctx}\n\n---\nStudent asks: {question}"
              if ctx else question)
    answer = _watchdog._call_llm(prompt, system=CHAT_SYSTEM, max_tokens=600)
    if answer.strip() in ("", "[SILENT]"):
        answer = "(no LLM configured — set SOCRATIC_LLM_API_KEY to enable chat)"
    if speak:
        try:
            from IPython.display import display
            audio = _watchdog.speak(answer)
            if audio is not None:
                display(audio)
        except Exception:
            pass
    return answer


# --- IPython glue (imported lazily so _core stays IPython-free) ---------------

def _register(ipython):
    from IPython.core.magic import Magics, magics_class, line_cell_magic
    from IPython.display import Markdown, display

    @magics_class
    class ChatMagics(Magics):
        @line_cell_magic
        def ask(self, line: str, cell: str | None = None):
            """%ask <question>   or   %%ask (question in the cell body).

            Flag: --speak  also voices the answer.
            """
            speak = "--speak" in line
            line = line.replace("--speak", "").strip()
            question = (line + "\n" + cell).strip() if cell else line
            answer = ask(question, speak=speak)
            display(Markdown(answer))

    ipython.register_magics(ChatMagics)


def load_ipython_extension(ipython):  # so `%load_ext experimental.notebook_chat` works too
    _register(ipython)
    print("💬  notebook_chat loaded — use %ask <question> (add --speak to hear it)")


def demo() -> None:
    """Self-check: context builder truncates and tolerates a missing notebook."""
    import socratic_watchdog  # noqa: F401 — ensures _watchdog import path is valid
    ctx = build_notebook_context(max_chars=50)
    assert isinstance(ctx, str) and len(ctx) <= 50
    assert ask("") == ""            # empty question short-circuits, no LLM call
    print("notebook_chat: ok")


if __name__ == "__main__":
    demo()

"""nbkit — the notebook primitives every AI-in-Jupyter package needs.

Reference implementation for the CS1302 agentic-coding notebooks. Each
function here is one the tutorial hands a student as *a docstring plus a
failing test*, so a 12B model can fill in the body one function at a time.

Extracted from socratic-watchdog, which already solved the hard part:

    _core._get_notebook_cells      → get_cells()   (the 3-source ladder)
    magics._try_auto_detect        → _cells_from_disk()
    magics._extract_error          → format_error()
    magics._show_thinking          → live_display()

Nothing here imports socratic_watchdog. Nothing here needs a network. The
only import outside the stdlib is IPython, and only inside the functions
that genuinely need a live kernel — so `pytest` runs the whole file with no
notebook attached.

L0 is read, L1 is write and display. L2 (ask) and L3 (triggers) live in
their own notebooks.
"""

from __future__ import annotations

import glob
import json
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, Optional


def shell():
    """The live IPython shell, or None when there is no kernel.

    Every kernel-dependent primitive goes through here and degrades to a
    no-op rather than raising, so a package built on nbkit still imports
    cleanly under plain `python` and under `pytest`.
    """
    try:
        from IPython import get_ipython
        return get_ipython()
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════
#  L0 — READ
# ══════════════════════════════════════════════════════════════════════

def strip_magics(source: str) -> str:
    """Drop the leading `%magic`, `%%cell-magic` and `!shell` lines.

    Needed because the two halves of the system disagree about what a cell
    contains. IPython hands a cell magic the source *with* the `%%socratic`
    line already removed; the notebook JSON stores the full text. Without
    this, a cell can never match itself.

        >>> strip_magics("%%socratic\\nx = 1")
        'x = 1'
        >>> strip_magics("x = 1  # 50% done")
        'x = 1  # 50% done'

    Only *leading* lines are stripped — a `%` in the middle of real code is
    left alone. (socratic-watchdog's regex strips matching lines anywhere in
    the cell; that is a latent bug, not a behaviour to copy.)
    """
    lines = source.splitlines()
    i = 0
    while i < len(lines) and lines[i].lstrip().startswith(("%", "!")):
        i += 1
    return "\n".join(lines[i:]).strip()


def _cells_from_colab() -> list[dict]:
    """Live cells from the Colab frontend, or [] if not in Colab.

    Colab never writes an .ipynb the kernel could read, so asking the
    browser is the only way. Always fresh — never cache this.
    """
    try:
        from google.colab import _message  # exists only inside Colab
        return _message.blocking_request("get_ipynb", timeout_sec=5)["ipynb"]["cells"]
    except Exception:
        return []


def _cells_from_mcp() -> list[dict]:
    """Live cells via `jupyter-mcp-cli`, or [] if it isn't installed.

    This is the JupyterHub/DIVE path: the CLI talks to jupyter_server with
    the session's own token, so it sees unsaved edits. Preferred over disk
    whenever it is available.
    """
    try:
        path = subprocess.run(
            ["jupyter-mcp-cli", "get_active_notebook"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if not path or "error" in path.lower():
            return []
        out = subprocess.run(
            ["jupyter-mcp-cli", "read_notebook_cells", "--arg", f"notebook_path={path}"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        return json.loads(out).get("cells", [])
    except Exception:
        return []


def _cells_from_disk(path: Optional[str] = None) -> list[dict]:
    """Cells read straight off an .ipynb file, or [] on any failure.

    The last-resort source, and the only one that works offline. With no
    `path`, picks the most recently modified .ipynb in the working
    directory.

    Caveat worth teaching: this file is only as fresh as the last save.
    Edit a cell, run it without saving, and what you read here is the *old*
    text. The two sources above see live state; this one does not.
    """
    try:
        if path is None:
            candidates = glob.glob("*.ipynb")
            if not candidates:
                return []
            path = max(candidates, key=lambda p: Path(p).stat().st_mtime)
        return json.loads(Path(path).read_text()).get("cells", [])
    except Exception:
        return []


def get_cells() -> list[dict]:
    """Every cell of the current notebook, as raw nbformat dicts.

    Tries three sources and takes the first that answers: Colab frontend →
    jupyter-mcp-cli → .ipynb on disk. Returns [] when all three fail, which
    is a normal state (plain `python`, a test run) and not an error.

    The ladder is the point. No single source works everywhere — Colab has
    no file, JupyterHub has no `google.colab`, a bare kernel has neither —
    so the primitive degrades instead of picking one and breaking on the
    other two. Same shape as the ask() fallback in notebook 1.

    Deliberately uncached, unlike socratic-watchdog: a cached cell list goes
    stale the moment the student adds a cell, and every bug that causes
    looks like an AI bug.
    """
    return _cells_from_colab() or _cells_from_mcp() or _cells_from_disk()


def cell_source(cell: dict) -> str:
    """The source text of one nbformat cell dict.

    nbformat allows `source` to be a list of lines *or* a single string;
    joining handles both.
    """
    return "".join(cell.get("source", []))


def get_cell(i: int) -> str:
    """Source of cell `i`, or "" if there is no such cell.

    Out of range returns "" rather than raising — an AI package that
    crashes the student's kernel over a missing cell is worse than one that
    stays quiet.
    """
    cells = get_cells()
    return cell_source(cells[i]) if -len(cells) <= i < len(cells) else ""


def get_current_cell() -> str:
    """Source of the cell that is executing right now, or "".

    Comes from IPython's input history (`_ih[-1]`), which is appended
    *before* execution starts — so during a cell run it is that cell. This
    is exact and needs no notebook file at all.

    Note this returns the source, not a position. For the index in the
    notebook, see current_index() — a strictly harder problem.
    """
    ip = shell()
    if ip is None:
        return ""
    return (ip.user_ns.get("_ih") or [""])[-1]


def current_index() -> Optional[int]:
    """Index of the running cell within get_cells(), or None.

    Matches the current source against every code cell, magic-stripped on
    both sides. Needed by anything positional: "the markdown above me",
    "the test cell below me".

    Returns None on no match or on an ambiguous one (two identical cells) —
    a wrong index means writing into someone else's cell, so refusing is
    the safe failure.

    # ponytail: exact match only. socratic-watchdog adds a similarity score
    # to cope with the stale-disk case; it compares characters positionally,
    # so inserting one character at the top drops the score to ~0 and it
    # misses anyway. If you need to tolerate stale disk reads, reach for
    # difflib.SequenceMatcher, not a hand-rolled score.
    """
    current = strip_magics(get_current_cell())
    if not current:
        return None
    hits = [
        i for i, c in enumerate(get_cells())
        if c.get("cell_type") == "code" and strip_magics(cell_source(c)) == current
    ]
    return hits[0] if len(hits) == 1 else None


def get_cells_before(n: int = 1) -> list[str]:
    """Source of the `n` cells immediately above the running cell.

    Derived, not primitive — get_cells() and current_index() already
    contain the whole answer. Worth writing anyway: reaching for context
    above the cursor is the most common thing an AI package does, and
    students should see it composed rather than added to the core.
    """
    idx = current_index()
    if idx is None:
        return []
    return [cell_source(c) for c in get_cells()[max(0, idx - n):idx]]


def get_last_output() -> Any:
    """Value of the last expression the notebook displayed, or None.

    IPython keeps it in `_`. Only expression results land there — a cell
    ending in print() or an assignment leaves the previous value in place.
    """
    ip = shell()
    return None if ip is None else ip.user_ns.get("_")


def format_error(exc: Optional[BaseException]) -> str:
    """One-line "TypeError: ..." for an exception, or "" for None.

    Pure and therefore testable, which is the entire reason it exists as
    its own function: socratic-watchdog has this logic twice, as
    _extract_error and _extract_error_from_info, differing only in the
    attribute they read it from. Take the exception as an argument and the
    duplicate disappears.

    Drops the stack frames on purpose. The exception type and message are
    what an LLM needs; the frames are tokens that push the student's actual
    code out of the context window.
    """
    if exc is None:
        return ""
    return "".join(traceback.format_exception_only(type(exc), exc)).strip()


def get_last_error() -> str:
    """The last uncaught exception in this kernel, formatted, or "".

    Reads `sys.last_value`, which IPython sets after a traceback.

    Stale by design: it survives until the *next* exception, so a cell that
    now succeeds still reports the error from ten minutes ago. Fine for a
    student typing %explain_error on purpose; wrong for anything automatic.
    Notebook 3's on_cell_error hook gets a live exception handed to it and
    should call format_error() on that instead.
    """
    return format_error(getattr(sys, "last_value", None))


# ══════════════════════════════════════════════════════════════════════
#  L1 — WRITE & DISPLAY
# ══════════════════════════════════════════════════════════════════════
#
# There is no write layer in socratic-watchdog to copy — it only ever
# reads. So this half is new, and the mechanism is worth stating plainly:
# the kernel cannot reach into the notebook document. It can only send the
# frontend a `set_next_input` payload and ask. That payload can address
# exactly two places, the current cell and a new cell below it.
#
# Which means write_cell(i, src) — write to an arbitrary index — is NOT
# buildable this way, and is not in this API. It needs the jupyter_server
# REST API plus a token, a much bigger build with worse failure modes under
# JupyterHub. Insert-below covers every case in notebooks 2 through 5.

def replace_current_cell(source: str) -> bool:
    """Overwrite the running cell with `source`. True if the request went out.

    Asks the frontend to swap the cell's text. The change appears after the
    current execution finishes — you cannot rewrite a cell and then read
    the new text back in the same run.

    Destructive: the student's code is gone with no undo step of your own
    (Ctrl+Z in the frontend still works). Anything built on this must be
    idempotent — running it twice on an already-processed cell should be a
    no-op, not a second rewrite. That is notebook 2's whole lesson.
    """
    ip = shell()
    if ip is None:
        return False
    ip.set_next_input(source, replace=True)
    return True


def insert_cell_below(source: str) -> bool:
    """Add a new cell below the running one. True if the request went out.

    The safe sibling of replace_current_cell: it can't destroy anything.
    Prefer it whenever the output is a suggestion rather than a correction.
    """
    ip = shell()
    if ip is None:
        return False
    ip.set_next_input(source, replace=False)
    return True


def show_md(text: str) -> None:
    """Render `text` as Markdown in the cell output."""
    from IPython.display import Markdown, display
    display(Markdown(text))


def show_html(html: str) -> None:
    """Render raw `html` in the cell output.

    Always set an explicit `color:` alongside any `background:`. JupyterLab's
    dark theme leaves the inherited text colour light, so a light-background
    box renders white-on-white — a real bug that shipped in
    socratic-watchdog's four status boxes.
    """
    from IPython.display import HTML, display
    display(HTML(html))


def live_display(initial: str = ""):
    """A handle to one output area you can rewrite in place.

    Returns an IPython DisplayHandle; call `handle.update(HTML(...))` to
    replace what is on screen instead of appending below it.

        handle = live_display("<i>thinking…</i>")
        handle.update(HTML(answer))

    This is how you show progress during a slow model call without leaving
    a trail of dead "thinking…" boxes — and how notebook 1 streams tokens.
    """
    from IPython.display import HTML, display
    return display(HTML(initial), display_id=True)

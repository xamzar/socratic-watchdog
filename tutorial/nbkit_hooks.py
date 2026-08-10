"""nbkit_hooks — L3, triggers. Run your package without being called.

This is the conceptual jump in the ladder. Notebooks 1 and 2 are things the
student invokes; from here the package invokes itself, and every design
question changes. It has to be silent when it has nothing to say, cheap when
it runs on every cell, and impossible to break the kernel with.

Three primitives:

    on_cell_run(fn)     fn(source) after every cell
    on_cell_error(fn)   fn(source, error) after cells that raised
    cell_magic(name)    a %%your_magic, in three lines instead of a class

The hard parts are not the event registration — that is one IPython call.
They are the four things below, all learned the expensive way in
socratic-watchdog:

1. **Re-running the registration cell must not register twice.** Students
   re-run cells constantly. Without dedupe you get two hooks, then four, and
   an "AI bug" that is really a bookkeeping bug. Hooks here are keyed by
   qualified name and replaced, not appended.
2. **A callback that raises must not break the next cell.** Anything thrown
   inside a hook is caught and printed as one line. A package that makes the
   student's unrelated code stop working gets uninstalled.
3. **Not every execution deserves a reaction.** Blank cells, and cells that
   are only `%magic` or `!shell`, are skipped before your callback is called.
4. **`post_run_cell` hands you different objects on different IPythons.**
   Unwrapped for you; see `_unwrap`.
"""

from __future__ import annotations

import sys
import time
import traceback
from typing import Callable, Optional

from nbkit import format_error, shell

# name → (event, wrapper) for everything we have registered. The dedupe key
# is the callback's qualified name, so re-running a cell that defines and
# registers `explain` replaces the old `explain` instead of stacking on it.
_REGISTERED: dict[str, tuple[str, Callable]] = {}


def _unwrap(arg):
    """Normalise what `post_run_cell` passes into one object.

    IPython ≥ 8.20 passes an ExecutionResult, whose `.info` holds the
    ExecutionInfo carrying `.raw_cell`; older IPython passed the
    ExecutionInfo directly. Both shapes are still in the wild — Colab and a
    campus JupyterHub are rarely on the same version — so handle both and
    never read `.info` without checking.

    Returns `(source, exception_or_None)`.
    """
    info = arg.info if hasattr(arg, "info") and hasattr(arg.info, "raw_cell") else arg
    source = getattr(info, "raw_cell", "") or ""
    exc = (getattr(arg, "error_in_exec", None)
           or getattr(arg, "error_before_exec", None)
           or getattr(info, "error_in_exec", None)
           or getattr(info, "error_before_exec", None))
    return source, exc


def _worth_reacting_to(source: str) -> bool:
    """False for cells no package should have an opinion about.

    Blank cells, and cells that are nothing but magics or shell escapes —
    `%load_ext`, `!pip install`. Reacting to those is noise at best, and at
    worst an infinite loop when your own magic triggers your own hook.
    """
    source = source.strip()
    if not source:
        return False
    return any(line.strip() and not line.strip().startswith(("%", "!"))
               for line in source.splitlines())


def _register(event: str, fn: Callable, wrapper: Callable) -> Callable:
    """Attach `wrapper` to `event`, replacing any earlier hook of the same name."""
    ip = shell()
    if ip is None:
        return fn
    key = getattr(fn, "__qualname__", repr(fn))
    old = _REGISTERED.pop(key, None)
    if old is not None:
        try:
            ip.events.unregister(old[0], old[1])
        except Exception:
            pass
    ip.events.register(event, wrapper)
    _REGISTERED[key] = (event, wrapper)
    return fn


def on_cell_run(fn: Optional[Callable[[str], None]] = None, *,
                min_seconds: float = 0.0):
    """Call `fn(source)` after every cell the student runs.

    Usable bare or with arguments::

        @on_cell_run
        def watch(source):
            ...

        @on_cell_run(min_seconds=3)
        def expensive(source):
            ...

    `min_seconds` is a debounce: executions arriving sooner than that after
    the last accepted one are dropped. Off by default, because notebook 3
    should not have to think about it. Turn it on the moment the callback
    costs money — a class of 500 running cells freely is a lot of inference,
    and most of those cells are `print(x)`.

    Returns `fn` unchanged, so the decorated name still works as a function.
    Re-running the cell re-registers rather than double-registers.
    """
    def decorate(f):
        last = [0.0]

        def wrapper(arg):
            try:
                source, _ = _unwrap(arg)
                if not _worth_reacting_to(source):
                    return
                now = time.monotonic()
                if min_seconds and now - last[0] < min_seconds:
                    return
                last[0] = now
                f(source)
            except Exception:
                _report(f)

        return _register("post_run_cell", f, wrapper)

    return decorate(fn) if callable(fn) else decorate


def on_cell_error(fn: Optional[Callable[[str, str], None]] = None, *,
                  min_seconds: float = 0.0):
    """Call `fn(source, error)` only after cells that raised.

        @on_cell_error
        def whisper(source, error):
            show_md(ask(f"A student hit {error} running:\\n{source}"))

    `error` is already formatted to one line by `nbkit.format_error` — type
    and message, no stack frames, because the frames crowd the student's
    actual code out of the prompt.

    Use this rather than `nbkit.get_last_error()`: the exception arrives here
    live, whereas `sys.last_value` lingers and will happily report an error
    the student fixed ten minutes ago.
    """
    def decorate(f):
        last = [0.0]

        def wrapper(arg):
            try:
                source, exc = _unwrap(arg)
                if exc is None or not _worth_reacting_to(source):
                    return
                now = time.monotonic()
                if min_seconds and now - last[0] < min_seconds:
                    return
                last[0] = now
                f(source, format_error(exc))
            except Exception:
                _report(f)

        return _register("post_run_cell", f, wrapper)

    return decorate(fn) if callable(fn) else decorate


def _report(fn: Callable) -> None:
    """Print one line about a hook that raised, then let the notebook carry on.

    Deliberately not a full traceback and deliberately not silent. Silent
    swallowing means a broken package looks like a package with nothing to
    say — the failure mode that costs the most debugging time.
    """
    name = getattr(fn, "__qualname__", repr(fn))
    exc = "".join(traceback.format_exception_only(*sys.exc_info()[:2])).strip()
    print(f"[nbkit] hook {name!r} failed and was skipped: {exc}")


def cell_magic(name: str):
    """Register the decorated `fn(line, cell)` as `%%name`.

        @cell_magic("explain")
        def explain(line, cell):
            get_ipython().run_cell(cell)      # run the student's code first
            show_md(ask("Explain:\\n" + cell))

    Three lines where socratic-watchdog needs a `@magics_class` with
    `@cell_magic` methods and a registration call. The class buys you shared
    state between magics; until you need that, this is the whole feature.

    Note what your function receives: `line` is the rest of the `%%explain`
    line, `cell` is everything below it — **without** the magic line, which
    is why `nbkit.strip_magics` exists on the other side. And the cell does
    not run unless you run it. That is a feature (notebook 4 inspects code
    before deciding whether to execute it) and a surprise exactly once.
    """
    def decorate(fn):
        ip = shell()
        if ip is not None:
            ip.register_magic_function(fn, magic_kind="cell", magic_name=name)
        return fn

    return decorate


def clear_hooks() -> None:
    """Unregister every hook nbkit has installed.

    The undo button for a teaching session. Also what a notebook should call
    before demonstrating a second, conflicting hook.
    """
    ip = shell()
    for event, wrapper in list(_REGISTERED.values()):
        if ip is not None:
            try:
                ip.events.unregister(event, wrapper)
            except Exception:
                pass
    _REGISTERED.clear()

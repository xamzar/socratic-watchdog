"""Socratic Watchdog — A Socratic TTS coding assistant for Jupyter.

Load with::

    %load_ext socratic_watchdog
    %socratic_task auto        # auto-read task from markdown above
    %%socratic
    def my_code():
        ...

Track authors can embed test cases::

    %socratic_tests
    assert fib(0) == 0
    assert fib(1) == 1
"""

import textwrap

from ._core import SocraticWatchdog, _watchdog

__version__ = "0.2.0"

__all__ = [
    "SocraticWatchdog",
    "_watchdog",
    "load_ipython_extension",
    "unload_ipython_extension",
]


def load_ipython_extension(ipython):
    """Called by ``%load_ext socratic_watchdog``."""
    from .magics import SocraticMagics, _post_run_cell_hook

    ipython.register_magics(SocraticMagics)
    ipython.events.register("post_run_cell", _post_run_cell_hook)
    print(textwrap.dedent("""\
        🧠  Socratic Watchdog loaded!
        ──────────────────────────────
        •  %%socratic        — run a cell with analysis
        •  %socratic_task   — set goal (or 'auto' to read from markdown)
        •  %socratic_tests          — embed test cases (--hidden for invisible)
        •  %socratic_generate_tests — auto-gen hidden tests from task
        •  %socratic_watch  — watch every cell

        Try:  %socratic_help
    """))


def unload_ipython_extension(ipython):
    """Called by ``%unload_ext socratic_watchdog``."""
    from .magics import _post_run_cell_hook

    ipython.events.unregister("post_run_cell", _post_run_cell_hook)
    print("🧠  Socratic Watchdog unloaded.")

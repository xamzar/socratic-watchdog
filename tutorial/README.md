# CS1302 — building an AI package in Jupyter

Teaching material for the summer class and Semester A CS1302A. Not part of the
`socratic_watchdog` package: nothing in `socratic_watchdog/` imports it, and it
is excluded from the sdist.

## What this teaches

Not "here is socratic-watchdog, read it." That package is too large for a 12B
model to build, and handing students a finished thing teaches nothing about how
it got made.

The lesson is **decompose → spec → test → compose**. A small model cannot build
the package from one prompt. It *can* write `get_current_cell()` given a
docstring and a failing test. That gap is the whole point, and the ladder below
is arranged so each notebook adds exactly one primitive.

| # | Package | New primitive | Point |
|---|---|---|---|
| 1 | Explainer — explain this cell | `get_current_cell`, `ask`, `show_md` | Smallest end-to-end loop. Three functions, real output. |
| 2 | Docstring filler | `insert_cell_below` | First time the package *modifies* the notebook. Idempotency. |
| 3 | Error whisperer | `on_cell_error`, `format_error` | First *reactive* package — no user invocation. The conceptual jump. |
| 4 | Watchdog-lite | hint escalation, test-pass fast path | Rebuilds the real package from parts. State across invocations. |
| 5 | Notebook quizzer | `get_cells`, `show_html` | Whole-notebook context + interactive output. Optional. |

A student who stops after #3 has still shipped something that works. Keep it
that way.

The notebooks themselves live in [`notebooks/`](notebooks/), preceded by a
`00_primitives.ipynb` that builds the whole L0/L1 vocabulary from scratch before
any package is attempted. [`AGENT_MISTAKES.md`](AGENT_MISTAKES.md) is the
companion reference: twelve things models reliably get wrong, each taken from a
real commit in this project, each with the prompt line that prevents it.

## Layers

| Layer | Module | What it is |
|---|---|---|
| L0 — read | `nbkit.py` | what the notebook contains |
| L1 — write & display | `nbkit.py` | changing what the notebook shows |
| L2 — model | `nbkit_ask.py` | `ask()` / `stream()` over any OpenAI-compatible endpoint |
| L3 — trigger | `nbkit_hooks.py` | `on_cell_run`, `on_cell_error`, `cell_magic` |

The same pattern shows up twice on purpose, and students should be pointed at
it: `get_cells()` and `ask()` both take a list of sources, try them in order,
and use the first that answers. Neither has a single way of working that is
right everywhere, so neither picks one.

## Running the tests

```bash
pytest tutorial/ -q      # 82 tests, no notebook and no network needed
```

Same rule as the package suite: if a test needs a kernel or an API key, it is
the wrong test. Kernel-dependent primitives are exercised against a fake shell,
which makes `pytest` a valid clean-room check on a student's implementation.

## Where this diverges from socratic-watchdog, and why

`nbkit.py` is a teaching copy, deliberately simpler. It does not track the
package, and the differences are the interesting part:

| | socratic-watchdog | nbkit | why |
|---|---|---|---|
| Current cell | similarity-match against the notebook file | `_ih[-1]` | exact, and needs no file at all |
| Cell index | fuzzy score, 0.7 threshold | exact match, `None` if ambiguous | the fuzzy score compares characters positionally, so one inserted character collapses it |
| Cell list | cached for the session | uncached | a stale list breaks the moment a student adds a cell, and the bug looks like an AI bug |
| Errors | `_extract_error` + `_extract_error_from_info` | one `format_error(exc)` | take the exception as an argument and the duplicate disappears |
| Writing cells | none — read-only | `set_next_input` | the package never needed it; notebooks 2–5 do |
| Endpoints | one, from env | two, tried in order | 500 students against 30 concurrent slots |
| Hook failure | registered once at load | dedupe by name, catch and report | students re-run the registration cell |

## Configuring the model

Nothing above L2 contains a URL. Set these in a `.env` beside the notebook, or
in the environment:

```bash
NBKIT_LITELLM_BASE_URL=...     # qwen3.6-27b, ~30 concurrent, tried first
NBKIT_DIVE_BASE_URL=...        # gemma-4-12b, ~500 concurrent, the fallback
```

Include the `/v1` yourself — `/chat/completions` is appended to whatever you
set, and nothing else. `NBKIT_BASE_URL` + `NBKIT_MODEL` + `NBKIT_API_KEY`
override both with a single endpoint, which is the escape hatch for working
from Colab against your own key.

**There are no default URLs**, and that is deliberate: the live addresses were
not in hand when this was written, and a guessed URL fails as a connection
error rather than as "you forgot to configure this" — then gets copied into
student notebooks and outlives the guess. Fill them in here once confirmed.

## The one thing that isn't buildable

`write_cell(i, src)` — writing to an arbitrary cell index — is **not** in this
API and cannot be. The kernel cannot reach into the notebook document; it can
only send the frontend a `set_next_input` payload, which addresses the current
cell or a new cell below it. Arbitrary-index writes need the jupyter_server REST
API plus a token, which is a much larger build with worse failure modes under
JupyterHub. `insert_cell_below` covers every case in notebooks 2 through 5.

## Status

All four layers are specced and green. Still open before the notebooks
themselves can be drafted:

- **Live endpoint URLs** for DiveAI and LiteLLM. L2 is written against them but
  cannot be run until they are filled in — and until it runs, the 12B model's
  real ceiling is unmeasured, which is what decides how much scaffolding each
  notebook needs.
- **Prof. Chan's agentic-coding notebook** (the BMI example) — the phase names
  must mirror his, not be invented.

Everything here is tested against fakes. Nothing has yet been run against a
real endpoint or a real kernel, so treat the green suite as "the logic is
right", not "this works on the server".

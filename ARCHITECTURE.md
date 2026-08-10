# Architecture — a newcomer's guide

This is the map to read **before** your first change. It explains what each
file does, how one notebook cell flows through the system, and the handful of
terms the code assumes you know. If anything here disagrees with the code, the
code is right and this doc has a bug — please fix it.

## The 30-second version

A student runs a code cell. We compare their code against test cases (if any).
If it passes, we celebrate. If it fails — or there are no tests — we ask an LLM
to phrase **one Socratic question** (never the answer) and speak it aloud.

## The three files

| File | Responsibility | Depends on IPython? |
|------|----------------|---------------------|
| `_core.py` | The engine: prompts, LLM calls, tests, TTS. Pure Python. | **No** — runs anywhere, easy to unit-test |
| `magics.py` | The Jupyter skin: `%%socratic` and friends, HTML boxes, confetti. | Yes |
| `__init__.py` | The `%load_ext` entry point. Wires the two together. | Yes |

The split is deliberate: **all the logic lives in `_core.py` with no Jupyter
dependency**, so you can test it with plain `pytest` and no notebook. `magics.py`
is only the presentation layer. When in doubt, put logic in `_core.py`.

Two directories sit beside the package and are **not** part of it:
`experimental/` (proposals, not importable from an install) and `tutorial/`
(teaching material — see the last section).

## How one `%%socratic` cell flows

```
Student runs %%socratic cell
        │
        ▼
magics.SocraticMagics.socratic()          ← the cell magic (magics.py)
        │
        ├─ resolve the TASK ──────────────────────────────┐
        │     explicit %socratic_task, else auto-detect    │
        │     the markdown cell above (_task_markdown_above)│
        │                                                   │
        ├─ resolve the TESTS (first hit wins):              │
        │     1. on-disk cache   (_load_cached_tests)       │
        │     2. #Test cases cell below (_extract_tests…)   │  all in _core.py
        │     3. ask the LLM to write them (generate_tests) │
        │                                                   │
        ├─ run the student's code (ip.run_cell)             │
        │                                                   │
        ▼                                                   │
SocraticWatchdog.analyze(source, error)   ←────────────────┘
        │
        ├─ PHASE 1  run tests. All pass?  → return "" (confetti, no LLM)
        │           Some fail?            → feed failures to the LLM
        │           No API key + tests?   → return "[TESTS_FAILED]" (honest, no praise)
        │
        ├─ PHASE 2  build the prompt (_build_prompt) + escalation hint
        │
        ├─ PHASE 3  call the LLM (_call_llm) — plain HTTPS POST
        │
        └─ PHASE 4  parse the reply (_parse_response)
                    "[SILENT]" → "" (praise)   |   anything else → the question
        │
        ▼
magics._deliver(question)   → HTML subtitle box + speak() TTS audio
```

Return values from `analyze()` are a tiny protocol — memorise these four:

| Return | Meaning | What the UI shows |
|--------|---------|-------------------|
| `""` (empty) | Correct — stay silent | Green praise box + confetti |
| a question string | Off track | Blue subtitle box + spoken question |
| `"[TESTS_FAILED]"` | Tests failed, no LLM to ask | Red "not passing yet" box |
| `"[LLM_UNAVAILABLE]"` | No tests **and** no LLM | Amber "Socrates is offline" box |

## Glossary (terms the code assumes)

- **Task** — what the student is supposed to build. Either set explicitly with
  `%socratic_task`, or auto-read from the markdown cell above their code.
- **Hidden tests** — `assert` lines that run but are never shown to the student.
  Used for the "did it pass?" fast path. Come from a `#Test cases` cell or the LLM.
- **Fast path** — if tests pass, we skip the LLM entirely. Instant and free.
- **Provenance** — a one-word label (`cache`/`cell`/`llm`) for where this cell's
  tests came from, so we can print one honest status line.
- **Escalation ladder** — repeated failures on the same task make the question
  progressively more concrete (see `_escalation_directive`). Resets on a pass.
- **Socratic rules** — the system prompt (`SOCRATIC_RULES`) that forbids the LLM
  from ever giving the answer. Sent on the `system` role, not in the user text.

## Where things live in `_core.py`

- `SocraticWatchdog.analyze()` — the pipeline above. Start here.
- `_build_prompt()` / `_get_system_prompt()` — what we send the LLM.
- `_call_llm()` — the only network call. OpenAI-compatible; DeepSeek by default.
- `generate_tests()` / `_cache_file()` / `_load_cached_tests()` — the test cache.
- `speak()` + `_speak_espeak/_edge/_kokoro()` — the three TTS backends.
- `detect_task_from_notebook()` + module-level `_task_markdown_above()` — task
  auto-detection from the notebook.
- `_get_notebook_cells()` — see below.

## How we see the notebook

A running kernel does not natively know about the notebook driving it, and no
single way of asking works everywhere. So `_get_notebook_cells()` tries three
sources and takes the first that answers:

| Order | Source | Where | Fresh? |
|---|---|---|---|
| 1 | Colab frontend — `google.colab._message` | `_core.py` | yes, refetched every call |
| 2 | `jupyter-mcp-cli` subprocess (JupyterHub / DIVE) | `_core.py` | cached for the session |
| 3 | glob `*.ipynb` off disk | `magics._try_auto_detect` | only as fresh as the last save |

Two consequences worth knowing before you debug anything positional:

- **Source 3 is stale by nature.** Edit a cell, run it without saving, and the
  file still holds the old text — so `_find_current_cell` misses and
  auto-detection quietly gives up. Not a bug you can fix in the matcher.
- **This layer is read-only.** Nothing in the package writes cells back. If you
  ever need to, the kernel cannot reach into the document; it can only send the
  frontend a `set_next_input` payload, which addresses the current cell or a new
  one below it and nothing else.

An empty list is a normal answer, not an error — every caller reads `[]` as
"no notebook context" and carries on.

## Making a change — the checklist

1. Is it logic? Put it in `_core.py`. Is it how it *looks*? `magics.py`.
2. Add or update a test in `tests/test_socratic_watchdog_core.py`. The suite
   runs with no notebook and no network (the LLM is mocked) — keep it that way.
3. Run `pytest -q` (186 tests, all green — that count includes `tutorial/`).
4. If you changed behavior, update `README.md` (the spec) in the same commit.
5. Prefer the standard library over new dependencies — the only required dep is
   `ipython`, and we'd like to keep it that way.

## `tutorial/` — the CS1302 teaching material

Not part of the package. Nothing in `socratic_watchdog/` imports it, and it is
excluded from the sdist.

Four modules, one per layer: `nbkit.py` (read + write), `nbkit_ask.py` (the
model), `nbkit_hooks.py` (triggers), each with its own test file. `nbkit.py` is
a stripped, standalone reimplementation of the notebook primitives above
(`get_cells`, `get_current_cell`, `format_error`, …) plus the write half this
package never needed; `nbkit_ask.py` is `_call_llm` with the two-endpoint
classroom fallback added; `nbkit_hooks.py` is `_post_run_cell_hook` generalised.

It exists because a 12B model cannot build socratic-watchdog from one prompt,
but it *can* write `get_current_cell()` given a docstring and a failing test —
so the CS1302 notebooks hand out one function at a time, and the test files are
the failing tests that come with them.

If you change how this package reads the notebook, `nbkit.py` does not
automatically follow — it is a teaching copy, deliberately simpler, and it
documents where it diverges. Keep the divergences honest rather than syncing
them.

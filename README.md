---
name: socratic-watchdog
aliases: [socratic-watchdog]
tags: [project, tool]
stage: playground
---
# 🧠 Socratic Watchdog

A Socratic TTS coding assistant that watches your Jupyter notebook cells,
analyzes them through the lens of the Socratic method, and **speaks guiding
questions** when you go off-track. Stays silent when your code is correct —
and celebrates with confetti when you get it right.

> *"I cannot teach anybody anything, I can only make them think."* — Socrates

## Features

| Feature | Description |
|---|---|
| **`%%socratic`** cell magic | Analyse one cell at a time |
| **`%socratic_watch on`** | Auto-watch every cell you run |
| **`%socratic_task auto`** | Auto-detect task from markdown above |
| **Cell-below test cases** | A code cell below marked `#Test cases` — its `assert` lines become the hidden tests |
| **Auto-generated tests** | `%socratic_generate_tests` — LLM writes test cases from the task |
| **Fast path** | When tests pass, skips LLM entirely — instant silent + confetti |
| **Honest failure** | Tests fail with no LLM available → reports the failure plainly, never false praise |
| **Three TTS backends** | espeak-ng (local robotic, current default) / edge-tts (cloud neural) / kokoro (local neural) |
| **Socratic method** | Never gives answers — only asks guiding questions |
| **Subtitle boxes** | Questions and praise shown as styled UI boxes alongside audio |
| **Confetti + praise** | Random Socratic praise + confetti animation on correct answers |
| **Timing stats** | `%socratic_stats` — per-step wall-clock breakdown |
| **Audio toggle** | `%socratic_audio on` / `off` — enable/disable spoken questions |
| **Model switching** | `%socratic_model` — swap LLM models at runtime |
| **Debug mode** | `%socratic_debug` — per-cell timing, LLM model, TTS backend |
| **Style toggle** | `%socratic_style brief` / `verbose` — direct or playful mentor |
| **Exploration mode** | `%socratic_explore` — free experimentation, no task required |
| **Works everywhere** | JupyterLab, Notebook, Colab, VS Code |

## Quick start

```python
%load_ext socratic_watchdog
%socratic_task Write a function that calculates Fibonacci numbers

%%socratic
def fib(n):
    return fib(n-1) + fib(n-2)  # missing base case!
```

Socrates will (verbally) ask something like:

> *"I see your function calls itself — what condition would stop this recursion?"*

When you fix it and it's correct, you'll get confetti and something like:

> *"Well done! You are thinking clearly."*

## Installation

```bash
pip install socratic-watchdog
```

The package has **zero required dependencies beyond `ipython`**. TTS backends and LLM access are configured via optional extras or environment variables:

```bash
# Optional: install a specific TTS backend
pip install socratic-watchdog[edge-tts]   # Microsoft neural voices (cloud, free)
pip install socratic-watchdog[kokoro]     # Local neural TTS (82M model, needs torch)
```

No extra pip install needed for espeak-ng — the package calls the system `espeak-ng` binary directly if it's on your PATH. Install it via `apt install espeak-ng` (Linux) or `brew install espeak-ng` (macOS).

### LLM access

The package calls an LLM to analyse your code. Two backends, auto-fallback:

| Backend | Env var | Default? |
|---|---|---|
| **Direct API** (DeepSeek / OpenAI-compatible) | `SOCRATIC_LLM_BACKEND=direct` | ✅ default |
| **Hermes CLI** (`hermes chat -q`) | `SOCRATIC_LLM_BACKEND=hermes` | fallback |

The direct API path tries these env vars in order: `SOCRATIC_LLM_API_KEY` → `DEEPSEEK_API_KEY` → `OPENAI_API_KEY`. If none are set, it falls back to Hermes CLI automatically.

## How it works

```
┌─────────────┐    ┌──────────────┐    ┌───────────┐    ┌──────────┐
│ Cell runs   │ →  │ Capture      │ →  │ LLM with  │ →  │ On track?│
│ (source +   │    │ source code  │    │ Socrates  │    │ → SILENT │
│  traceback) │    │ + error      │    │ persona   │    │ Off track│
└─────────────┘    └──────────────┘    └───────────┘    │ → TTS Q  │
                                                        └──────────┘
```

When **test cases** are set (via a `#Test cases` cell below or `%socratic_generate_tests`), there's a fast path: if the code passes all tests, the LLM is skipped entirely — instant silent + confetti.

The **Socrates persona** instructs the LLM to:
1. Never give direct answers or show corrected code
2. Ask exactly one guiding question
3. Reference something specific in the student's code
4. Stay completely silent when correct

## Commands

| Magic | What it does |
|---|---|
| `%%socratic` | Run a cell with Socratic analysis |
| `%socratic_task <goal>` | Describe your coding goal |
| `%socratic_task auto` | Auto-detect task from markdown cell above |
| `%socratic_task clear` | Remove the task |
| `%socratic_task` | Show current task |
| `#Test cases` cell below | Author-written tests: a code cell *below* your `%%socratic` cell, marked `#Test cases` |
| `%socratic_generate_tests` | LLM auto-generates hidden test cases from the task |
| `%socratic_watch on` | Watch every cell automatically (3 s debounce) |
| `%socratic_watch off` | Stop auto-watching |
| `%socratic_audio` | Toggle TTS audio on/off |
| `%socratic_model` | Choose LLM model at runtime |
| `%socratic_debug` | Per-cell timing/trace breakdown |
| `%socratic_style` | Switch between brief (direct) and verbose (playful) |
| `%socratic_explore` | Free experimentation mode (no task needed) |
| `%socratic_auto_tests` | Auto-gen tests on every `%socratic_task` |
| `%socratic_off` | Quick alias to stop |
| `%socratic_reset` | Clear task, tests, and cached notebook data |
| `%socratic_clear_cache` | Clear generated-tests cache |
| `%socratic_cache` | List/inspect cached generated tests |
| `%socratic_stats` | Show timing breakdown of last analysis |
| `%socratic_help` | Show usage help |

### Test cases

Author-written test cases let Socrates check correctness deterministically — no LLM
guesswork. Put them in a **code cell directly below** your `%%socratic` cell, marked
with `#Test cases` (also accepted: `#Tests`, `#test_cases`). The watchdog scans that
cell, runs its `assert` lines as hidden tests, and caches them on disk keyed by task:

```python
%load_ext socratic_watchdog
%socratic_task Write a function that checks if a number is even
```

```python
%%socratic
def is_even(n):
    return n % 2 == 0
```

```python
#Test cases
assert is_even(0) == True
assert is_even(1) == False
assert is_even(42) == True
assert is_even(-7) == False
```

- Pass all tests → instant silent + confetti, LLM skipped entirely.
- Fail with an LLM available → the failures are fed to the LLM for a sharper question.
- Fail with **no** LLM available → reported as a plain failure (never false praise).

The `#Test cases` cell runs like any other cell, so students see it. To keep tests
hidden, use LLM-generated tests instead:

```python
%socratic_task Write a function that reverses a string
%socratic_generate_tests  # LLM writes 4-6 assert statements, cached on disk
```

### Audio toggle

```python
%socratic_audio off   # TTS disabled
%socratic_audio on    # TTS re-enabled
```

### Model switching

```python
%socratic_model         # show numbered list of providers
%socratic_model 2       # pick #2 from the list
%socratic_model gpt-4o  # custom model name
```

### Debug mode

```python
%socratic_debug on   # shows test counts, LLM model+timing, TTS backend+timing
%socratic_debug off
```

### Style toggle

```python
%socratic_style brief    # direct questions, no preambles
%socratic_style verbose  # patient, playful mentor tone (default)
```

### Exploration mode

```python
%socratic_explore on    # Socrates encourages free experimentation
%socratic_explore off   # back to task-driven mode
```

### Auto-generated tests

```python
%socratic_auto_tests on   # auto-gen hidden tests on every %socratic_task
%socratic_auto_tests off
%socratic_clear_cache     # clear cached tests (useful for demos)
%socratic_cache           # inspect the generated-tests cache
```


## Configuration

### TTS

| Env var | Default | Description |
|---|---|---|
| `SOCRATIC_TTS_BACKEND` | `espeak` | `espeak` (local robotic, ~0.03 s, current default), `edge-tts` (cloud neural, ~3 s, planned default), or `kokoro` (local neural, ~3.8 s) |
| `SOCRATIC_TTS_VOICE` | `en-US-AndrewNeural` | Voice for edge-tts |
| `SOCRATIC_KOKORO_VOICE` | `af_heart` | Kokoro voice pack (`af_heart`, `am_adam`, `bm_lewis`, etc.) |
| `SOCRATIC_ESPEAK_VOICE` | `en-us` | Voice for espeak-ng |

### LLM

| Env var | Default | Description |
|---|---|---|
| `SOCRATIC_LLM_BACKEND` | `direct` | `direct` (API call) or `hermes` (CLI). Both auto-fallback to the other. |
| `SOCRATIC_LLM_BASE_URL` | `https://api.deepseek.com` | API base URL (also reads `OPENAI_BASE_URL`) |
| `SOCRATIC_LLM_API_KEY` | — | API key (also reads `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`) |
| `SOCRATIC_LLM_MODEL` | `deepseek-chat` | Model name |
| `SOCRATIC_LLM_TIMEOUT` | `30` | Seconds to wait for LLM |
| `HERMES_PROFILE` | `dev` | Hermes profile used when `SOCRATIC_LLM_BACKEND=hermes` |

### Other

| Env var | Default | Description |
|---|---|---|
| `SOCRATIC_DEBUG` | (unset) | Set to `1` to print timing breakdown after each analysis |
| `SOCRATIC_DEBOUNCE` | `3.0` | Seconds between auto-watch analyses (min gap in `%socratic_watch on` mode) |
| `SOCRATIC_TESTS_CACHE` | `~/.hermes/socratic_tests_cache/` | Cache directory for auto-generated test cases |

## Architecture

```
socratic_watchdog/
├── _core.py        # Core engine (no IPython deps — works anywhere)
│   ├── SocraticWatchdog.analyze()       # prompt → LLM → question/silence
│   ├── SocraticWatchdog.speak()         # text → TTS (kokoro/edge-tts/espeak) → Audio
│   ├── SocraticWatchdog.generate_tests() # LLM → test cases (disk-cached)
│   └── _call_llm()                      # direct API + hermes CLI fallback
├── magics.py       # IPython magics + post-run hook
│   ├── %%socratic, %socratic_task, %socratic_generate_tests, etc.
│   ├── _post_run_cell_hook              # auto-watch mode
│   ├── confetti animation               # canvas confetti on correct answers
│   └── 90+ Socratic praise phrases      # random praise on correct answers
└── __init__.py     # %load_ext entry point
```

## License

MIT

## Related

- [edge-tts][1] — free TTS engine
- [Kokoro][4] — local neural TTS (82M model)
- [Hermes Agent][3] — the agent framework

[1]: https://github.com/rany2/edge-tts
[3]: https://hermes-agent.nousresearch.com
[4]: https://huggingface.co/hexgrad/Kokoro-82M

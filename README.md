# 🧠 Socratic Watchdog

A Socratic TTS coding assistant that watches your Jupyter notebook cells,
analyzes them through the lens of the Socratic method, and **speaks guiding
questions** when you go off-track. Stays silent when your code is correct.

> *"I cannot teach anybody anything, I can only make them think."* — Socrates

## Features

- **%%socratic** cell magic — analyser one cell at a time
- **%socratic_watch on** — auto-watch every cell you run
- **TTS audio delivery** — speaks questions via espeak-ng (default), edge-tts, or kokoro
- **Socratic method** — never gives answers, only asks guiding questions
- **Works everywhere** — JupyterLab, Notebook, Colab, VS Code

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

## Installation

```bash
# Minimal install (uses espeak-ng for TTS — requires apt install espeak-ng)
pip install socratic-watchdog

# Or with a TTS backend of your choice:
pip install socratic-watchdog[edge-tts]   # cloud neural TTS (free, no API key)
pip install socratic-watchdog[kokoro]     # local neural TTS (82M model, needs torch)
```

Requires either:
- **Hermes CLI** (`hermes chat -q`) — available on the DIVE platform
- **OpenAI-compatible API** — set env vars: `SOCRATIC_LLM_BASE_URL`, `SOCRATIC_LLM_API_KEY`

> **Note:** The default TTS backend is `espeak-ng`. Install it via `apt install espeak-ng` (Linux) or `brew install espeak-ng` (macOS). Switch backends with `SOCRATIC_TTS_BACKEND=espeak|edge-tts|kokoro`.

## How it works

```
┌─────────────┐    ┌──────────────┐    ┌───────────┐    ┌──────────┐
│ Cell runs   │ →  │ Capture      │ →  │ LLM with  │ →  │ On track?│
│ (source +   │    │ source code  │    │ Socrates  │    │ → SILENT │
│  traceback) │    │ + error      │    │ persona   │    │ Off track│
└─────────────┘    └──────────────┘    └───────────┘    │ → TTS Q  │
                                                        └──────────┘
```

The **Socrates persona** instructs the LLM to:
1. Never give direct answers or show corrected code
2. Ask exactly one guiding question
3. Reference something specific in the student's code
4. Stay completely silent when correct

## Commands

| Magic | What it does |
|---|---|
| `%%socratic` | Run a cell with Socratic analysis |
| `%socratic_task ...` | Describe your coding goal |
| `%socratic_watch on` | Watch every cell automatically |
| `%socratic_watch off` | Stop auto-watching |
| `%socratic_off` | Quick alias to stop |
| `%socratic_reset` | Clear the task description |
| `%socratic_help` | Show usage help |

## Configuration

| Env var | Default | Description |
|---|---|---|
| `SOCRATIC_TTS_BACKEND` | `espeak` | TTS engine: `espeak` (default, local), `edge-tts` (cloud neural), or `kokoro` (local neural) |
| `SOCRATIC_TTS_VOICE` | `en-US-AndrewNeural` | Edge-TTS voice |
| `SOCRATIC_LLM_TIMEOUT` | `30` | Seconds to wait for LLM |
| `SOCRATIC_LLM_BASE_URL` | `https://api.deepseek.com` | API base URL |
| `SOCRATIC_LLM_API_KEY` | — | API key (or use Hermes CLI) |
| `SOCRATIC_LLM_MODEL` | `deepseek-chat` | Model name |

## Architecture

```
socratic_watchdog/
├── _core.py        # Core engine (no IPython deps — works anywhere)
│   ├── SocraticWatchdog.analyze()    # prompt → LLM → question/silence
│   ├── SocraticWatchdog.speak()      # text → TTS (espeak/edge-tts/kokoro) → Audio
│   └── _call_llm()                   # hermes CLI or direct API
├── magics.py       # IPython magics + post-run hook
│   ├── %%socratic, %socratic_task, etc.
│   └── _post_run_cell_hook           # auto-watch mode
└── __init__.py     # %load_ext entry point
```

## License

MIT

## Related

- [jupyter-hermes-personalities][2] — the Socrates personality source
- [edge-tts][1] — free TTS engine
- [Hermes Agent][3] — the agent framework

[1]: https://github.com/rany2/edge-tts
[2]: https://github.com/dive4dec/jupyter-hermes-personalities
[3]: https://hermes-agent.nousresearch.com

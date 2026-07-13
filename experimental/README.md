# Experimental features (proposals — nothing here is wired into the main package yet)

These are **standalone modules** you can import and try. They do **not** edit
`_core.py` or `magics.py` — each file's docstring shows the one line you'd add to
connect it, so nothing in the shipping package changes until we decide it should.

Written in response to the colleagues' notes on **affect-aware voice agents,
speech-to-text, and turning this into an in-notebook AI chat.**

---

## What's here, in plain terms

| File | What it does | The colleague ask it answers |
|---|---|---|
| `tts_coqui.py` | A **better-sounding, emotion-aware voice** for Socrates using [Coqui TTS](https://github.com/coqui-ai/tts). Praise sounds warm, a firm hint sounds firm. | "better open-source TTS than Edge TTS… emotion expressed through voice" |
| `stt_whisper.py` | **Talk instead of type** — transcribes your microphone into text with faster-whisper (uses the GPU if you have one). | "STT… transcribing our voice to send to the agent… needs GPU" |
| `notebook_chat.py` | A plain **`%ask <question>`** chat that answers directly (not Socratic), and can *see your whole notebook including the test cells below*. Optionally reads the answer aloud. | "talk to the AI in the notebook… replace jupyter-ai magics… complete view of the notebook including the test cells to come" |

Put `stt_whisper` + `notebook_chat` + any TTS backend together and you get a
**full in-notebook verbal AI chat**: speak → transcribe → LLM → spoken answer.

---

## Setup (each is optional and independent)

```bash
# Better voice
pip install TTS

# Voice input (add a CUDA GPU for speed; works on CPU with the "tiny"/"base" model)
pip install faster-whisper

# The %ask chat needs nothing extra — it reuses the package's existing LLM client
```

Then, in a notebook:

```python
%load_ext socratic_watchdog
from experimental.notebook_chat import load_ipython_extension as chat
chat(get_ipython())

%ask why does my function recurse forever?
%ask --speak explain list comprehensions
```

To use the Coqui voice, add one branch to `speak()` (shown in `tts_coqui.py`)
and set `SOCRATIC_TTS_BACKEND=coqui`.

---

## How do I configure the API key and endpoint? Does it use Hermes?

**All three reuse the exact same LLM config the main package already uses** — you
set it once and everything (Socratic mode + `%ask`) picks it up. Checked in
order, first match wins:

| What | Env vars (first wins) | Default |
|---|---|---|
| API key | `SOCRATIC_LLM_API_KEY` → `DEEPSEEK_API_KEY` → `OPENAI_API_KEY` | (none → chat says "no LLM configured") |
| Endpoint | `SOCRATIC_LLM_BASE_URL` → `OPENAI_BASE_URL` | `https://api.deepseek.com` |
| Model | `SOCRATIC_LLM_MODEL` → `OPENAI_MODEL` | `deepseek-chat` |

```bash
export SOCRATIC_LLM_API_KEY="sk-..."
export SOCRATIC_LLM_BASE_URL="https://your-endpoint/v1"
export SOCRATIC_LLM_MODEL="your-model"
```

**Hermes:** not yet — the package talks to any **OpenAI-compatible** endpoint. If
Hermes exposes (or is fronted by) an OpenAI-compatible `/chat/completions` URL,
point `SOCRATIC_LLM_BASE_URL` at it and it Just Works, no code change. A native
Hermes backend (and the "reuse the Hermes we have on dive / self-learning" idea)
is designed in [`../HERMES_INTEGRATION.md`](../HERMES_INTEGRATION.md).

**TTS/STT keys:** none. `tts_coqui` and `stt_whisper` run **fully locally** — no
key, no endpoint. Only the LLM chat needs a key.

---

## Feature suggestions (ranked: value / effort)

1. **`%ask` chat as the headline feature.** Cheapest win — the file already
   works. It reframes the package from "TTS quiz tool" to "in-notebook AI you
   can ask anything, grounded in your whole notebook." Ships behind an import,
   zero risk to Socratic mode. → *This is the strongest case for the rename.*
2. **Rename the package.** As the colleague said, TTS is the least useful side.
   Candidates: `notebook-mentor`, `socratic-notebook`, `jupyter-mentor`. Keep
   `socratic-watchdog` as a PyPI alias so existing installs don't break.
3. **Coqui voice + verdict→emotion.** The watchdog *already knows* correct vs
   off-track, so affect comes almost for free (`emotion_for_verdict`). Needs a
   set of short reference clips (warm/neutral/firm) — that's the only manual bit.
4. **Voice loop (`stt_whisper` + a `%listen` magic).** Browser mic capture is
   the fiddly part (JS sketched in `stt_whisper.py`); the transcription is done.
5. **Mobile-friendly output.** The subtitle boxes are already HTML; audit their
   CSS for small screens and make `autoplay` degrade to a tap-to-play button
   (mobile browsers block autoplay). Small, self-contained.
6. **Non-blocking TTS** (already in `../BACKLOG.md` #7) — matters more once the
   slower neural backends (Coqui/kokoro) are the default; speak on a thread so
   confetti/subtitles render instantly.
7. **Self-learning loop.** The nightly professor report
   (`scripts/nightly_report.py`) already flags stuck loops and answer-leaks;
   feed its findings back into `SOCRATIC_TEST_GEN_SYSTEM` / the persona prompt so
   the agent improves from real student interactions. This is the concrete
   "reuse Hermes on dive" path — Hermes reads the report, edits the prompt seam.

## What's NOT here because it already exists

- **Turn audio on/off** → `%socratic_audio on|off` (shipping).
- **Choose the model** → `%socratic_model` (shipping).
- **Daily log review / flag issues / .md report as a cron job** → already built:
  `scripts/nightly_report.py` + the cron setup in `../HERMES_INTEGRATION.md`.
- **Full-notebook view for Socratic analysis** → `_get_notebook_cells()` already
  reads cells above and below; `notebook_chat` reuses it.

"""Speech-to-text so students can *talk* to Socrates — EXPERIMENTAL, not wired in.

Why this exists
---------------
Right now the conversation is one-way: Socrates speaks, the student types code.
The colleagues want the reverse leg too — transcribe the student's voice into
text and feed it to the agent. That closes the loop into a real verbal chat
(see experimental/notebook_chat.py, which consumes the transcript).

Model choice
------------
faster-whisper (CTranslate2 port of OpenAI Whisper) is the pragmatic pick:
open-source, runs on CPU for the "tiny"/"base" models, and uses the GPU
automatically when one is present (device="cuda"). The colleagues correctly
note STT wants a GPU to feel instant — "small" on CPU is ~real-time, "tiny" is
faster than real-time, and any CUDA GPU makes even "medium" snappy.

Two halves of the problem
-------------------------
1. Get audio bytes. In a notebook the mic lives in the *browser*, not on the
   kernel host. That's a JS/ipywidgets recording step — sketched in
   ``BROWSER_RECORDER_JS`` below, kept out of the Python hot path on purpose.
2. Transcribe bytes -> text. That's ``transcribe`` below, the part that needs
   the GPU and the part this file actually implements.

Setup
-----
    pip install faster-whisper
    # models auto-download on first use to ~/.cache/huggingface

Config (env vars)
    SOCRATIC_STT_MODEL   default "base"   (tiny/base/small/medium/large-v3)
    SOCRATIC_STT_DEVICE  default "auto"   (auto -> cuda if available else cpu)
    SOCRATIC_STT_LANG    default "en"

No API key needed — fully local. To use a hosted Whisper instead (e.g. a Hermes
STT endpoint), that'd be a different adapter; this one is on-box.
"""
from __future__ import annotations

import os
from typing import Optional

_model = None

# Drop this into a notebook cell to capture mic audio browser-side and stash it
# as a base64 WAV the kernel can pick up. Kept as a string, not executed here —
# notebook_chat.py is where it'd be rendered.
BROWSER_RECORDER_JS = r"""
// records ~<seconds>s of mic audio, base64 -> window.__socratic_audio
async function socraticRecord(seconds=5){
  const s = await navigator.mediaDevices.getUserMedia({audio:true});
  const r = new MediaRecorder(s), chunks=[];
  r.ondataavailable = e => chunks.push(e.data);
  r.start(); await new Promise(k=>setTimeout(k, seconds*1000)); r.stop();
  await new Promise(k=> r.onstop=k);
  const b = new Blob(chunks);
  window.__socratic_audio = btoa(String.fromCharCode(...new Uint8Array(await b.arrayBuffer())));
}
socraticRecord();
"""


def _resolve_device(setting: str) -> str:
    if setting != "auto":
        return setting
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        name = os.environ.get("SOCRATIC_STT_MODEL", "base")
        device = _resolve_device(os.environ.get("SOCRATIC_STT_DEVICE", "auto"))
        compute = "float16" if device == "cuda" else "int8"
        _model = WhisperModel(name, device=device, compute_type=compute)
    return _model


def transcribe(audio_path: str) -> Optional[str]:
    """Transcribe a WAV/MP3/etc file to text. Returns the text, or None on error.

    ``audio_path`` is a file on the *kernel* host. For browser mic capture,
    write ``window.__socratic_audio`` (base64) to a temp file first — see
    ``BROWSER_RECORDER_JS`` and ``save_base64_wav``.
    """
    if not audio_path or not os.path.exists(audio_path):
        return None
    try:
        model = _get_model()
        lang = os.environ.get("SOCRATIC_STT_LANG", "en") or None
        segments, _info = model.transcribe(audio_path, language=lang)
        return "".join(seg.text for seg in segments).strip()
    except Exception:
        return None


def save_base64_wav(b64: str, path: str) -> str:
    """Decode the browser recorder's base64 blob to ``path``. Returns ``path``."""
    import base64
    with open(path, "wb") as f:
        f.write(base64.b64decode(b64))
    return path


def demo() -> None:
    """Self-check: device resolution + graceful failure, no model download."""
    assert _resolve_device("cpu") == "cpu"
    assert _resolve_device("cuda") == "cuda"          # honoured verbatim
    assert _resolve_device("auto") in ("cpu", "cuda")  # never raises
    assert transcribe("/no/such/file.wav") is None
    # round-trip the base64 helper
    import base64, tempfile
    p = save_base64_wav(base64.b64encode(b"hi").decode(),
                        tempfile.mktemp(suffix=".wav"))
    assert open(p, "rb").read() == b"hi"
    print("stt_whisper: ok")


if __name__ == "__main__":
    demo()

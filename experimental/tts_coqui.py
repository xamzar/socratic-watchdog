"""Affect-aware TTS backend using Coqui TTS (XTTS-v2) — EXPERIMENTAL, not wired in.

Why this exists
---------------
The colleagues want *emotion expressed through voice*. The current backends
(espeak = robotic, edge-tts = neural but flat, kokoro = neural local) all read
in one monotone. Coqui XTTS-v2 is open-source, runs locally, and can voice-clone
from a short reference clip — which is the lever we use for affect: a "warm"
reference sample makes praise sound warm, a "firm" one makes a hint sound firm.

How to connect it (do NOT edit _core.py yet — this is a proposal)
-----------------------------------------------------------------
In ``SocraticWatchdog.speak`` the dispatch is::

    backend = os.environ.get("SOCRATIC_TTS_BACKEND", DEFAULT_TTS_BACKEND)
    if backend == "espeak": ...
    elif backend == "kokoro": ...

Add one branch::

    elif backend == "coqui":
        from experimental.tts_coqui import speak_coqui
        result = speak_coqui(text, emotion=self._current_emotion)

``_current_emotion`` doesn't exist yet — the watchdog already knows whether it's
praising (correct) or questioning (off-track), so that verdict maps to an
emotion for free. See ``emotion_for_verdict`` below.

Setup
-----
    pip install TTS            # Coqui (torch pulled in; ~2 GB, GPU optional)
    # first call downloads xtts_v2 (~1.8 GB) to ~/.local/share/tts

Config (env vars, same style as the built-in backends)
    SOCRATIC_COQUI_MODEL   default "tts_models/multilingual/multi-dataset/xtts_v2"
    SOCRATIC_COQUI_SPEAKERS_DIR  dir of <emotion>.wav reference clips (6-15s each)
    SOCRATIC_COQUI_LANG    default "en"

No API key or endpoint needed — this is fully local. (LLM API config is a
separate thing; see experimental/README.md.)

Real affect control, if voice-cloning presets aren't enough: Parler-TTS takes a
*text* description of the emotion ("a calm, encouraging teacher"), and
Chatterbox has an `exaggeration` knob. Both are heavier; XTTS is the pragmatic
first step.
"""
from __future__ import annotations

import io
import os
import wave
from typing import Optional

# emotion -> (speed multiplier, reference-clip basename). speed is the only knob
# XTTS exposes cheaply; the reference clip carries the real timbre/affect.
_EMOTION_PRESETS = {
    "neutral":     (1.0, "neutral"),
    "warm":        (0.95, "warm"),      # praise: slower, warmer clip
    "encouraging": (1.0, "warm"),
    "curious":     (1.05, "neutral"),   # a guiding question
    "firm":        (1.05, "firm"),      # escalated / direct hint
}

_pipeline = None  # cached model; loading XTTS costs several seconds


def emotion_for_verdict(verdict: str, attempt: int = 0) -> str:
    """Map the watchdog's existing verdict to an emotion preset name.

    ``verdict`` is what analyze() already produces: "[SILENT]" = correct,
    anything else = a question. ``attempt`` is the hint-ladder counter.
    """
    if verdict.strip().upper().startswith("[SILENT]") or "correct" in verdict.lower():
        return "warm"
    if attempt >= 3:
        return "firm"
    return "curious"


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        from TTS.api import TTS  # heavy import; only when actually speaking
        model = os.environ.get(
            "SOCRATIC_COQUI_MODEL",
            "tts_models/multilingual/multi-dataset/xtts_v2",
        )
        _pipeline = TTS(model)
    return _pipeline


def _reference_wav(basename: str) -> Optional[str]:
    """Path to <basename>.wav in the speakers dir, or None if unset/missing."""
    d = os.environ.get("SOCRATIC_COQUI_SPEAKERS_DIR")
    if not d:
        return None
    path = os.path.join(d, f"{basename}.wav")
    return path if os.path.exists(path) else None


def speak_coqui(text: str, emotion: str = "neutral"):
    """Text -> affect-aware speech. Returns an IPython Audio widget, or None.

    Fails soft (returns None) if TTS isn't installed or synthesis errors — same
    contract as the built-in _speak_* methods, so it's drop-in.
    """
    if not text.strip():
        return None
    speed, ref_name = _EMOTION_PRESETS.get(emotion, _EMOTION_PRESETS["neutral"])
    try:
        from IPython.display import Audio
        tts = _get_pipeline()
        lang = os.environ.get("SOCRATIC_COQUI_LANG", "en")
        kwargs = {"text": text, "speed": speed}
        # XTTS is multilingual + voice-clone; single-speaker models reject these.
        if getattr(tts, "is_multi_lingual", False):
            kwargs["language"] = lang
        ref = _reference_wav(ref_name)
        if ref:
            kwargs["speaker_wav"] = ref
        samples = tts.tts(**kwargs)  # list[float] @ model sample rate
        sr = int(getattr(tts.synthesizer, "output_sample_rate", 24000))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            import array
            wf.writeframes(array.array(
                "h", (int(max(-1.0, min(1.0, s)) * 32767) for s in samples)
            ).tobytes())
        buf.seek(0)
        return Audio(buf.read(), autoplay=True, rate=sr)
    except Exception:
        return None


def demo() -> None:
    """Self-check: pure logic (presets + verdict mapping) with no model download."""
    assert emotion_for_verdict("[SILENT]") == "warm"
    assert emotion_for_verdict("What stops the recursion?", attempt=0) == "curious"
    assert emotion_for_verdict("The base case is missing.", attempt=4) == "firm"
    assert _EMOTION_PRESETS["warm"][0] < 1.0  # praise is slower than neutral
    # speak_coqui must fail soft when TTS/model is absent, never raise.
    assert speak_coqui("", "neutral") is None
    print("tts_coqui: ok")


if __name__ == "__main__":
    demo()

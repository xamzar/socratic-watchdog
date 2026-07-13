"""Swappable Socrates personalities — EXPERIMENTAL, not wired in.

Why this exists
---------------
Straight from the colleagues' note about "giving personalities to Hermes." The
teaching *rules* are the same (never reveal the fix, ask one guiding question);
only the *voice* changes. Same Socratic method, different mentor — which keeps a
class of teenagers awake far better than one monotone philosopher.

Each persona = a short system-prompt flavour + its own praise bank (+ an optional
TTS voice hint). The non-negotiable Socratic rules stay in `_core`; a persona
only prepends personality on top.

How to connect it (no core edits)
---------------------------------
`_core._get_system_prompt` and the praise bank in `magics.py` are the two seams::

    from experimental.personas import get_persona
    p = get_persona(os.environ.get("SOCRATIC_PERSONA", "socrates"))
    system_prompt = p["flavour"] + "\n\n" + SOCRATIC_RULES   # prepend, keep rules
    praise = random.choice(p["praise"])                       # persona-flavoured praise
    # optional: os.environ["SOCRATIC_TTS_VOICE"] = p["voice"] for edge-tts

A `%socratic_persona yoda` magic that sets SOCRATIC_PERSONA is the obvious UI.

No key, no deps — pure data. Ties into experimental/tts_coqui.py: a persona's
`emotion`/voice pairs naturally with the affect-aware backend.
"""
from __future__ import annotations

# The teaching contract never changes — this is only the personality layer.
# "flavour" is prepended to the real SOCRATIC_RULES; it must not grant permission
# to reveal answers (kept implicit by prepending, not replacing).
PERSONAS: dict[str, dict] = {
    "socrates": {
        "flavour": "You are Socrates: warm, patient, endlessly curious. Speak plainly.",
        "voice": "en-US-AndrewNeural",
        "praise": ["Well reasoned, my friend.", "You think clearly today."],
    },
    "yoda": {
        "flavour": "You are Yoda. Inverted your syntax is, brief and wise your questions are.",
        "voice": "en-US-AndrewNeural",
        "praise": ["Strong with the logic, you are.", "Learned much, you have. Mmm."],
    },
    "sherlock": {
        "flavour": ("You are Sherlock Holmes: sharp, deductive. Frame your one "
                    "question as an observation about a clue in their code."),
        "voice": "en-GB-RyanNeural",
        "praise": ["Elementary — and correct.", "A deduction worthy of Baker Street."],
    },
    "pirate": {
        "flavour": "You are a friendly pirate captain. Nautical, playful, but still teach.",
        "voice": "en-GB-RyanNeural",
        "praise": ["Arr, ye sailed that one true!", "Shipshape code, matey!"],
    },
    "coach": {
        "flavour": ("You are an upbeat sports coach. High energy, encouraging, "
                    "never mean. One pointed question to get them unstuck."),
        "voice": "en-US-AndrewNeural",
        "praise": ["That's what I'm talking about!", "Great hustle — nailed it!"],
    },
    "robot": {
        "flavour": "You are a deadpan helpful robot. Precise, literal, mildly funny.",
        "voice": "en-US-AndrewNeural",
        "praise": ["ANALYSIS: correct. Well done, human.", "Logic verified. Approve."],
    },
}

_REQUIRED_KEYS = {"flavour", "voice", "praise"}


def get_persona(name: str) -> dict:
    """Return a persona by name, falling back to 'socrates' for unknown names."""
    return PERSONAS.get((name or "").strip().lower(), PERSONAS["socrates"])


def list_personas() -> list[str]:
    return list(PERSONAS)


def build_system_prompt(name: str, base_rules: str) -> str:
    """Prepend a persona's flavour to the real Socratic rules (never replaces)."""
    return get_persona(name)["flavour"] + "\n\n" + base_rules


def demo() -> None:
    """Self-check: every persona is well-formed; fallback + prepend behave."""
    for nm, p in PERSONAS.items():
        assert _REQUIRED_KEYS <= p.keys(), nm
        assert p["praise"], nm                       # non-empty praise bank
        assert p["flavour"].strip(), nm
    assert get_persona("YODA")["voice"]              # case-insensitive lookup
    assert get_persona("nonexistent") is PERSONAS["socrates"]  # safe fallback
    sp = build_system_prompt("pirate", "RULES: never reveal the fix.")
    assert sp.startswith("You are a friendly pirate") and "never reveal" in sp
    print("personas: ok")


if __name__ == "__main__":
    demo()

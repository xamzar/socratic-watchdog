"""nbkit_ask — L2, the model layer. One wrapper over the chat endpoint.

Swapping models is a kwarg; swapping *providers* is an env var. Nothing above
this layer should ever contain a URL.

Extracted from socratic-watchdog's `_core._call_llm`, which is 40 lines of
stdlib `urllib` and no SDK — worth keeping that way. An OpenAI-compatible
chat-completions endpoint is a JSON POST. A dependency to do a JSON POST is a
dependency you maintain forever so a student can save four lines.

## The capacity problem, which is the actual lesson here

Two endpoints, and they are not interchangeable:

    qwen3.6-27b   via LiteLLM   ~30 concurrent   better answers
    gemma-4-12b   via DiveAI    ~500 concurrent  the one that will answer

One class is ~500 students. Thirty of them get qwen and the rest get a timeout,
so `ask()` tries qwen first and falls through to gemma the moment qwen is
saturated. Under real classroom load almost everyone lands on gemma.

Which is why the notebooks must be written so **gemma is sufficient**. The
fallback is not a footnote to add at the end — it is in the primitive from
notebook 1, and every prompt in the tutorial has to work at the bottom of it.

This is the same shape as `nbkit.get_cells()`: several sources, try in order,
first one that answers wins. Worth pointing at explicitly when teaching it —
students meet the pattern twice and should notice.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Iterator, NamedTuple, Optional

TIMEOUT = int(os.environ.get("NBKIT_TIMEOUT", "60"))

# Retry the *same* endpoint once when it says "too busy" before giving up on
# it — a saturated queue often drains in a second. Anything else moves on
# immediately; a wrong model name will not fix itself.
BUSY_CODES = {429, 502, 503, 504}
BUSY_RETRY_WAIT = 1.5


class Endpoint(NamedTuple):
    """One OpenAI-compatible chat-completions endpoint."""

    name: str
    base_url: str
    model: str
    api_key: str


def endpoints() -> list[Endpoint]:
    """The endpoints to try, best first. Unconfigured ones are dropped.

    Read fresh on every call, so a student can point at a different server
    mid-session without restarting the kernel.

    Set `NBKIT_BASE_URL` / `NBKIT_MODEL` / `NBKIT_API_KEY` to override
    everything with a single endpoint — that is the escape hatch for working
    from Colab against your own key, and it disables the fallback.

    Otherwise the classroom pair, in order:

        NBKIT_LITELLM_BASE_URL  NBKIT_LITELLM_MODEL  NBKIT_LITELLM_API_KEY
        NBKIT_DIVE_BASE_URL     NBKIT_DIVE_MODEL     NBKIT_DIVE_API_KEY

    Base URLs have no default and must be set — see the note at the bottom of
    this file. Model names default to the two the class is served. An empty
    API key is fine; the campus endpoints may not want one.
    """
    solo = os.environ.get("NBKIT_BASE_URL")
    if solo:
        return [Endpoint("custom", solo,
                         os.environ.get("NBKIT_MODEL", "gemma-4-12b"),
                         os.environ.get("NBKIT_API_KEY", ""))]

    found = []
    for name, default_model in (("litellm", "qwen3.6-27b"), ("dive", "gemma-4-12b")):
        base = os.environ.get(f"NBKIT_{name.upper()}_BASE_URL")
        if not base:
            continue
        found.append(Endpoint(
            name, base,
            os.environ.get(f"NBKIT_{name.upper()}_MODEL", default_model),
            os.environ.get(f"NBKIT_{name.upper()}_API_KEY", ""),
        ))
    return found


def _post(ep: Endpoint, body: dict, stream: bool):
    """POST to one endpoint. Returns the open response; caller closes it.

    The version segment is *not* added for you — whatever `base_url` says,
    plus `/chat/completions`. So `NBKIT_DIVE_BASE_URL` needs its own `/v1` if
    the server wants one. (socratic-watchdog has the same rule and the same
    trap.)
    """
    headers = {"Content-Type": "application/json"}
    if ep.api_key:
        headers["Authorization"] = f"Bearer {ep.api_key}"
    req = urllib.request.Request(
        f"{ep.base_url.rstrip('/')}/chat/completions",
        data=json.dumps({**body, "stream": stream}).encode(),
        headers=headers,
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def _body(prompt: str, system: Optional[str], model: str,
          max_tokens: int, temperature: float) -> dict:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return {"model": model, "messages": messages,
            "max_tokens": max_tokens, "temperature": temperature}


def _is_busy(exc: BaseException) -> bool:
    """True if this failure looks like load rather than a mistake of ours."""
    return isinstance(exc, urllib.error.HTTPError) and exc.code in BUSY_CODES


def _attempt(fn, model: Optional[str]):
    """Run `fn(endpoint)` down the endpoint list. Raise if none answer.

    The whole fallback policy lives here, once, so `ask` and `stream` cannot
    drift apart.
    """
    eps = endpoints()
    if not eps:
        raise RuntimeError(
            "No model endpoint configured. Set NBKIT_BASE_URL (plus "
            "NBKIT_MODEL, NBKIT_API_KEY), or the classroom pair "
            "NBKIT_LITELLM_BASE_URL / NBKIT_DIVE_BASE_URL."
        )

    failures = []
    for ep in eps:
        if model:
            ep = ep._replace(model=model)
        for attempt in (1, 2):
            try:
                return fn(ep)
            except Exception as exc:  # noqa: BLE001 — every failure is a fallthrough
                if attempt == 1 and _is_busy(exc):
                    time.sleep(BUSY_RETRY_WAIT)
                    continue
                failures.append(f"  {ep.name} ({ep.model}): {type(exc).__name__}: {exc}")
                break

    raise RuntimeError(
        "Every model endpoint failed:\n" + "\n".join(failures) +
        "\nIf these are all timeouts, the class is saturating the server — "
        "wait and re-run the cell."
    )


def ask(prompt: str, system: Optional[str] = None, model: Optional[str] = None,
        max_tokens: int = 512, temperature: float = 0.7) -> str:
    """Send `prompt` to the model and return its reply as one string.

        >>> ask("Name one Python built-in for sorting.")     # doctest: +SKIP
        'sorted()'

    `system` sets the persona or the rules; `model` overrides the model name
    on whichever endpoint answers. Both optional — a bare `ask("...")` is the
    whole API for notebook 1.

    Raises `RuntimeError` naming every endpoint tried and why each failed.
    That is deliberate: an `ask()` that returns "" on failure produces a
    package that appears to work and silently says nothing, which is the
    hardest possible bug for a beginner to see. A traceback that reads
    "connection refused" is a better teacher.

    Callers that genuinely cannot raise — anything on an automatic trigger —
    catch it at their own layer. See `nbkit_hooks.on_cell_run`.
    """
    def once(ep: Endpoint) -> str:
        with _post(ep, _body(prompt, system, ep.model, max_tokens, temperature),
                   stream=False) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]

    return _attempt(once, model)


def stream(prompt: str, system: Optional[str] = None, model: Optional[str] = None,
           max_tokens: int = 512, temperature: float = 0.7) -> Iterator[str]:
    """Yield the reply in pieces as the model produces it.

        handle = live_display()
        text = ""
        for piece in stream("Explain this code:\\n" + get_current_cell()):
            text += piece
            handle.update(Markdown(text))

    Same arguments and same fallback as `ask`. Use it whenever a wait would
    otherwise be silent — on a 12B model a long answer takes tens of seconds,
    and a frozen cell reads as a crash.

    One caveat the type signature hides: this is a generator, so nothing is
    sent until you start iterating, and the fallback to the next endpoint can
    only happen while connecting. An endpoint that dies mid-answer leaves you
    with a partial reply, not a retry.
    """
    def once(ep: Endpoint) -> Iterator[str]:
        # Connect eagerly so a dead endpoint raises here, inside _attempt,
        # where the fallback can still catch it — not on the first next().
        response = _post(ep, _body(prompt, system, ep.model, max_tokens, temperature),
                         stream=True)
        return _iter_sse(response)

    return _attempt(once, model)


def _iter_sse(response) -> Iterator[str]:
    """Turn a server-sent-events response into a stream of text pieces.

    The wire format is one `data: {json}` line per token-ish chunk, blank
    lines between, and a literal `data: [DONE]` at the end. Anything we can't
    parse is skipped rather than raised — a malformed keepalive should not
    kill an answer that is otherwise arriving fine.
    """
    with response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                return
            try:
                piece = json.loads(payload)["choices"][0]["delta"].get("content")
            except Exception:
                continue
            if piece:
                yield piece


# ── Configuration note ────────────────────────────────────────────────
#
# There are deliberately no default base URLs in this file. The live DiveAI
# and LiteLLM addresses were not in hand when it was written, and a plausible
# guessed URL is worse than none: it fails as a connection error rather than
# as "you forgot to configure this", and it will be copied into student
# notebooks and outlive the guess.
#
# Fill them in via env (a .env next to the notebook works), and once they are
# confirmed, put them in tutorial/README.md — not here.

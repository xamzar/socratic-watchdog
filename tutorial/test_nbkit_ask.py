"""Tests for nbkit_ask — L2, the model layer.

No network. Every test replaces `urllib.request.urlopen`, so what is under
test is the fallback policy and the wire-format parsing, not the server.
"""

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import nbkit_ask  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────

class FakeResponse(io.BytesIO):
    """Enough of an http.client.HTTPResponse for our two readers."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def reply(text):
    return FakeResponse(json.dumps(
        {"choices": [{"message": {"content": text}}]}
    ).encode())


def sse(*pieces, done=True):
    lines = [f'data: {json.dumps({"choices": [{"delta": {"content": p}}]})}'
             for p in pieces]
    if done:
        lines.append("data: [DONE]")
    return FakeResponse(("\n".join(lines) + "\n").encode())


def http_error(code):
    return urllib.error.HTTPError("u", code, "busy", {}, None)


@pytest.fixture
def both_endpoints(monkeypatch):
    """The classroom pair, configured."""
    for k in list(nbkit_ask.os.environ):
        if k.startswith("NBKIT_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NBKIT_LITELLM_BASE_URL", "http://litellm/v1")
    monkeypatch.setenv("NBKIT_DIVE_BASE_URL", "http://dive/v1")
    monkeypatch.setattr(nbkit_ask, "BUSY_RETRY_WAIT", 0)


class Calls(list):
    """The requests that were made. `.queue` is what urlopen will return."""

    def __init__(self):
        super().__init__()
        self.queue = []


@pytest.fixture
def calls(monkeypatch):
    """Record every urlopen and serve queued responses (or raise them)."""
    log = Calls()

    def fake_urlopen(req, timeout=None):
        log.append(req)
        assert log.queue, "urlopen called more times than the test queued for"
        out = log.queue.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(nbkit_ask.urllib.request, "urlopen", fake_urlopen)
    return log


def bodies(calls):
    return [json.loads(r.data) for r in calls]


# ── endpoint configuration ────────────────────────────────────────────

def test_endpoints_are_ordered_best_first(both_endpoints):
    names = [e.name for e in nbkit_ask.endpoints()]
    assert names == ["litellm", "dive"]


def test_endpoint_models_default_to_the_classroom_pair(both_endpoints):
    by_name = {e.name: e.model for e in nbkit_ask.endpoints()}
    assert by_name == {"litellm": "qwen3.6-27b", "dive": "gemma-4-12b"}


def test_unconfigured_endpoints_are_dropped(monkeypatch):
    for k in list(nbkit_ask.os.environ):
        if k.startswith("NBKIT_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NBKIT_DIVE_BASE_URL", "http://dive/v1")
    assert [e.name for e in nbkit_ask.endpoints()] == ["dive"]


def test_solo_override_replaces_the_pair_and_disables_fallback(monkeypatch,
                                                               both_endpoints):
    monkeypatch.setenv("NBKIT_BASE_URL", "http://mine/v1")
    monkeypatch.setenv("NBKIT_MODEL", "gpt-whatever")
    eps = nbkit_ask.endpoints()
    assert len(eps) == 1
    assert (eps[0].name, eps[0].model) == ("custom", "gpt-whatever")


def test_endpoints_are_reread_every_call(monkeypatch, both_endpoints):
    assert len(nbkit_ask.endpoints()) == 2
    monkeypatch.delenv("NBKIT_LITELLM_BASE_URL")
    assert len(nbkit_ask.endpoints()) == 1


def test_no_configuration_is_an_explanatory_error(monkeypatch, calls):
    for k in list(nbkit_ask.os.environ):
        if k.startswith("NBKIT_"):
            monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="NBKIT_BASE_URL"):
        nbkit_ask.ask("hi")


# ── ask ───────────────────────────────────────────────────────────────

def test_ask_returns_the_message_content(both_endpoints, calls):
    calls.queue.append(reply("sorted()"))
    assert nbkit_ask.ask("how do I sort?") == "sorted()"


def test_ask_hits_the_first_endpoint_only_when_it_works(both_endpoints, calls):
    calls.queue.append(reply("ok"))
    nbkit_ask.ask("q")
    assert len(calls) == 1
    assert calls[0].full_url == "http://litellm/v1/chat/completions"


def test_ask_appends_chat_completions_without_inventing_a_version(both_endpoints,
                                                                  calls):
    """base_url is used verbatim — the /v1 has to come from the env var."""
    calls.queue.append(reply("ok"))
    nbkit_ask.ask("q")
    assert calls[0].full_url == "http://litellm/v1/chat/completions"


def test_system_prompt_goes_on_the_system_role(both_endpoints, calls):
    calls.queue.append(reply("ok"))
    nbkit_ask.ask("q", system="You are Socrates.")
    msgs = bodies(calls)[0]["messages"]
    assert msgs[0] == {"role": "system", "content": "You are Socrates."}
    assert msgs[1]["role"] == "user"


def test_no_system_prompt_means_no_system_message(both_endpoints, calls):
    calls.queue.append(reply("ok"))
    nbkit_ask.ask("q")
    assert [m["role"] for m in bodies(calls)[0]["messages"]] == ["user"]


def test_model_kwarg_overrides_the_endpoint_default(both_endpoints, calls):
    calls.queue.append(reply("ok"))
    nbkit_ask.ask("q", model="gemma-4-12b")
    assert bodies(calls)[0]["model"] == "gemma-4-12b"


def test_api_key_is_sent_only_when_set(monkeypatch, both_endpoints, calls):
    calls.queue.append(reply("ok"))
    nbkit_ask.ask("q")
    assert "Authorization" not in calls[0].headers

    monkeypatch.setenv("NBKIT_LITELLM_API_KEY", "secret")
    calls.queue.append(reply("ok"))
    nbkit_ask.ask("q")
    assert calls[1].headers["Authorization"] == "Bearer secret"


# ── the fallback, which is the whole point of this layer ──────────────

def test_a_busy_endpoint_is_retried_once_before_moving_on(both_endpoints, calls):
    calls.queue.extend([http_error(429), reply("second try")])
    assert nbkit_ask.ask("q") == "second try"
    assert len(calls) == 2
    assert all(c.full_url.startswith("http://litellm") for c in calls)


def test_saturated_litellm_falls_through_to_dive(both_endpoints, calls):
    """30 concurrent slots, ~500 students. This is the common case, not the
    edge case."""
    calls.queue.extend([http_error(429), http_error(429), reply("from gemma")])
    assert nbkit_ask.ask("q") == "from gemma"
    assert calls[-1].full_url == "http://dive/v1/chat/completions"


def test_a_non_busy_failure_moves_on_immediately(both_endpoints, calls):
    """A bad model name will not fix itself in 1.5 seconds — don't wait."""
    calls.queue.extend([http_error(400), reply("from gemma")])
    assert nbkit_ask.ask("q") == "from gemma"
    assert len(calls) == 2


def test_a_timeout_falls_through_too(both_endpoints, calls):
    calls.queue.extend([TimeoutError("timed out"), reply("from gemma")])
    assert nbkit_ask.ask("q") == "from gemma"


def test_every_endpoint_failing_names_every_endpoint(both_endpoints, calls):
    calls.queue.extend([http_error(500), http_error(500)])
    with pytest.raises(RuntimeError) as e:
        nbkit_ask.ask("q")
    msg = str(e.value)
    assert "litellm (qwen3.6-27b)" in msg
    assert "dive (gemma-4-12b)" in msg
    assert "saturating" in msg


def test_failure_raises_rather_than_returning_empty(both_endpoints, calls):
    """An ask() that returns '' builds a package that looks like it works and
    silently says nothing — the worst bug a beginner can be handed."""
    calls.queue.extend([http_error(500), http_error(500)])
    with pytest.raises(RuntimeError):
        nbkit_ask.ask("q")


# ── stream ────────────────────────────────────────────────────────────

def test_stream_yields_the_pieces_in_order(both_endpoints, calls):
    calls.queue.append(sse("Hello", ", ", "world"))
    assert list(nbkit_ask.stream("q")) == ["Hello", ", ", "world"]


def test_stream_sets_the_stream_flag(both_endpoints, calls):
    calls.queue.append(sse("x"))
    list(nbkit_ask.stream("q"))
    assert bodies(calls)[0]["stream"] is True


def test_ask_does_not_set_the_stream_flag(both_endpoints, calls):
    calls.queue.append(reply("x"))
    nbkit_ask.ask("q")
    assert bodies(calls)[0]["stream"] is False


def test_stream_stops_at_done_and_ignores_trailing_junk(both_endpoints, calls):
    calls.queue.append(FakeResponse(
        b'data: {"choices":[{"delta":{"content":"a"}}]}\n'
        b'data: [DONE]\n'
        b'data: {"choices":[{"delta":{"content":"never"}}]}\n'
    ))
    assert list(nbkit_ask.stream("q")) == ["a"]


def test_stream_skips_keepalives_and_unparseable_lines(both_endpoints, calls):
    calls.queue.append(FakeResponse(
        b'\n'
        b': keepalive\n'
        b'data: not json at all\n'
        b'data: {"choices":[{"delta":{}}]}\n'
        b'data: {"choices":[{"delta":{"content":"survived"}}]}\n'
        b'data: [DONE]\n'
    ))
    assert list(nbkit_ask.stream("q")) == ["survived"]


def test_stream_connects_eagerly_so_the_fallback_can_still_fire(both_endpoints,
                                                                calls):
    """If connecting were deferred to the first next(), a dead endpoint would
    raise past _attempt and there would be no fallback at all."""
    calls.queue.extend([http_error(503), http_error(503), sse("from gemma")])
    assert list(nbkit_ask.stream("q")) == ["from gemma"]
    assert calls[-1].full_url == "http://dive/v1/chat/completions"

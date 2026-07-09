# Hermes integration — ideas & design notes

> Status: **exploration**, not committed work. This is a menu of ways Socratic
> Watchdog could talk to a Hermes agent backend, ranked roughly by
> value-per-effort. Nothing here is built yet. Pick, don't implement all.

## Context we're building on

- Socratic already calls an **OpenAI-compatible** LLM over plain HTTPS
  (`_core._call_llm`), defaulting to DeepSeek. Swapping in another backend is a
  ~30-line change, not a rewrite.
- A Hermes fleet already exists on the core VM (`~/.hermes`, foreman + 8
  specialists, DeepSeek-backed) — the *same model family* Socratic uses. So
  Hermes isn't a new brain, it's an **orchestration + memory + evaluation layer**
  around the brain we already call.
- **Doc-drift to fix regardless:** the README advertises a
  `SOCRATIC_LLM_BACKEND=hermes` CLI fallback. That backend **does not exist in
  the code.** Either build it (idea 1) or drop the claim.

## The ladder of integration (cheapest first)

### 1. Hermes as an LLM backend  ·  effort: S  ·  value: M
Make good on the README. Add a `hermes` branch to `_call_llm` that shells out to
`hermes chat -q` (or hits the fleet's HTTP endpoint) instead of DeepSeek direct.
One new method, gated by `SOCRATIC_LLM_BACKEND=hermes`, same return contract.
**Why it matters:** routes every question through Hermes, so everything below
(logging, memory, evaluation) becomes possible. This is the foundation rung.

### 2. Hermes as the question *reviewer*  ·  effort: M  ·  value: M-H
Before a question is spoken, a Hermes "pedagogy" specialist grades it against the
Socratic rules: *does it leak the answer? is it one question? does it reference
the student's actual code?* If it fails, regenerate. Cheap quality gate that
catches the LLM's worst misses (giving away the fix) before a student sees them.
Runs async so it doesn't slow the happy path.

### 3. Session memory + nightly professor report  ·  partially BUILT
The enabling primitive now exists: every `analyze()` appends one JSON line to a
daily log (`_core._log_session`, on by default, `SOCRATIC_SESSION_LOG=off` to
disable). `scripts/nightly_report.py` reads a day's log and emits a Markdown
report: per-student stats, stuck-loop detection, offline-LLM flags, and — when an
API key is present — an **LLM answer-leak review** that flags any question that
gave away the fix instead of guiding.

Run it by hand, or nightly via cron:

```cron
# 6am daily: yesterday's report, mailed/saved for the professor
0 6 * * *  cd /path/to/socratic-watchdog && .venv/bin/python scripts/nightly_report.py --out reports/$(date -d yesterday +\%F).md
```

**Where Hermes comes in next:**
- Swap `review_answer_leaks()`'s LLM call for a Hermes *pedagogy specialist* so
  the leak-judging and the report prose come from an agent with classroom memory,
  not a one-shot completion.
- Push the JSONL onto the Hermes bus/vault so an agent builds a cross-session
  **learner model** (which concepts each student repeatedly trips on) instead of
  the per-day snapshot the script sees today.
- Have the cron job be a Hermes routine rather than raw crontab, so the report
  can trigger follow-ups (e.g. flag a stuck student to the professor in Slack).

### 4. Hermes as the test-case auditor  ·  effort: M  ·  value: H  ·  seam BUILT
Author-written tests (the `#Test cases` cell) are trusted — a human wrote them.
**LLM-generated tests are the risk**, and `generate_tests()` makes one blind
call: nothing checks the asserts before a student hits them. The classic trap is
a test that's *technically true but pedagogically wrong* — e.g. for a beginner
`add(a, b)` task the model emits `assert add(0.1, 0.2) == 0.3`, which fails on
floating-point rounding. The assert is "correct"; the student is baffled by
something the exercise never asked them to care about.

A Hermes **test-auditor** closes this loop:
1. Read recently generated tests (from the session log + the on-disk cache).
2. Judge each against the task: is it in scope? does it rely on float equality,
   exotic types, or edge cases the task never mentions? does it trip a correct
   beginner solution?
3. When it spots a *systematic* mistake, **amend the test-generation system
   prompt** to forbid it — then future generations avoid it. No code change.

The seam for step 3 already exists: `_core._test_gen_system()` resolves the
generator's system prompt as **`SOCRATIC_TEST_GEN_SYSTEM` env → an override file
(`~/.hermes/socratic-sessions/test_gen_system.txt`) → the built-in default.**
The auditor just writes that file. The built-in default already warns against
float-equality and out-of-scope tests; the auditor's job is to catch the
mistakes we *didn't* anticipate and add them.

**Guardrails (same spirit as idea 5):** the auditor *proposes* a new prompt; a
human approves before it goes live. Keep every version of the file (it's the
generator's "genome") so a bad amendment can be rolled back, and so you can see
exactly which prompt produced which tests. This is idea 5's evolution loop, but
scoped to the *test generator* — where "good" is far more measurable (does the
test trip a known-correct solution?) than for the Socratic questioner, which
makes it the safest place to try adaptive prompts first.

### 5. The evolutionary system-prompt tuner  ·  effort: L  ·  value: ?  ← your idea
Your instinct is right, and it has a rigorous name: this is **online
prompt optimization** — a multi-armed bandit (or light genetic search) over
system-prompt variants, scored by a fitness function. The "biological" part is
sound *if* we're honest about the fitness signal. Sketch:

- **Population:** N variants of `SOCRATIC_RULES` (tone, strictness, question
  style). Start hand-seeded, then mutate the best (reword a rule, swap an
  example, tighten "never give the answer").
- **Trial:** a Hermes agent runs each variant against a fixed **benchmark set**
  of (task, buggy-code) fixtures — *not* live students at first.
- **Fitness — the hard part.** You cannot measure "did the student learn." Proxy
  signals, combined:
  - *Answer-leak rate* (an LLM judge checks the question never reveals the fix) —
    **minimize.** This is the one that protects students; weight it highest.
  - *Specificity* (does the question cite the student's actual code?) — maximize.
  - *Resolution* (in a simulated student loop, does a follow-up model fix the bug
    after the question?) — maximize, but watch for it rewarding answer-leaking.
  - *Brevity / one-question compliance* — maximize.
- **Selection:** keep top-k, mutate, retire the rest. Bandit (Thompson sampling)
  over variants is simpler and safer than full GA — reach for GA only if the
  search space really is combinatorial.

**Guardrails (non-negotiable, or this hurts learners):**
- Optimize **offline against fixtures**, never silently on live students.
- A variant ships only after a human reads it — an evolved prompt can drift into
  something that games the fitness function (e.g. asks vague questions that never
  "leak" because they say nothing). This is reward-hacking; the judge is fooled,
  the student is failed.
- Version and log every prompt that ever spoke to a student — reproducibility and
  the ability to roll back a bad generation.
- Fixtures must be diverse (topics, bug types, skill levels) or you overfit to
  "reverse a string" and regress on everything else.

**Honest verdict:** high-effort, genuinely interesting, and the payoff is capped
by how good your fitness proxies are. Do idea 3 (session memory) first — it gives
you the *data* to build a real fitness signal instead of a synthetic one.

### 6. Multi-persona tutoring  ·  effort: M  ·  value: M
Hermes already has specialists. Route by task type: a "debugging Socrates" for
tracebacks, a "design Socrates" for architecture questions, a "concept Socrates"
for first-principles. `%socratic_model` already switches models at runtime — this
is the same lever, pointed at Hermes personas instead.

## Suggested order

1 (backend) → 3 (memory) → 2 (reviewer) → 5 (tuner, once memory gives real data).
4 and 6 are independent nice-to-haves you can slot in anytime.

## The one-paragraph pitch for idea 5

"A Hermes agent periodically runs a benchmark of buggy-code fixtures through
several variants of Socrates' system prompt, scores each variant on how well it
guides without ever giving the answer, breeds the winners, and proposes the best
new prompt for a human to approve." That's your biological adjustment — made safe
by keeping it offline, judged, versioned, and human-gated.

# Socratic Watchdog — Feature Backlog

Prioritized proposals queued during an autonomous session (2026-07-10). Top of
the list is highest value / lowest risk. Items marked ✅ were implemented in the
same session; the rest are proposals for review.

## Done this session
- ✅ Honest-failure reporting (`[TESTS_FAILED]`) instead of false praise.
- ✅ Unified test-source indicator (`_announce_test_source`).
- ✅ README spec reconciled with code (`#Test cases` convention, TTS default).
- ✅ `%socratic_help` completeness — add task/style/generate_tests + `#Test cases`.

## Near-term (small, safe, high value)
1. **`%socratic_hint` escalation ladder.** Track attempts per task; after N
   failed cells on the same task, let the question get progressively more
   concrete (Socratic question → leading question → near-answer). One counter
   on the watchdog, reset on pass. Teachers asked for "eventually just tell me".
2. **Session transcript export (`%socratic_export`).** There are already
   `~/socratic-sessions/session-*` dirs — dump the cell/question/verdict history
   to JSON/Markdown for grading and review. Read-only side effect, no core risk.
3. **Configurable debounce.** `_DEBOUNCE_SECONDS` is hard-coded to 3.0. Expose
   via `SOCRATIC_DEBOUNCE` env var + `%socratic_watch on 5`. One-line read.
4. **`#Test cases` marker in help + a `%socratic_check` dry-run** that shows the
   student which tests would run without submitting code.

## Medium (worth a design pass)
5. **Per-student progress state.** Persist which tasks a student has passed
   (keyed by task hash, same cache dir) so a returning session can show a
   progress bar. Ties into #2.
6. **Rubric-mode LLM prompt.** Optional teacher-supplied rubric appended to the
   system prompt so questions target specific learning objectives.
7. **Non-blocking TTS.** Speak in a background thread so the confetti/subtitle
   renders instantly and audio catches up (edge-tts/kokoro are slow).
8. **Colab/VS Code auto-detect fallback.** `detect_task_from_notebook` relies on
   reading the .ipynb from disk; in Colab the file isn't on the local FS. Detect
   the environment and degrade gracefully to explicit-task mode with a clear msg.

## Larger / speculative (needs product sign-off)
9. **Multi-language support** — the persona + praise phrases are English-only.
10. **Classroom dashboard** — aggregate multiple students' session JSON into a
    teacher view (ties into the projects-dashboard PWA already planned).
11. **Adaptive difficulty** — generate follow-up tasks based on what the student
    struggled with.

## Known cleanups / tech debt
- `pyproject.toml` version is `0.3.0`; the honest-failure + indicator work is a
  user-visible behavior change → bump to `0.4.0` before next publish.
- `~/wiki/drafts/socratic-watchdog.md` is empty; either delete it or make it
  point at the repo README (currently the single source of truth for the spec).
- `demo_01_basics.ipynb` is 627 KB (embedded outputs) — strip outputs to shrink
  the repo / PyPI sdist.

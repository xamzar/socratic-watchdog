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
1. ✅ **Hint escalation ladder.** `_attempts` counter per task; questions get
   progressively more concrete (Socratic → leading hint → direct explanation),
   reset on pass. Done this session. Possible follow-up: expose the current
   level in the debug table and let teachers tune the thresholds.
2. **Session transcript export (`%socratic_export`).** There are already
   `~/socratic-sessions/session-*` dirs — dump the cell/question/verdict history
   to JSON/Markdown for grading and review. Read-only side effect, no core risk.
3. ✅ **Configurable debounce.** `SOCRATIC_DEBOUNCE` env var now overrides the
   3.0s default (`magics.py:546`). Follow-up: still no inline `%socratic_watch on 5`
   form — watch only takes `on`/`off`.
4. **`%socratic_check` dry-run** that shows the student which tests would run
   without submitting code. (`#Test cases` marker is already in `%socratic_help`.)

## Medium (worth a design pass)
5. **Per-student progress state.** Persist which tasks a student has passed
   (keyed by task hash, same cache dir) so a returning session can show a
   progress bar. Ties into #2.
6. **Rubric-mode LLM prompt.** Optional teacher-supplied rubric appended to the
   system prompt so questions target specific learning objectives.
7. **Non-blocking TTS.** Speak in a background thread so the confetti/subtitle
   renders instantly and audio catches up (edge-tts/kokoro are slow).
8. ✅ **Colab auto-detect fallback.** `_get_colab_cells` (`_core.py:1025`) reads
   cells via the Colab kernel message API when the .ipynb isn't on local FS.
   Follow-up: VS Code notebooks still fall back to explicit-task mode.

## Larger / speculative (needs product sign-off)
9. **Multi-language support** — the persona + praise phrases are English-only.
10. **Classroom dashboard** — aggregate multiple students' session JSON into a
    teacher view (ties into the projects-dashboard PWA already planned).
11. **Adaptive difficulty** — generate follow-up tasks based on what the student
    struggled with.

## Known cleanups / tech debt
- ✅ Version bumped to `0.4.0` (`pyproject.toml`, `__init__.py`).
- `~/wiki/drafts/socratic-watchdog.md` is empty; either delete it or make it
  point at the repo README (currently the single source of truth for the spec).
- `demo_01_basics.ipynb` is 627 KB (embedded outputs) — strip outputs to shrink
  the repo / PyPI sdist.

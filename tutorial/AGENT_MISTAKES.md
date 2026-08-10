# What your AI gets wrong, and what to say instead

Every mistake below actually happened, in this repo or in
`eventbinder/skillmd-form`, and was fixed in a later commit. None of it is
hypothetical and none of it came from a blog post. The commit hash is there so
you can go and read the damage.

The pattern across all of them is the same: **a model produces code that is
locally correct and globally wrong.** It answers the question you asked and not
the question you meant, because it cannot see the parts of the system you did
not put in the prompt. Every fix below is a sentence you could have written
beforehand.

Each entry is: what happened · why it happens · what to say.

---

## 1 · Before it writes anything

### It invents the shape of your data

> **`a271ec8`** — `nbformat` cell `source` is a *list of lines*. The code
> called `.rstrip()` on it, which crashes on every real notebook.

A model that has seen a thousand notebook snippets has also seen `source` used
as a string, because plenty of code normalises it first. It guesses the common
case. Worse, it then writes a test using *its own* guess, so the test passes and
the bug ships.

> Say: *"Here is a real sample of the data: `<paste one>`. Write the function
> against that exact shape, and make the test use a sample copied from the real
> file, not one you invent."*

### It picks a version floor from vibes

> **`078248e`** — `ipython>=8.0.0`. Every API the package uses has existed since
> 7.x. On Colab, that floor force-upgraded a pinned 7.34 to 9.x, conflicting
> with `google-colab` and destabilising the runtime for anyone who installed it.

"Modern and safe" is the default instinct. It has no idea what your deployment
target already has installed, and it will not ask.

> Say: *"The target environment is `<Colab / the course JupyterHub>`, which has
> `<X>` pinned. Set the lowest floor that supports the APIs actually used, and
> list which API forced it."*

### It builds the thing you named, not the thing you need

> **`b811cc5`** — an entire "optional stack question" feature, reverted whole a
> few commits later. Built exactly to a contract line nobody wanted.

Models do not push back on scope. If the spec says it, they build it.

> Say: *"Before writing anything, tell me what you think this is for and what
> you would cut. Then wait."*

---

## 2 · While it writes

### It casts instead of checking

> **`973f55a`** — DOM values cast straight to `V[]` with `as`. The cast silences
> the type checker; it does not make the value that type. Any unexpected string
> walked straight into application state.

A cast is the shortest path to a green compiler, and green is what the model is
optimising for.

> Say: *"Never use a type assertion to satisfy the checker. If a value crosses a
> boundary you don't control, validate it and drop what doesn't match."*

### It fires an async call and walks away

> **`973f55a`** — `navigator.clipboard.writeText(...)` unawaited. It rejects in
> insecure or permission-blocked contexts, which means an unhandled rejection
> and a user who thinks they copied something and did not.

The happy path works on the developer's machine. Every time.

> Say: *"Await every promise. For anything that can fail from outside — network,
> clipboard, permissions, disk — tell me what the user sees when it fails."*

### It writes a layer that exists to be undone

> **`f1337fd`** — five named ID maps whose only use was spelling out their own
> catalogue: numbers converted to strings and back for no reason. Removing the
> indirection *revealed a real bug it had been hiding* — a deselect returning
> `null` that the coercion had been quietly swallowing.

This is the most expensive one on the list, because it does not look like a
mistake. It looks like architecture. And it makes bugs invisible rather than
loud.

> Say: *"Don't add an abstraction with one implementation, or a mapping used
> only by the thing that defines it. If you think one is needed, say why first."*

### It leaves the scaffolding in

> **`4261785`** — `next.svg`, `vercel.svg`, `window.svg`, unused font imports,
> the starter README. All still there, weeks in.

`create-next-app` output is not your project. The model treats it as existing
code to be preserved.

> Say: *"Delete every file from the starter template that this project does not
> use. List what you deleted."*

---

## 3 · When it changes code that already exists

**This is the dangerous category.** Everything above produces code that is
merely wrong. These produce code that works when you test it and fails later,
for someone else.

### It adds a fast path and forgets what the slow path did

> **`1bc88a7`** — a new live-notebook path (Colab, `jupyter-mcp-cli`) returned
> early without scanning the cell below for `#Test cases`. On Colab, hand-written
> tests were silently ignored and the LLM regenerated them every time. The old
> disk path did the scan. The new one just… didn't.

The model was asked to add a source. It added a source. Nobody asked it to
enumerate everything the existing branch did on the way to its return, so it
didn't.

> Say: *"You are adding a second path to `<function>`. List everything the
> existing path does between entry and return, then confirm the new path does
> each one or say why it shouldn't."*

### It skips the code that would have cleared stale state

> **`ce13848`** — on a cache hit, `_generate_timings` was never reset, so the
> debug table showed LLM timings from a previous, unrelated call as if they
> belonged to this one.

Same root cause as above, one level subtler. The fast path skipped the work
*and* skipped the bookkeeping that the work happened to do.

> Say: *"What state does the slow path overwrite as a side effect? Reset it
> explicitly on the fast path."*

### It writes each call site from scratch

> **`18407e1`** — the same upward-scan loop written twice; the sha256 cache-key
> and path computation written **four** times. Also, in this repo today:
> `_extract_error` and `_extract_error_from_info` are the same function twice,
> differing only in which attribute they read the exception off.

Each individual request was answered well. No request ever spanned two call
sites, so nothing ever noticed.

> Say: *"Before you write this, grep for anything that already does it. If
> something is close, change that instead of adding a second copy."*

### It moves files and breaks the importers

> **`126eaf8`, `81444be`, `267a1b3`** — three separate "fix imports" /
> "dependencies fix" commits. Also `c8d91c8` → `2b42e2c` → `35563fd`: moving
> types into a `types/` folder took three attempts.

A reorganisation is a whole-repo operation. The model does it file by file.

> Say: *"This is a move, not an edit. Update every importer in the same change
> and show me the build passing before you stop."*

---

## 4 · When it thinks it's finished

### It documents features that no longer exist

> **`18407e1`** — `%socratic_tests` had been removed. It was still in the
> docstrings, still in the startup banner students see, and still in the
> offline hint telling them what to try next.

Nothing fails. Tests pass. The only person who finds out is a student typing a
command that doesn't exist.

> Say: *"You removed `<X>`. Grep the whole repo for its name — including
> markdown, docstrings, and user-facing strings — and remove or update every hit."*

### It writes a paragraph where a line would do

> **`b7ad400`** (99 lines of comments → 20), **`fdf46a8`**, **`d015521`** —
> three separate commits whose entire content is deleting comments the model
> wrote.

Explanatory comments are the house style of generated code. They restate the
line below in English, they go stale first, and they bury the two comments that
actually matter.

> Say: *"Comment only what the code cannot say: why this approach over the
> obvious one, and what breaks if it changes. Never restate what the line does."*

### It never says "no"

The one counter-example in this history is worth as much as the rest of the
list. From **`973f55a`**:

> *"Skipped the review's buildRequest-cloning item: the payload arrays are
> already typed readonly, so callers can't mutate them, and the output is
> consumed locally. Cloning would guard a scenario the types already prevent."*

That is a review item examined, judged unnecessary, and declined with a reason.
Models will not do this unless invited — they treat every suggestion as an
instruction, including wrong ones.

> Say: *"If any of this is unnecessary, skip it and tell me why. I would rather
> have four changes and a reason than six changes."*

---

## The short version

Paste this above a task and most of the list stops happening:

```
Before you write code:
  - Grep for anything that already does this. Reuse it.
  - Tell me what you'd cut from my request, then wait.

While writing:
  - No type assertions to satisfy the checker. Validate at boundaries.
  - Await everything. Tell me what the user sees on failure.
  - No abstraction with one implementation.

If you're adding a branch to an existing function:
  - List everything the existing path does before its return.
  - Confirm the new path does each one, or say why it shouldn't.
  - Say what state the old path resets as a side effect.

Before you stop:
  - Grep for every name you removed, including docs and user-facing strings.
  - Comment only what the code can't say.
  - Skip anything unnecessary and tell me why.
```

## Why this list is short

Twelve items, from a hundred-odd commits across two projects. That is the
useful finding.

The failures are not creative. They cluster hard, they repeat across an
unrelated Python package and a TypeScript app, and almost all of them come from
one cause: **the model can only see what is in the prompt.** It cannot see the
other branch of the function, the version pinned on the deployment target, the
four other places the same key is computed, or the banner that mentions the
command you deleted.

So the skill being taught is not prompting. It is deciding what the model needs
to see, and checking the part it couldn't.

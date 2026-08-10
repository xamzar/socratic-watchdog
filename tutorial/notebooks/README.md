# The notebooks

**Try it in the browser, nothing to install:**

[![Open 00 in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/xamzar/socratic-watchdog/blob/main/tutorial/notebooks/00_primitives.ipynb)
&nbsp;**00 · Primitives** — no API key needed, no endpoint, no install. Start here.

[![Open 01 in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/xamzar/socratic-watchdog/blob/main/tutorial/notebooks/01_explainer.ipynb)
&nbsp;**01 · Explainer** — needs a model endpoint; fill in the block in the first cell.

Colab is worth using for notebook 00 specifically, because Colab is the one
place where the disk fallback genuinely does not work — there is no `.ipynb`
file for the kernel to read. `cells_from_colab()` is what answers instead, and
you get to watch that happen rather than take it on trust.

Work through them in order. Each one adds exactly one capability, and each one
ends with something that runs.

| | notebook | you build | new idea |
|---|---|---|---|
| 00 | `00_primitives.ipynb` | eleven functions, from nothing | the kernel does not know about the notebook |
| 01 | `01_explainer.ipynb` | `%%explain` | L2, and why one endpoint isn't enough |
| 02 | *not written* | docstring filler | writing back — idempotency |
| 03 | *not written* | error whisperer | reactive: runs without being called |
| 04 | *not written* | watchdog-lite | state, and not calling the model |
| 05 | *not written* | your own tool | combining L0–L3, and the limits |

A student who stops after 03 has still shipped something real. That property is
deliberate — keep it if you extend the series.

## How each function is taught

Four cells, every time:

1. **Spec** — what it does and the edge case that is easy to get wrong.
2. **Test** — run it, watch it fail. Do not skip this. A test you never saw
   fail is a test you do not know works.
3. **Build** — a stub, and the exact words to hand your AI.
4. **Test again.**

That loop is the actual subject of the course. The functions are the excuse.

## Running them

Notebook 00 needs nothing — no network, no API key. It reads itself off disk,
so **run it from this directory** and save before running the cells that scan
the notebook file.

Notebook 01 onward needs a model endpoint. See
[`../README.md#configuring-the-model`](../README.md) — set
`NBKIT_LITELLM_BASE_URL` and `NBKIT_DIVE_BASE_URL`, or `NBKIT_BASE_URL` for a
single endpoint of your own.

## Before you ask your AI for anything

Read [`../AGENT_MISTAKES.md`](../AGENT_MISTAKES.md). Twelve things models get
wrong, each one taken from a commit in this project that had to be fixed later,
each with the sentence that prevents it. The short version at the bottom is
worth pasting above any task.

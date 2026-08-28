---
name: herdr-pipeline
description: Run a sequential chain of sub-agents in the current Herdr workspace where each stage's output becomes the next stage's input — A feeds B feeds C. Use for coupled, ordered work that must NOT be parallelized: draft → critique → revise, explore → plan → implement → test, extract → transform → validate. Builds on the `herdr` skill for CLI mechanics. Requires HERDR_ENV=1.
---

# herdr-pipeline

Chain sub-agents so each one consumes the previous stage's result. This is the
deliberate opposite of `herdr-fanout`: use it when the parts are **coupled and
ordered** — stage N cannot start until stage N-1's output exists. You (the
orchestrator) carry the payload from one stage to the next; the agents never
talk to each other directly.

Read the `herdr` skill first for CLI conventions, ID rules, agent lifecycle
states, and safety — they apply here and are not repeated.

## Precondition

```bash
test "${HERDR_ENV:-}" = 1
```

If this fails, say you are not inside Herdr and stop.

## When a pipeline is the right shape

Use it when the handoff is real data, not just ordering:

- **draft → critique → revise** — a writer, a reviewer, a reviser.
- **explore → plan → implement → test** — each stage needs the last's artifact.
- **extract → transform → validate** — classic ETL over a document or dataset.

If the stages don't actually pass output — they're just several independent
jobs — that's `herdr-fanout`, not this. If two agents need to go back and forth
(question/answer), that's `herdr-relay`. Keep pipelines to a few meaningful
stages; a chain of ten thin agents loses more to handoff overhead than it gains.

Decide the stages up front: for each, a **role**, the **input** it receives, and
the **output shape** it must produce (so the next stage can consume it). Keep
self-terminating instructions ("…and exit", "quit") out of every stage brief — an
agent that exits closes its pane mid-pipeline.

## One pane per stage, run in order

Give each stage its own pane in the current tab (siblings), or its own tab if you
want a per-stage roster in the tab bar. Panes keep the whole chain on one screen:

```bash
herdr pane split --current --direction right --cwd "$PWD" --no-focus
# read .result.pane.pane_id  -> $pane1
herdr agent start "$(basename "$pane1")" --kind claude --pane "$pane1"
```

**Address each stage by its pane id**, not a custom name — a name set at
`agent start` is cleared when the agent re-registers during init (Claude does
this), breaking a later `agent prompt <name>`.

Then run stages **sequentially**, each with `--wait` (a pipeline has nothing to
do in parallel — blocking is correct here). **Hand payloads off through files,
not the terminal:** Claude/Codex render on the alternate screen, so
`agent read`/`pane read --source recent-unwrapped` returns blank and can't
capture a stage's output. Have each stage write its result to a file and read
the file yourself:

```bash
d=/tmp/pipeline.$$        # a scratch dir for handoffs

# Stage 1 — produce, writing its output to a file
herdr agent prompt "$pane1" "<brief + task input>. Write your full output to $d/draft.md and reply with only that path." --wait --timeout 300000
draft=$(cat "$d/draft.md")     # you read the file, not the terminal

# Stage 2 — consume stage 1's file, produce its own
herdr agent prompt "$pane2" "Critique the draft in $d/draft.md. Write your critique to $d/critique.md and reply with only that path.
<what to check, and the output shape>" --wait --timeout 300000

# Stage 3 — consume $d/critique.md …
```

If a stage shares the cwd, passing the **path** is enough; otherwise pass the
file's contents. As a quick fallback for a short, one-screen result, `herdr pane
read "$paneN" --source visible` shows the current screen — but files are the
reliable default for handoffs.

## Carrying the payload cleanly

The handoff is the whole point, so make it robust:

- **Capture, then verify.** After each stage settles, `agent read` the result
  and confirm it actually contains the artifact the next stage needs before you
  forward it. A stage that ended `blocked` didn't finish — answer it
  (`herdr agent prompt "<name>" "<answer>"`) or stop the chain.
- **Prefer files for big or structured payloads.** Terminal scrollback truncates
  (especially on an agent's alternate screen). For anything large or structured,
  tell the stage to write its output to a temp file and reply with the path,
  then read the file and pass its contents (or the path, if the next agent
  shares the cwd) to the next stage. This is more reliable than scraping the
  transcript.
- **Forward only what the next stage needs**, framed as input — not the raw
  transcript with the agent's thinking. Restate the contract each time.
- **You are the type-checker between stages.** If stage 2's output doesn't match
  what stage 3 expects, fix it or loop stage 2 again rather than passing garbage
  downstream — a bad handoff corrupts everything after it.

## Loops and early exit

A pipeline can bend back on itself: if the critique stage says the draft is
inadequate, re-run the draft stage with the critique as input, then continue.
Cap the loops (e.g. two revise cycles) so it terminates. If any stage fails
irrecoverably, stop and report which stage broke and with what — do not fabricate
a downstream result from a missing input.

## Finish

Report the final stage's output as the deliverable, and briefly what each
intermediate stage contributed so the chain is auditable. You created these
panes/tabs, so you may close them once you've captured every result — but leave
them for inspection unless the user asked for a clean workspace, and close only
the ones from your roster.

Each stage is a full agent with its own token spend; a pipeline pays off when the
staged separation genuinely improves the result (independent critique, isolated
transforms), not as ceremony around work one agent could do in a single pass.

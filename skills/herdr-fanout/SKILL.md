---
name: herdr-fanout
description: Fan a task out across parallel sub-agents, each in its own tab of the current Herdr workspace, then collect and synthesize their results. Use when work splits into independent parts that gain from running side by side — multi-file/multi-area review, several design approaches at once, parallel research, or a batch of similar edits. Builds on the `herdr` skill for CLI mechanics. Requires HERDR_ENV=1.
---

# herdr-fanout

Spin up several coding sub-agents at once, **one per tab in the current
workspace**, dispatch an independent slice of the work to each, then wait,
collect, and synthesize. This is the parallel-orchestration layer on top of the
base `herdr` skill: that skill defaults to a single sibling *pane* and avoids
making tabs; this skill deliberately chooses **tab-per-sub-agent** so each agent
gets a full-width terminal and the workspace's tab bar becomes your roster.

Read the `herdr` skill first for the CLI conventions, ID rules, status
semantics, and safety rules — they all apply here and are not repeated.

## Precondition

```bash
test "${HERDR_ENV:-}" = 1
```

If this fails, say you are not inside Herdr and stop.

## When to fan out (and when not to)

Fan out only when the work is genuinely **decomposable into independent
slices** — the whole point is parallelism, and a sub-agent starts cold with none
of your context.

Good fits:

- **Breadth review** — a different area/file/subsystem per agent.
- **Diverse approaches** — N agents each attempt the same problem from a
  different angle, then you compare (an MVP-first vs risk-first vs
  perf-first design panel).
- **Parallel research** — each agent investigates one question or source.
- **Batch edits** — the same mechanical change across many independent targets.

Do **not** fan out when the parts are tightly coupled or must happen in order,
when one slice's output is another's input (that's a pipeline, do it yourself or
sequentially), or when the coordination overhead clearly exceeds the work. Two
or three well-scoped agents usually beat eight thin ones — prefer fewer, each
with a real slice.

Decide the slices before spawning anything. Write them down as a roster: for
each, a **role label**, a **self-contained brief** (a cold agent sees only what
you type), and **what it must return**.

## Spawn one sub-agent per tab

For each slice, create a tab in the *current* workspace and read the pane id
straight from the response — `result.root_pane.pane_id`:

```bash
out=$(herdr tab create --workspace "$HERDR_WORKSPACE_ID" \
      --cwd "$PWD" --label "reviewer:auth" --no-focus)
pane=$(printf '%s' "$out" | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"]["root_pane"]["pane_id"])')
```

- **`--no-focus`** always, so the user's focus stays in your pane. The tab bar
  still shows the new tab.
- **`--label`** every tab with its role (`reviewer:auth`, `design:mvp`,
  `research:pricing`) — the labels are your at-a-glance dashboard.
- **`--cwd "$PWD"`** shares this checkout. For agents that will *write* in
  parallel and could collide, give each its own git worktree instead — that's
  the `herdr-worktree-agent` skill; read-only reviewers can safely share.

Then start a named agent in that pane and **fire the brief without `--wait`**, so
you can launch the next slice while this one works:

```bash
herdr agent start "reviewer_auth" --kind claude --pane "$pane"   # returns when ready
herdr agent prompt "reviewer_auth" "<the self-contained brief for this slice>"
```

- `agent start` returns only once Herdr detects the agent ready for input;
  agent **names** must match `[a-z][a-z0-9_-]{0,31}` and be unique among live
  agents — use them as your stable target from here on.
- Omitting `--wait` on `agent prompt` submits the task and returns; you collect
  later. Use the `--kind` matching the requested agent (`claude`, `codex`, `pi`,
  `gemini`, `opencode`, …; run `herdr agent` for the list). For genuine
  independence on a review, pick a *different* kind/model per tab.

Keep a table as you go — `{label, tab_id, pane_id, agent_name}` — parsed from
each response, never guessed.

## Dispatch briefs that survive cold context

Each sub-agent has none of your conversation. A one-line instruction gets
one-line-quality work. Every brief states: the exact files/paths and how to see
them; what the slice is and the properties that must hold; what it may **not** do
(e.g. "do not modify files"); and the exact **return shape** you'll collect
(ranked findings with file:line, or a decision with rationale). Tell each agent
to end with a clear, self-contained summary — that final message is what you
read back.

## Wait, collect, synthesize

After firing every brief, loop over your roster and wait each agent to a settled
state, then read its transcript by agent name:

```bash
herdr agent wait "reviewer_auth" --timeout 300000
herdr agent read "reviewer_auth" --source recent-unwrapped --lines 200
```

- `agent wait` with no `--until` settles on `idle`/`done`/`blocked` — the same
  states `agent prompt --wait` uses. Loop over the whole roster; the waits are
  independent, so the total wall-clock is the slowest agent, not the sum.
- A `blocked` agent is asking for input — inspect with `herdr agent get` /
  `agent read`, then either answer it (`herdr agent prompt "<name>" "<answer>"`)
  or note it and move on.
- A timeout is not a failure by itself — read the agent and decide.
- If a completed response is longer than the pane's scrollback can show (common
  when the agent renders on the alternate screen), ask that agent to write its
  full summary to a temp Markdown file and reply with the path, then read the
  file — the base skill's fallback.
- Then **synthesize yourself**: merge and de-duplicate across sub-agents,
  reconcile disagreements by going to the source rather than averaging, and
  treat every sub-agent claim as a claim to verify before you relay it. A result
  from an independent cold agent is input, not a verdict.

## Cleanup

You created these tabs, so you may close them — but only after you've read every
transcript, and prefer to leave them for the user to inspect unless they asked
for a clean workspace. When you do clean up, close only tabs from your roster:

```bash
herdr tab close "$tab_id"
```

Never close tabs, panes, or workspaces you did not create. Report the roster
(labels + tab ids) so the user can open any of them.

## Cost and honesty

Each tab is a full agent with its own token spend and its own tab on screen —
fan out where parallel independent work genuinely pays for the overhead, not by
reflex. If you spawned N and only some finished, say so plainly and report what
you actually collected.

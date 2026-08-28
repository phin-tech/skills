---
name: herdr-relay
description: Mediate a back-and-forth conversation between two agents in adjacent Herdr panes — one asks, the other answers, you relay each turn — until they converge. Use for interactive collaboration rather than one-shot dispatch: an implementer consulting a domain expert, a proposer and a critic iterating, a solver querying an oracle. Builds on the `herdr` skill for CLI mechanics. Requires HERDR_ENV=1.
---

# herdr-relay

Run two agents side by side and carry messages between them, turn by turn, until
they reach a conclusion. Agents in Herdr can't message each other directly — so
**you are the relay**: you read one agent's question, hand it to the other, read
the reply, and hand it back. This is the interactive counterpart to
`herdr-fanout` (one-shot parallel) and `herdr-pipeline` (one-way chain): here the
two participants go **back and forth**.

Read the `herdr` skill first for CLI conventions, agent lifecycle states, and
safety — they apply and are not repeated. The lifecycle detail that makes relay
work: **`blocked`** means Herdr detected the agent asking a question or waiting
for approval — that's your cue to relay.

## Precondition

```bash
test "${HERDR_ENV:-}" = 1
```

If this fails, say you are not inside Herdr and stop.

## When to relay

Use it when two agents genuinely need to iterate:

- **Implementer ↔ domain expert** — a builder that keeps hitting questions only
  a spec-holder can answer.
- **Proposer ↔ critic** — one drafts, the other pushes back, repeat until it
  holds up.
- **Solver ↔ oracle** — one works a problem and consults another for facts or
  checks it can't do itself.

If the interaction is really one-way (produce → consume), use `herdr-pipeline`.
If the two never need to respond to each other, use `herdr-fanout`. Relay costs
the most orchestration per unit work — reserve it for problems where the dialogue
is the point.

## Set up the two participants

Two adjacent panes so both conversations are visible at once:

```bash
herdr pane split --current --direction right --cwd "$PWD" --no-focus
# .result.pane.pane_id -> $impl
herdr agent start "$(basename "$impl")" --kind claude --pane "$impl"

herdr pane split --pane "$impl" --direction down --cwd "$PWD" --no-focus
# .result.pane.pane_id -> $expert
herdr agent start "$(basename "$expert")" --kind codex --pane "$expert"
```

Give the two agents **different kinds/models** when independence is the point
(e.g. an implementer consulting a different model as the expert). **Address each
by its pane id** (`$impl`, `$expert`) for the whole relay — a name set at
`agent start` is cleared when the agent re-registers during init, so
`agent prompt <name>` would fail. The pane ids are your relay addresses.

Brief each on its role and, crucially, on **how to signal you**: tell the asking
agent to end a turn with an explicit, self-contained question when it needs the
other; tell the answering agent to reply with just the answer. A clear turn
boundary is what lets you relay cleanly. Never relay a self-terminating
instruction ("…and exit", "quit") to either agent — it closes that pane and
breaks the dialogue.

## The relay loop

Kick off the initiator, then wait for it to reach a turn boundary — either it
settles (`idle`/`done`) or it `blocked` on a question:

```bash
herdr agent prompt "$impl" "<the task, and: when you need domain input, end your message with a clearly marked question>" --wait --timeout 300000
```

**Reading a turn:** Claude/Codex render on the alternate screen, so scrollback
(`--source recent-unwrapped`) is blank — read the current turn with
`--source visible`, which shows the agent's latest reply. Then loop:

1. **Read the current speaker.** `herdr pane read "$impl" --source visible --lines 60`. Extract the question or the result.
2. **Decide: converged or continue?** If the speaker produced the final answer
   (no open question), exit the loop. Otherwise take its question.
3. **Relay to the other agent** and wait for its reply:
   ```bash
   herdr agent prompt "$expert" "impl asks: <question>. Answer concisely." --wait --timeout 300000
   answer=$(herdr pane read "$expert" --source visible --lines 60)
   ```
4. **Relay the answer back** and let the first agent continue:
   ```bash
   herdr agent prompt "$impl" "expert answered: <answer>. Continue." --wait --timeout 300000
   ```
5. **Repeat** from step 1.

If a reply is longer than one screen, have that agent write the turn to a temp
file and reply with the path, then read the file instead of the screen. If an
agent goes `blocked` instead of settling, that's a mid-turn question or an
approval prompt — `agent get "$pane"` / `pane read "$pane" --source visible` to
see what it wants, then relay it or answer it
(`herdr agent prompt "$pane" "<answer>"`) as appropriate.

## Keep the relay honest and terminating

- **Cap the turns.** Set a max round count and stop when you hit it, reporting
  where they were — two agents can loop indefinitely (endless "one more
  clarification"). If they're not converging, break in and decide yourself.
- **Relay faithfully but framed.** Pass each message as a quoted question/answer
  with a one-line frame ("expert answered: …, continue"), not the other agent's
  whole transcript. Don't inject your own opinion as if it came from the other
  agent; if you need to steer, say it's from you.
- **Detect stalls.** If a relayed prompt returns `agent_prompt_stalled` or an
  agent produces nothing new, read the pane and intervene rather than looping on
  emptiness.
- **You're still the arbiter.** Treat both agents' claims as claims — if the
  expert asserts something load-bearing, verify it before letting the
  implementer build on it.

## Finish

Report the converged outcome, plus a short trace of the exchange (what was asked,
what settled it) so the dialogue is auditable. Close the two panes once you've
captured the result, only if you created them and the user didn't ask to keep the
workspace. Relay is the most expensive of the three orchestration skills — two
live agents plus your turn-by-turn mediation — so use it only where the
back-and-forth genuinely earns its cost.

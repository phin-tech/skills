---
name: pi-review
description: Get an independent second-opinion review from the Pi coding agent (the `pi` CLI). Use for reviewing a git diff / change, or a plan / design doc — when you want a fresh, cold-context critic that has no stake in the work, then verify its findings before relaying them. Requires the `pi` binary on PATH.
---

# pi-review

Runs [Pi](https://github.com/badlogic/pi-mono) — a separate coding agent with its
own model, context and tools — as a read-only reviewer. Its entire value is
**independence**: Pi starts cold, with no memory of the reasoning that produced
the work, so it catches things the author (often you) is blind to.

Two modes: **change review** (a git diff) and **plan review** (a markdown plan
or design doc). Both follow the same shape — *brief Pi well → run it read-only →
verify every finding before you relay it.* For raw `pi` invocation mechanics and
flag details, see the `pi-agent` skill; this skill is about reviewing well.

## The non-negotiables

- **`-p` is required.** Without it Pi opens a TUI and hangs on a PTY nothing is
  attached to. Always `pi -p`.
- **Keep it read-only.** A reviewer that edits is no longer a reviewer. Pass
  `-t read,bash,grep` for change review, `-t read,grep` (or `--no-tools` for a
  pure plan) for plan review. Say "do not modify any files" in the prompt too.
- **Pi cannot see this conversation.** It only sees the tree and what you put in
  the prompt. Everything it needs — paths, the diff, the intent, the properties
  that must hold — goes in the briefing.
- **Every finding is a claim until you check it.** Verify against the code/plan
  yourself before relaying. A false finding passed on as real costs the user
  more than a missed one.
- **Never fabricate a run.** If `pi` is missing, the call fails, or output is
  empty, say so and do the review yourself with the same rigour, labelled as
  your own — not as a second opinion.

## Choosing the reviewer model(s)

Independence is the whole point, and it is strongest when the reviewer is a
**different model family from whoever wrote the code**. If you (Claude) wrote it,
a Claude reviewer shares your blind spots — reach for a different provider.

**Discover, don't hardcode.** Model IDs drift fast (versions bump under you), so
query at review time rather than trusting a name baked into this file:

```bash
pi --list-models                 # everything available on this install
pi --list-models gemini          # fuzzy search by provider or name
pi --list-models latest | grep '~'   # the stable cross-provider aliases
```

Prefer the `~provider/…-latest` aliases — they're stable pointers that survive
version bumps, and they span providers for genuine diversity:

| Alias | Family | Good as a Claude-independent reviewer |
|---|---|---|
| `~google/gemini-pro-latest` | Google | yes — different family |
| `~openai/gpt-latest` | OpenAI | yes — different family |
| `~x-ai/grok-latest` | xAI | yes — different family |
| `~z-ai/glm-latest`, `~moonshotai/kimi-latest` | open | yes — strong, cheaper |
| `~anthropic/claude-opus-latest` | Anthropic | only if the author was *not* Claude |

`--model` takes fuzzy patterns and globs, so `--model '*gemini-pro*'` or
`--model '~openai/gpt-latest'` both resolve. Confirm the pick with a quick
`pi --list-models <pattern>` if unsure it matched.

**Single reviewer (default).** One strong, independent model with
`--thinking high`. Cheapest, and usually enough:

```bash
pi -p --model '~google/gemini-pro-latest' -t read,bash,grep --thinking high "<briefing>"
```

**Multi-model panel (high-stakes changes / plans).** Run the *same briefing*
through 2–3 different families, then cross-check. Do this when the cost of a
missed bug is high and a second token spend is clearly worth it.

```bash
for m in '~google/gemini-pro-latest' '~openai/gpt-latest' '~x-ai/grok-latest'; do
  echo "===== $m =====";
  pi -p --model "$m" -t read,bash,grep --thinking high "<briefing>";
done
```

Then reconcile the outputs yourself:

- A finding **multiple models raise independently** is higher-confidence —
  surface it first.
- A finding **only one model raises** is not wrong, just unconfirmed — still
  verify it against the code; models disagree for real reasons and for dumb ones.
- **De-duplicate** overlapping findings; **contradictions between models** are a
  signal to go read the code and settle it, not to average them.

Regardless of one model or three, every finding still passes through the
verify-before-relay step below — a panel raises coverage, it does not replace
your check.

## Mode A — change review (a git diff)

### 1. Establish what changed (you, before calling Pi)

```bash
git status --short          # untracked files are part of the change
git diff                    # staged + unstaged
git merge-base HEAD main    # the base it diverged from
```

Untracked files never appear in `git diff` — list and read them yourself so you
can point Pi at them.

### 2. Brief Pi

```bash
cd <repo> && pi -p --model <independent-model> -t read,bash,grep --thinking high "<briefing>"
```

Pick `<independent-model>` per *Choosing the reviewer model(s)* above (omit
`--model` only if Pi's default provider is already independent of the author).
The briefing must contain:

- **The exact files**, including untracked ones, and how to see the diff
  (`git diff`, or specific paths).
- **What the change is meant to do**, in two or three sentences.
- **The specific properties that must hold** — the security invariant, the
  lifecycle that must not leak, the protocol that must round-trip. Generic
  "review this code" produces generic findings.
- **Attack directions** worth the effort: auth checks in the wrong order,
  resources not released on every path, concurrency / cancellation, swallowed
  errors, serialization edge cases, and **claims in docs or comments the code
  does not honour**.
- **Constraints**: do not modify files; do not run the app or touch external
  systems; tests may be run read-only.
- **The report shape** (below).

## Mode B — plan / design review (a markdown plan or spec)

### 1. Give Pi the plan and the ground it sits on

A plan is only reviewable against reality. Point Pi at the plan file **and** the
code/interfaces it will touch, so it can check the plan against what exists.

```bash
cd <repo> && pi -p --model <independent-model> -t read,grep --thinking high "Review the plan in @docs/plan.md against the code it touches. <briefing>"
```

Use `@file` to attach files, or paste short plans inline. For a plan with no
codebase to check against, `--no-tools` gives a pure-reasoning critique.

The briefing must contain:

- **What the plan is trying to achieve**, and any constraints it must satisfy
  (deadline, backwards-compat, must-not-break surfaces).
- **What to attack**: unstated assumptions, missing steps, ordering hazards,
  hand-waved "and then wire it up" gaps, failure/rollback paths not considered,
  interfaces the plan assumes but that don't exist, and places the plan
  contradicts the current code.
- **Scope discipline**: review the plan as written; flag scope creep and
  under-specification rather than redesigning it.
- **The report shape** (below).

## Report shape (ask for this in both modes)

Tell Pi explicitly — padding is unwelcome, and if a category is clean, say so:

- **Ranked by severity**, most serious first — don't treat a footgun like a nit.
- Each finding: `file:line` (or plan section), a **concrete failure scenario**
  (inputs / state → wrong outcome), and a suggested direction for the fix.
- An explicit split between **what Pi traced** through the code/plan and **what
  it merely suspects** — so you can tell analysis from guesswork.

## 3. Verify, then report (you)

For each finding, open the cited code or plan section and trace it yourself.
Sort into:

- **Confirmed** — you followed the path; it's real. Say what breaks.
- **Wrong** — the code/plan doesn't do what Pi says. Say so, with the line that
  disproves it. Don't soften it.
- **Out of scope / pre-existing** — real but not from this change. Note and move on.

Where a finding is cheap to settle empirically (a test, a one-line repro), do
that rather than reasoning about it.

Then report to the user: lead with the **verdict** (safe to ship / plan is
sound — and what you'd fix first), then confirmed findings most-severe first,
then briefly what Pi raised that did **not** survive checking (so the user sees
the review's precision), and end with what neither of you could check (no
cluster, no prod data) — unstated gaps are how a clean review misleads.

## Cost and honesty

Each call is a separate agent with its own token spend. Use it where a genuine
second opinion is worth more than doing the pass yourself. Treat Pi's output as
a claim, not a verdict.

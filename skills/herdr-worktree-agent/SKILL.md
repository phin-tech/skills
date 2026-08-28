---
name: herdr-worktree-agent
description: Spawn a sub-agent in a fresh Git worktree (its own Herdr workspace and checkout) so it can edit code in isolation without colliding with your tree or other agents, then diff and merge its work back. Use when delegating real write-work — a feature, a fix, a refactor — or running several parallel writers safely. Builds on the `herdr` skill for CLI mechanics. Requires HERDR_ENV=1 and a Git repo.
---

# herdr-worktree-agent

Delegate write-work to a sub-agent that operates in its **own git worktree** — a
separate checkout on its own branch, opened as its own Herdr workspace — so its
edits never touch your working tree and never collide with other agents. When
it's done, you review its branch, then diff and merge it back on your terms.

This makes first-class the isolation that `herdr-fanout` only gestures at: any
time sub-agents will *write* in parallel, give each a worktree instead of sharing
one checkout. Read the `herdr` skill first for CLI conventions, agent lifecycle,
and safety rules — they apply and are not repeated.

## Precondition

```bash
test "${HERDR_ENV:-}" = 1
git rev-parse --is-inside-work-tree   # must be a git repo
```

If either fails, say so and stop.

## Create the worktree and its agent

`herdr worktree create` makes a linked worktree checked out to a new branch and
opens it as a **new workspace** (its own tab and pane), returning everything you
need in one JSON response:

```bash
out=$(herdr worktree create --cwd "$PWD" \
      --branch "agent/feature-x" --base HEAD \
      --label "feature-x" --no-focus)
```

Parse from `.result`, never predict:

- `.result.root_pane.pane_id`   → where the agent will run (its cwd is the checkout)
- `.result.worktree.path`       → the checkout path on disk (for git commands)
- `.result.workspace.workspace_id` → the worktree's workspace, for cleanup

```bash
pane=$(printf '%s' "$out" | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"]["root_pane"]["pane_id"])')
tree=$(printf '%s' "$out" | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"]["worktree"]["path"])')
ws=$(printf   '%s' "$out" | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"]["workspace"]["workspace_id"])')
```

- **`--branch`** — give it a clear, namespaced branch (`agent/…`). **`--base`**
  sets the fork point (default the current HEAD); pin it to `main` or a specific
  ref when that matters.
- **`--no-focus`** keeps you where you are; the new workspace still appears in
  the sidebar.
- The checkout lives under `~/.herdr/worktrees/<repo>/<branch>`, fully separate
  from your tree.

Then start the agent in that pane and give it the task — this agent *may* edit,
so let it work with `--wait`. **Address it by pane id**, not a custom name (a name
set at `agent start` is cleared when the agent re-registers during init, so a
later `agent prompt <name>` fails):

```bash
herdr agent start "$(basename "$pane")" --kind claude --pane "$pane"
herdr agent prompt "$pane" "<self-contained brief: what to build, the acceptance criteria, and to commit its work on this branch when done>" --wait --timeout 600000
```

Collect this agent's work from **git** (below), not from the terminal — that
sidesteps the alternate-screen read limitation entirely.

## Brief for isolated write-work

A worktree agent starts cold and is about to change code, so be exact:

- **The task and its acceptance criteria** — what "done" means, concretely.
- **That it is on its own branch in an isolated checkout**, and should **commit**
  its work there (so you can review real commits, not just a dirty tree).
- **Scope fences** — which files/areas it may touch; not to run destructive
  commands, push, touch remotes, or exit/quit the agent, unless you said so.
- **What to report** — a summary of what changed and why, and any decisions it
  made, as its final message.

For several parallel writers, repeat this with a distinct branch and label per
agent; each is fully isolated, so they cannot corrupt each other. (Manage the
fleet with `herdr-fanout`'s roster/collect loop, but with worktrees instead of
shared tabs.)

## Review, diff, merge back

The agent's work is on its branch in `$tree`. Inspect it from your own tree
using the worktree path — do **not** blindly merge:

```bash
git -C "$tree" status
git -C "$tree" log --oneline "$(git merge-base HEAD agent/feature-x)"..agent/feature-x
git diff HEAD...agent/feature-x            # what the agent changed vs your base
```

Read the diff and verify it against the acceptance criteria yourself — a
delegated edit is a proposal, not a fact. Then integrate on your terms:

```bash
git merge --no-ff agent/feature-x          # or cherry-pick, or rebase, as you prefer
```

Resolve conflicts yourself; the agent that wrote the branch is not the one
merging it. If the work is wrong or partial, either send the agent a follow-up in
its still-live pane (`herdr agent prompt "$pane" "<fix/continue>"`) or abandon
the branch — cheap, because it never touched your tree.

## Clean up

Once merged (or abandoned) and you no longer need the checkout, remove the
worktree's workspace and checkout:

```bash
herdr worktree remove --workspace "$ws" [--force]
```

`--force` is needed if the checkout has uncommitted changes — only use it once
you're sure nothing there is worth keeping. After removal, prune and delete the
branch from your main repo if you don't want it (`git worktree prune`,
`git branch -d agent/feature-x`). Never remove a worktree or workspace you did
not create, and never `--force`-remove one whose changes you haven't reviewed.

Each worktree is a real checkout plus a full agent — worth it when isolation
genuinely matters (parallel writers, risky refactors, keeping your tree clean),
overkill for a read-only review (share the tree via `herdr-fanout` instead).

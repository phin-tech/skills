---
name: herdr-tab
description: "Spin up a coding agent in a new tab of the current Herdr workspace and hand it a prompt. Invoke as /herdr-tab [name] <prompt> — an optional leading tab name, then whatever you want the agent to do. Use to offload a task into its own tab without leaving your current one. Requires HERDR_ENV=1."
---

# herdr-tab

Open a new tab in the current workspace, start an agent in it, and give it the
prompt — a one-shot "go do this over there." This is the minimal single-agent
convenience on top of the `herdr` skill (read that for CLI conventions, agent
lifecycle, and safety). For multi-agent coordination reach for `herdr-fanout`,
`herdr-pipeline`, `herdr-worktree-agent`, or `herdr-relay` instead.

## Precondition

```bash
test "${HERDR_ENV:-}" = 1
```

If this fails, say you are not inside Herdr and stop.

## Parse the arguments

The invocation is `/herdr-tab [name] <prompt>`. From `$ARGUMENTS`:

- **Optional tab name**, taken (in priority order) from a leading `-n <name>` /
  `--name <name>`, a leading `name:<label>`, or a leading `[label]` in brackets.
  If none of those is present, **auto-derive** a short label from the prompt (a
  few words, kebab-case) so the tab is still legible in the tab bar.
- **The prompt** is everything left after removing the name token. It is the task
  for the agent — pass it through as the user wrote it; do not rewrite it.
- **Optional `--kind <agent>`** anywhere in the args overrides the agent kind
  (default `claude`; run `herdr agent` for the installed kinds).

If the prompt ends up empty, ask the user what the tab should do rather than
launching an idle agent.

## Create the tab and start the agent

```bash
tabout=$(herdr tab create --workspace "$HERDR_WORKSPACE_ID" \
         --cwd "$PWD" --label "<tab name>" --no-focus)
pane=$(printf '%s' "$tabout" | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"]["root_pane"]["pane_id"])')
```

- **`--no-focus`** so the user stays in the current tab; the new tab still shows
  in the tab bar.
- **`--cwd "$PWD"`** so the agent shares the current working directory.

Start the agent in that pane, then **address it by its pane id** for everything
after:

```bash
herdr agent start "$(basename "$pane")" --kind claude --pane "$pane"   # returns when ready
herdr agent get "$pane"                                                 # confirm agent_status is idle
herdr agent prompt "$pane" "<the prompt>"
```

**Target by pane id, not a custom name.** Some agents (Claude among them)
re-register as they finish initializing, which *clears* a name set at
`agent start` — a later `agent prompt <name>` then fails with `agent_not_found`.
The pane id is the stable handle. (The name passed to `agent start` is required
but you don't rely on it afterward.)

**Never put a self-terminating instruction in the prompt** — "…and exit",
"quit", "/exit". A one-shot agent that exits closes its own pane and the tab
vanishes before you can read anything. If the user's prompt contains one, strip
it and mention you did.

## Default behavior: fire, don't block

By default **omit `--wait`** — dispatch the prompt and let the tab work in the
background so the user's current session stays free. Then report:

- the tab name and `tab_id`, and the agent name, so the user can switch to it or
  address it later;
- that it's running in the background.

Offer to collect the result when it's done. Only **wait** if the user clearly
wants the answer brought back inline ("...and tell me what it finds"):

```bash
herdr agent prompt "$pane" "<the prompt>" --wait --timeout 120000
herdr pane read "$pane" --source visible --lines 45      # see below
```

**Reading a TUI agent's answer.** Claude, Codex, and similar render on the
terminal's *alternate screen*, so `--source recent-unwrapped` (scrollback)
comes back **blank** — those rows never enter host scrollback. Use
`--source visible` to see the current screen, which includes the agent's latest
reply. For output longer than one screen, don't scrape at all: tell the agent to
write its full result to a temp file and reply with the path, then read the file.

If `agent start` or the dispatch fails (e.g. `agent_prompt_stalled`), or the
agent goes `blocked` (it hit a permission/approval prompt), inspect
`herdr agent get "$pane"` and `herdr pane read "$pane" --source visible` and
report what happened — don't silently leave a dead or stuck tab.

## Notes

- The agent starts cold — it has none of this conversation. If the prompt leans
  on context the user gave *you*, fold that context into the prompt text before
  dispatching.
- You created this tab; don't close it unless the user asks. Leave it for them to
  watch or reuse.

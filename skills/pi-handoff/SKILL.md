---
name: pi-handoff
description: Hand off a task to the Pi coding agent (the `pi` CLI) as a sub-agent, running in an isolated working directory. Use for second-opinion reviews, independent verification of work another agent did, or specialized git-tree rollbacks. Requires the `pi` binary on PATH.
---

# pi-handoff

Runs [Pi](https://github.com/badlogic/pi-mono) — a separate coding agent with its
own model, context and tools — as a sub-agent. Its value is *independence*: Pi
starts cold, with no memory of the reasoning that produced the work, so it is
worth calling when a fresh adversarial pass matters more than shared context.

## Invocation

`pi` is a single command with flags. There is **no `pi run` subcommand and no
`--workdir` / `--task` / `--non-interactive` flags** — those fail with
`Error: Unknown options`. The working directory is simply the shell's cwd.

```bash
cd <path_to_project> && pi -p "<instruction>"
```

- `-p` / `--print` — non-interactive: process the prompt, print, exit. Always
  use this from an agent; without it `pi` opens a TUI and hangs on a PTY that
  nothing is attached to.
- `cd` first — that is what "isolated working directory" means here. For real
  isolation (Pi must not see or touch the main tree), give it a `git worktree`
  of its own rather than trusting it to stay put.

Useful flags, in rough order of how often they matter:

| Flag | Use |
|---|---|
| `--model <pattern>` | pick the model, e.g. `--model '*opus*'`; supports `provider/id` and fuzzy patterns |
| `--thinking <level>` | `off … low, medium, high, xhigh, max` — raise it for review work |
| `--tools`, `-t <list>` | allowlist tools. `-t read,bash,grep` for a review that must not edit |
| `--no-tools`, `-nt` | pure reasoning, no filesystem access |
| `--mode json` | machine-readable output when you need to parse the result |
| `--no-session` | ephemeral; don't leave a session file behind |
| `--plan` | plan-mode extension: restricted exploration, no writes |

Check `pi --help` before relying on anything not listed here — extensions
register their own flags, so the surface differs per install.

## Calling it well

Pi has none of your context. A one-line instruction produces a one-line-quality
answer. Give it:

1. **Where to look** — exact paths, and `git status` / `git diff` if the work is
   uncommitted (Pi cannot see your conversation, only the tree).
2. **What to attack** — the specific properties that must hold, not "review
   this".
3. **What it may not do** — say "do not modify any files" explicitly when you
   want a read-only pass, and back it with `-t read,bash,grep`.
4. **How to report** — ranked by severity, with file:line and a concrete
   failure scenario; and ask it to separate what it traced from what it
   suspects, so you can tell analysis from guesswork.

## Cost and honesty

Each call is a separate agent with its own token spend, so use it where a second
opinion is genuinely worth more than doing the pass yourself. Treat its output
as a *claim*, not a verdict: verify each finding against the code before acting,
and say plainly when Pi was wrong rather than relaying it as fact.

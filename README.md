# phin-tech skills

A collection of [Agent Skills](https://www.skills.sh/) for AI coding agents
(Claude Code and compatible).

## Install

Install every skill in this repo:

```bash
npx skills add phin-tech/skills
```

Or a single skill:

```bash
npx skills add phin-tech/skills --skill pi-review
```

Skills are also installable by hand — copy a folder from `skills/` into
`~/.claude/skills/`.

These skills get their independence from an **actual different model** — they
shell out to the [Pi](https://github.com/badlogic/pi-mono) coding agent (`pi`
CLI) so a cold-context model with no stake in the work does the review. That's a
stronger form of "second opinion" than persona role-play inside the same model.

## Skills

| Skill | What it does |
|---|---|
| [`pi-review`](skills/pi-review/SKILL.md) | Independent review of a git diff or a plan/design doc via `pi`, with model discovery and an optional multi-model panel, then verified before relaying. |
| [`pi-agent`](skills/pi-agent/SKILL.md) | Delegate any task to Pi as a sub-agent in an isolated working directory — second opinions, independent verification, git-tree rollbacks. Covers the raw `pi` invocation mechanics the review skills build on. |

## Agents

| Agent | What it does |
|---|---|
| [`adversarial-reviewer`](agents/adversarial-reviewer.md) | A Claude Code sub-agent that briefs Pi for a hostile review of a change, then acts as the skeptic of Pi's output — verifying every finding before relaying it. Agent-format (not a skill); copy into `~/.claude/agents/`. |

## Layout

```
skills/
  <skill-name>/
    SKILL.md      # installable via skills.sh
agents/
  <agent>.md      # Claude Code agents; install by hand
```

The `skills/` layout is a flat, depth-3 tree discovered by the
[skills.sh](https://www.skills.sh/) CLI. Agents are not skills, so skills.sh
does not install them — copy them into `~/.claude/agents/` yourself.

## License

MIT — see [LICENSE](LICENSE).

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

## Skills

| Skill | What it does |
|---|---|
| [`pi-review`](skills/pi-review/SKILL.md) | Independent second-opinion review of a git diff or a plan/design doc, run through the [Pi](https://github.com/badlogic/pi-mono) coding agent (`pi` CLI) with model discovery and an optional multi-model panel, then verified before relaying. |

## Layout

```
skills/
  <skill-name>/
    SKILL.md
```

Flat layout, discovered by the [skills.sh](https://www.skills.sh/) CLI (depth-3
scan of `skills/`).

## License

MIT — see [LICENSE](LICENSE).

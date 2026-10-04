# schemaviz

Draw a database schema as one interactive page, and see what a migration changed. A command-line tool plus a skill that
teaches an agent to drive it.

The renderer reads only [DBML](https://dbml.dbdiagram.io), so one tool covers every framework. Exporters write DBML for
SQLAlchemy, Django, SQL dumps, dbt projects and live databases; for anything else the agent reads the code and writes it.

## Install

Pick one. All give you a `schemaviz` command; check with `schemaviz doctor`.

```bash
brew install phin-tech/tap/schemaviz                                                      # a binary, no Python
curl -fsSL https://raw.githubusercontent.com/phin-tech/skills/main/skills/schemaviz/install.sh | sh   # the same binary, checksum-verified
uv tool install git+https://github.com/phin-tech/skills#subdirectory=skills/schemaviz   # or pipx; needs Python 3.9+
python3 skills/schemaviz/schemaviz.py <command>                                          # no install: standard library only
```

The skill (`SKILL.md`) tells the agent to use `schemaviz` when it is on the PATH and the script beside it when not:

```bash
npx skills add phin-tech/skills --skill schemaviz     # or copy this folder into ~/.claude/skills/
```

## Commands

| Command | What it does |
|---|---|
| `schemaviz render schema.dbml --out schema.html` | One self-contained HTML page. `--expect N` fails if the table count is not N. |
| `schemaviz open schema.dbml` | Serve it locally and open the browser; the page reloads when the file changes. |
| `schemaviz open schema.dbml --from v1.4 --to HEAD` | The same for what changed between two revisions, with a revision picker in the page. |
| `schemaviz diff old.dbml new.dbml` | The changes between two files as a page. `--md summary.md` also writes a pull request summary. |
| `schemaviz diff --file docs/schema.dbml --from v1.4 --to HEAD` | The changes between two revisions of a tracked file. |
| `schemaviz publish ... --out-dir site` | A static folder for another tool to upload (see below). |
| `schemaviz sqlalchemy`, `django`, `db`, `sql`, `dbt` | Write DBML from models, a SQL dump, dbt artifacts or a live database. |
| `schemaviz doctor` | What works on this machine. |

### The page

Four tabs: **Tables** (cards, grouped, with types, keys and descriptions), **Relationships** (a map of foreign keys),
**Explore** (click a foreign key to open the table it points at) and **Overview** (everything on a zoomable canvas).
`Note`s become descriptions and a `TableGroup` becomes a coloured section.

### Exporters

```bash
schemaviz sql schema.sql --dialect postgresql --out schema.dbml   # pg_dump --schema-only, structure.sql, sqlite .schema, prisma migrate diff --script
schemaviz dbt target/manifest.json --out schema.dbml              # catalog.json beside it gives real column types
schemaviz sqlalchemy app.models:Base --out schema.dbml            # needs sqlalchemy
schemaviz django mysite.settings --out schema.dbml                # needs django, run in the project's environment
schemaviz db postgresql+asyncpg://user:pw@host/db --out schema.dbml
```

The DBML header records the command, the source (`// Source: database postgresql`) and, for dbt, the tool version and
when the manifest was generated. `diff` warns when its two files came from different sources or tool versions (type
spellings and constraints can differ for that reason alone), and when a file was generated 7+ days before the commit
that holds it (it is probably stale). The warnings appear on stderr, in the page banner and in `summary.md`.

### What changed

Keep the generated DBML in git and compare any two revisions: new tables and columns are green, changed ones amber with
what they were, removed ones red and struck through. A banner counts them. A rename looks like a removal plus an
addition until you confirm it with `--rename old=new` or `--rename table.old=new`; the diff prints hints for likely ones.
Type respellings (`int` / `integer`) and reworded notes are not reported as changes.

### Publishing

`publish` only generates files; uploading is your own tool's job. Compare against the **merge base**, not the tip of
`origin/main`: otherwise tables main added after your branch started show up as dropped. In CI the checkout needs
`fetch-depth: 0` (or at least the base branch fetched).

```bash
schemaviz publish --file docs/schema.dbml --from "$(git merge-base origin/main HEAD)" --to HEAD --out-dir site --url https://example.com/pr-12/
aws s3 sync site/ s3://my-bucket/pr-12/        # or gsutil, gh, a Pages deploy: whatever you already use
```

`site/` holds `index.html` (self-contained; open it anywhere), `summary.md` (paste or post it as a PR comment: counts,
a table of column changes per table, and a Mermaid diagram that GitHub renders), `schema.dbml`, and `manifest.json`
(the file list, sizes, content types and change counts, for an uploader to read). `summary.md` starts with
`<!-- schemaviz -->` so a CI job can find and update its own comment.

## Development

```bash
python3 -m unittest discover -s tests      # standard library only; 32 tests
```

Release: bump `__version__` in `schemaviz.py`, push, then push a tag `schemaviz-v<version>` that matches it.
`.github/workflows/schemaviz-release.yml` builds one binary per platform with PyInstaller (macOS arm64 and Intel, Linux
x64 and arm64, Windows), smoke-tests each, and attaches them with `SHA256SUMS`; `install.sh` verifies the checksum.
Then update the Homebrew tap:

```bash
python3 scripts/homebrew-formula.py 0.1.0 > ../homebrew-tap/Formula/schemaviz.rb   # reads the release's SHA256SUMS
```

0.1.0 was released and installed through all three routes above (Windows binary built, not run).

## Files

| File | Purpose |
|---|---|
| `SKILL.md` | What the agent follows: use the command, get from code to DBML, what to carry over. |
| `schemaviz.py` | The command: renderer, differ, exporters, local server. Standard library only. |
| `template.html` | The page the renderer fills in. Keep it next to the script (an install puts it in `share/schemaviz`). |
| `pyproject.toml` | Makes `uv tool install` / `pipx install` give a `schemaviz` command. |
| `install.sh` | Installs a release binary and verifies its checksum. |
| `scripts/homebrew-formula.py` | Writes the Homebrew formula for a release. |
| `tests/` | Unit tests. |

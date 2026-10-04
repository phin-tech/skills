# schemaviz

Draw a database schema as one interactive HTML page, from any ORM.

The renderer reads only [DBML](https://dbml.dbdiagram.io). The agent's job is to read your models or migrations
(Django, Rails, Prisma, TypeORM, SQLAlchemy, Ecto, ...) and write the DBML; `schemaviz.py` draws it. That keeps the
drawing deterministic and lets one tool cover every framework.

## What you get

A single `.html` file with four tabs:

| Tab | What it is |
|---|---|
| Tables | Every table as a card, grouped into sections, with column types, keys and descriptions. |
| Relationships | A column-per-section map of foreign keys. Click a table to see what it points at and what points at it. |
| Explore | Start from one table. Click a foreign key to open the table it points at on the left; click "Pointed at by" to open children on the right. Drag tables to arrange them. |
| Overview | The whole schema on a zoomable, pannable canvas, placed by a force-directed layout. Click a key to jump to its table. |

Table and column `Note`s in the DBML become the descriptions. A `TableGroup` becomes a coloured section. A table that
most others point at (a tenant or account root) is drawn as a bar so its links don't bury the rest.

## Use

Ask your agent: *"diagram the database"*. It reads the code, writes `schema.dbml`, and runs:

```bash
python3 skills/schemaviz/schemaviz.py render schema.dbml --out schema.html --title "My app"
```

`render` needs only Python 3. Open `schema.html` in a browser. It also accepts a `.json` file; see `SKILL.md`.

## Optional exporters

If the project is SQLAlchemy or Django, or you have a database to point at, the script can write the DBML itself (these need
`pip install sqlalchemy` or `django`, and for `db` a driver such as `asyncpg` or `psycopg`):

```bash
python3 schemaviz.py sqlalchemy app.models:Base --out schema.dbml   # reads comment= and info={"group": ...}
python3 schemaviz.py django mysite.settings --out schema.dbml   # reads help_text; app label becomes the section
python3 schemaviz.py db postgresql+asyncpg://user:pw@host/db --out schema.dbml   # reads COMMENT ON
```

Other tools that emit DBML work too: `prisma-dbml-generator`, `drizzle-dbml-generator`, `@dbml/cli`
(`db2dbml`, `sql2dbml`). Check their output against the code.

## Files

| File | Purpose |
|---|---|
| `SKILL.md` | The instructions the agent follows: how to get from code to DBML, and what to carry over. |
| `schemaviz.py` | The renderer and the optional exporters. |
| `template.html` | The page the renderer fills in. Keep it next to the script. |

## Install

```bash
npx skills add phin-tech/skills --skill schemaviz
```

or copy this folder into `~/.claude/skills/`.

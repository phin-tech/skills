---
name: schemaviz
description: Draw a database schema (tables, columns, keys, relationships, descriptions) as an interactive page, and show what a migration changed between two commits. Use when asked to diagram, visualise or map a database, ORM models, a SQL dump or a dbt project, or to see what changed in a schema. Uses the `schemaviz` command, which also has exporters for SQLAlchemy, Django, SQL, dbt and live databases.
---

# schemaviz

`schemaviz` turns a schema into one interactive page with four tabs: Tables, Relationships, Explore (click a foreign key
to open the table it points at) and Overview (every table on a zoomable map). It can also compare two versions and mark
what changed. Your job is the step before it: get the schema into DBML (a plain-text schema format), then run the command.

## The command

Check `command -v schemaviz`. If it is there, use it. If not, either install it or use the copy next to this file; the
subcommands are identical.

```
brew install phin-tech/tap/schemaviz                                                        # a binary, no Python
uv tool install git+https://github.com/phin-tech/skills#subdirectory=skills/schemaviz     # or pipx; more options: README.md
python <this-skill-folder>/schemaviz.py <command> ...        # no install; Python 3.9+, standard library only
schemaviz doctor                                              # what works on this machine
```

| You want | Run |
|---|---|
| The schema as a file to share | `schemaviz render schema.dbml --out schema.html` |
| The user to explore it now | `schemaviz open schema.dbml` (local server, reloads when the file changes) |
| What a migration changed | `schemaviz open schema.dbml --from v1.4 --to HEAD` (revision picker in the page) |
| The same as a file | `schemaviz diff --file schema.dbml --from v1.4 --to HEAD --out changes.html` |
| Something to attach to a pull request | `schemaviz publish --file schema.dbml --from "$(git merge-base origin/main HEAD)" --to HEAD --out-dir site` (section 5) |
| DBML from code or a database | `schemaviz sqlalchemy`, `django`, `sql`, `dbt`, `db` (section 1) |

`open` keeps running until stopped. Start it in the background, give the user the address it prints, and stop it when
they are done. Add `--no-open` when the user is not at this machine.

## 1. Write the DBML

Use whatever is cheapest and most accurate:

- **A generator that already exists**, if the project has one: `prisma-dbml-generator` (Prisma), `drizzle-dbml-generator`
  (Drizzle), `npx @dbml/cli db2dbml postgres <url>` (a live database), `sql2dbml` (a `.sql` dump). Check its output
  against the code before trusting it.
- **Prisma:** `npx prisma migrate diff --from-empty --to-schema-datamodel schema.prisma --script > schema.sql`, then
  `schemaviz sql schema.sql --dialect postgresql --out schema.dbml`. It needs no database. Count tables with
  `grep -c '^CREATE TABLE' schema.sql`, not `grep -c '^model '`: an implicit many-to-many relation adds an `_AToB` join
  table with no model. Avoid `prisma-dbml-generator` for diffs: it lists Prisma's relation fields (`user`, `team`) as
  columns, which inflated Documenso from 490 real columns to 616.
- **Any SQL schema** (a `pg_dump --schema-only`, Rails `structure.sql`, sqlite `.schema`, or any file of `CREATE TABLE`
  and `ALTER TABLE ... ADD CONSTRAINT` statements): `schemaviz sql schema.sql --dialect postgresql --out schema.dbml`.
  Standard library only. It reads primary and foreign keys, unique constraints and indexes, and `COMMENT ON`. It does not
  replay a folder of incremental migrations; dump the schema after migrating instead.
- **dbt:** `schemaviz dbt target/manifest.json --out schema.dbml`. It uses `catalog.json` beside the manifest for
  the warehouse's real columns and types. Models, seeds and snapshots become tables (ephemeral models are skipped; add
  `--sources` for declared sources). Descriptions become notes, a `relationships` test becomes a foreign key, `unique`
  and `not_null` tests become constraints, and if exactly one column has both it is drawn as the primary key (a dbt convention, not
  something dbt declares; several candidates are left as unique). Contract `foreign_key` constraints are read too. Folders become sections. If you do not have the artifacts, `dbt parse` writes the manifest
  without querying the warehouse (needs dbt-core, an adapter, and a `profiles.yml`; a throwaway DuckDB profile
  worked for five projects), but without a catalog only declared columns appear, with no types.
  `dbt docs generate` makes the catalog and does query the warehouse. On Databricks, prefer the manifest and catalog your CI
  or dbt Cloud job already produced over running dbt yourself. A project with few tests draws few foreign keys.
- **SQLAlchemy:** `schemaviz sqlalchemy app.models:Base --out schema.dbml` (needs sqlalchemy; run it where the
  app's dependencies are installed). Reads `comment=` as the descriptions and `info={"group": "..."}` on a table as its section.
- **Django:** `schemaviz django mysite.settings --out schema.dbml` (needs django; run it where the project's
  dependencies are installed, from the directory holding `manage.py`). Reads every concrete model through the app
  registry, so abstract bases, multi-table inheritance, auto-created many-to-many tables and `db_table` are handled.
  `help_text` becomes the column note, the model docstring's first line the table note, the app label the section.
  Proxy models are skipped. Check the table count against the tables in a migrated database (`dbshell`, then `\dt`), not against the generator itself.
- **A live database:** `schemaviz db <sqlalchemy url> --out schema.dbml`. Reads `COMMENT ON` text.
  Use the same database engine every time you regenerate a tracked file (sqlite and Postgres differ on types and
  constraints), and skip migration bookkeeping tables (it does, e.g. `django_migrations`, `_prisma_migrations`).
  Tested on sqlite only, where SQLAlchemy does not report a column's inline `UNIQUE`; for sqlite use
  `sqlite3 app.db .schema > schema.sql` and the `sql` exporter instead.
- **Anything else (Rails, TypeORM, Eloquent, Hibernate, Ecto, Sequelize, ...):** read the model or migration
  files and write the DBML yourself. Do not guess: every table and column in the output must come from the code.
- **Large schemas (roughly 50+ tables) or declarative data** (a knex/Sequelize schema object, JSON/YAML, Diesel's
  `schema.rs`): write a throwaway script that loads or parses the source and emits DBML, instead of typing it. It is
  faster, and every table still comes from the code.
- **Decorator/class ORMs** (Sequelize, TypeORM, Eloquent): walk the base-class chain, because `id`, timestamps and
  soft-delete columns often come from a parent class or mixin. Skip virtual and counter-cache fields, which are not
  columns. When `@ForeignKey` and `@BelongsTo` disagree, trust `@BelongsTo`. Sequelize and similar default to nullable
  and the migrations often say otherwise, so mark `not null` only where the code or migration says so. Several classes
  can share one table: count distinct table names.
- **Migration-based stacks** (Diesel, Rails, Flyway): migrations are incremental, so take the final shape from
  the generated schema file (`schema.rs`, `schema.rb`, `structure.sql`) and use migrations only to fill gaps.
  Diesel/SeaORM: `joinable!` names the FK column but is omitted when a table has several FKs to the same parent;
  recover those from `REFERENCES` in the migrations.
- **ORMs with no declared FKs** (Go xorm/gorm, many tag-based ORMs): the code
  cannot prove a relationship. This is the one deliberate exception to "do not guess", so infer a `ref` only from a `<table>_id`-style column whose target table exists, plus a
  short explicit alias map for the odd ones (`owner_id` -> `user`), leave out look-alikes (`external_id`, polymorphic
  `ref_id`), and tell the user the rule you used. Table names come from a `TableName()` method or the framework's name
  mapper, not the struct name. Self-references are supported.

What to carry over, in order of how much it helps the reader:

1. Every table and column, with types, `pk`, `unique`, `not null`.
2. Every foreign key, as `ref: > other_table.column` on the column that holds it (`ref: -` when it is one-to-one).
   Many-to-many join tables are just tables with two foreign keys.
3. A `Note:` on each table and each column, taken from the docstring, `help_text`, `comment`, or the code comment
   next to it. Keep the author's words; shorten, don't invent. Leave it out when the code says nothing.
4. `TableGroup "Section name" { table_a table_b }` to cluster tables by what they are for. If the code has no such
   grouping, group by feature using the folder or module the models live in. Skip groups if unsure; the renderer
   groups tables that reference each other.

## 2. DBML you need

```dbml
Table users {
  id integer [pk]
  email varchar(255) [unique, not null, default: 'none', note: 'Apple sign-in may give a relay address']
  household_id integer [not null, ref: > households.id]   // many users, one household
  Note: 'A person who can sign in'
  indexes { (household_id, email) [unique] }               // composite unique; use [pk] for a composite key
  indexes { (household_id, created_at) }                   // plain composite index
}
Table households { id integer [pk] }
Ref: orders.user_id > users.id          // also accepted outside a table; < - <> exist too
TableGroup "Accounts" { users households }
```

Columns are nullable unless `not null` or `pk`. A type with spaces or `<>` (`struct<a:int,b:string>`, `character varying`) goes in double quotes. Names with spaces go in double quotes. `//` starts a comment.
Strings are single-quoted; `'''triple'''` spans lines. Full syntax: https://dbml.dbdiagram.io/docs/

## 3. Render

```
schemaviz render schema.dbml --out schema.html --title "My app"
```

It prints how many tables it found: compare that with the number of distinct tables in the code, and add any that are missing.
Pass `--expect N` to make it fail on a mismatch. Take N from a count that is independent of how you produced the DBML
(`grep -c`, or the tables of a migrated database; not `apps.get_models()` when the generator uses it), or a parser bug and the expected count will agree on the wrong number. Count join tables
that exist in the database (auto many-to-many tables included). It also warns when a `thing_id` column has
no `ref` although a `thing` table exists, which may mean a missed foreign key (many hits are external IDs, so treat it as a prompt to look). A reference to a table that is not in the file is skipped with a warning on stderr; fix the DBML rather than ignore it.

Open the HTML in a browser, or use `schemaviz open schema.dbml` to serve it. A JSON file works in place of DBML for anything easier to emit that way:
`{"tables": [{"name": "users", "comment": "...", "group": "...", "cols": [{"n": "id", "t": "int", "pk": true}, {"n": "org_id", "fk": "orgs.id"}]}]}`.

## 4. Track the schema and see what a migration changed

Commit the DBML next to the code (for example `docs/schema.dbml`) and regenerate it whenever the models or migrations
change. Then any two points in history can be compared without checking anything out or installing the app:

```
schemaviz diff --file docs/schema.dbml --from v1.4 --to HEAD --out changes.html   # tag/commit/branch
schemaviz diff --file docs/schema.dbml --from HEAD --out changes.html            # HEAD vs working tree
schemaviz diff old.dbml new.dbml --old-label before --new-label after            # two plain files
```

The page is the normal four tabs with the changes marked in place: green `+` added, amber `~` changed (with a "was"
line: type, nullability, key, unique, foreign key target), red struck-through `−` removed. A banner counts them and
"Only show changed tables" hides the rest. A history that has no committed DBML yet can still be compared: generate the
DBML at each commit (`git worktree add`, then step 1) and diff the two files.

**Renames.** DBML has no memory, so a rename looks like one removal plus one addition. The diff prints
`hint: possible rename: ...` on stderr when a removed and an added table share most columns, or a removed and an added
column in one table have the same type. Do not accept a hint on its own. Confirm it in the history between the two
revisions: the migrations (Django `RenameModel`/`RenameField`, Rails `rename_table`/`rename_column`, Alembic
`op.rename_table`/`new_column_name`, SQL `ALTER TABLE ... RENAME`), or `git log -M --follow` and `git diff -M` on the model
files. Pass only confirmed renames, then rerun:

```
schemaviz diff --file docs/schema.dbml --from v1.4 --to HEAD \
  --rename users=accounts --rename accounts.created_at=joined_at
```

`old=new` renames a table; `table.old=new` renames a column. Renamed items show as changed, with "renamed from" and any
other change, and a foreign key that only follows a renamed table is not reported as changed.

**Size.** Measured: 306 tables render and lay out comfortably. At 1,530 tables the Tables tab still loads in about 2
seconds (3.5 MB page), but the Overview tab's force layout takes about 50 seconds, freezes the page, and is unreadable at
that scale. There is no filter flag yet, so for a very large project render a DBML you have trimmed to one area
(`grep`/script the tables you want) and tell the user the diagram is partial.

## 5. Share it

HTML does not render in a pull request, so `schemaviz publish` writes a folder: `index.html` (self-contained, for a
bucket, Pages or a CI artifact), `summary.md` (counts, a table of column changes per table, and a Mermaid diagram,
which GitHub renders) and `manifest.json` (file list and change counts). It does not upload anything; hand `site/` to the
tool the project already uses (`aws s3 sync`, `gsutil`, `gh pr comment --body-file site/summary.md`). Post to a pull
request only when the user asks. `--url` puts the hosted address in the summary. Always start the comparison at the
merge base (`git merge-base origin/main HEAD`), not at `origin/main`, or tables main gained since the branch started look
dropped; CI needs `fetch-depth: 0` for that.

## What the renderer decides for you

- A table that most others point at (a tenant or account root) is drawn as a bar rather than as a hub with dozens of
  lines.
- Tables with no `TableGroup` are grouped by the foreign keys between them.
- The page is one file. It loads two Google Fonts and falls back to system fonts when they can't be fetched.

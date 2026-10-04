---
name: schemaviz
description: Draw a database schema (tables, columns, keys, relationships, descriptions) as an interactive HTML page. Use when asked to diagram, visualise or map a database or its ORM models, in any language or framework. You read the code, write DBML, and the bundled renderer draws it.
---

# schemaviz

`schemaviz.py render` turns a DBML file into one self-contained HTML page with four tabs: Tables, Relationships,
Explore (click a foreign key to open the table it points at) and Overview (every table on a zoomable map, laid out by
a force simulation). It needs only Python 3 and nothing installed.

The script and `template.html` sit next to this file; run the script by its path. Your job is the step before it: get the schema into DBML.

## 1. Write the DBML

Use whatever is cheapest and most accurate:

- **A generator that already exists**, if the project has one: `prisma-dbml-generator` (Prisma), `drizzle-dbml-generator`
  (Drizzle), `npx @dbml/cli db2dbml postgres <url>` (a live database), `sql2dbml` (a `.sql` dump). Check its output
  against the code before trusting it.
- **Prisma:** no database needed. In a scratch directory: `npm i prisma@6 prisma-dbml-generator` (tested with Prisma 6;
  not tried with 7), copy `schema.prisma`, add `generator dbml { provider = "prisma-dbml-generator" }` to the **copy**,
  delete its other generators (they need packages you don't have), set dummy values for any `env()` URLs, then
  `npx prisma generate`. Expect noise: `/// @zod...` and `/// [Type]` doc comments become notes and should be dropped,
  `Enum` blocks are ignored by the renderer (enum columns just show the enum name), and `@@map` names are only kept if
  the generator emits them, so compare table names with the `@@map` values. Use `--expect $(grep -c '^model ' schema.prisma)`.
- **SQLAlchemy:** `python schemaviz.py sqlalchemy app.models:Base --out schema.dbml` (needs sqlalchemy; run it where the
  app's dependencies are installed). Reads `comment=` as the descriptions and `info={"group": "..."}` on a table as its section.
- **Django:** `python schemaviz.py django mysite.settings --out schema.dbml` (needs django; run it where the project's
  dependencies are installed, from the directory holding `manage.py`). Reads every concrete model through the app
  registry, so abstract bases, multi-table inheritance, auto-created many-to-many tables and `db_table` are handled.
  `help_text` becomes the column note, the model docstring's first line the table note, the app label the section.
  Proxy models are skipped. Check the table count against the tables in a migrated database (`dbshell`, then `\dt`), not against the generator itself.
- **A live database:** `python schemaviz.py db <sqlalchemy url> --out schema.dbml`. Reads `COMMENT ON` text.
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

Columns are nullable unless `not null` or `pk`. Names with spaces go in double quotes. `//` starts a comment.
Strings are single-quoted; `'''triple'''` spans lines. Full syntax: https://dbml.dbdiagram.io/docs/

## 3. Render

```
python schemaviz.py render schema.dbml --out schema.html --title "My app"
```

It prints how many tables it found: compare that with the number of distinct tables in the code, and add any that are missing.
Pass `--expect N` to make it fail on a mismatch. Take N from a count that is independent of how you produced the DBML
(`grep -c`, or the tables of a migrated database; not `apps.get_models()` when the generator uses it), or a parser bug and the expected count will agree on the wrong number. Count join tables
that exist in the database (auto many-to-many tables included). It also warns when a `thing_id` column has
no `ref` although a `thing` table exists, which may mean a missed foreign key (many hits are external IDs, so treat it as a prompt to look). A reference to a table that is not in the file is skipped with a warning on stderr; fix the DBML rather than ignore it.

Open the HTML in a browser. A JSON file works in place of DBML for anything easier to emit that way:
`{"tables": [{"name": "users", "comment": "...", "group": "...", "cols": [{"n": "id", "t": "int", "pk": true}, {"n": "org_id", "fk": "orgs.id"}]}]}`.

## What the renderer decides for you

- A table that most others point at (a tenant or account root) is drawn as a bar rather than as a hub with dozens of
  lines.
- Tables with no `TableGroup` are grouped by the foreign keys between them.
- The page is one file. It loads two Google Fonts and falls back to system fonts when they can't be fetched.

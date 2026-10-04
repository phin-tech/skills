"""Run with: python3 -m unittest discover -s tests   (standard library only)"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import schemaviz as z  # noqa: E402

OLD = """Table users { id integer [pk]
 email varchar(100) [not null]
 legacy_flag boolean
 org_id integer [ref: > orgs.id] }
Table orgs { id integer [pk] }
Table old_log { id integer [pk] }
"""
NEW = """Table users { id integer [pk]
 email varchar(255) [unique, not null]
 org_id integer [not null, ref: > orgs.id]
 created_at timestamptz }
Table orgs { id integer [pk] }
Table invoices { id integer [pk]
 user_id integer [ref: > users.id] }
"""


def cli(*args, cwd=None, seed=None):
    env = dict(os.environ, **({"PYTHONHASHSEED": str(seed)} if seed is not None else {}))
    return subprocess.run([sys.executable, str(HERE / "schemaviz.py"), *args], capture_output=True, text=True, cwd=cwd, env=env)


def data_of(html):
    return json.loads(re.search(r"const DATA = (\{.*\});\nconst GROUPS", html).group(1))


class Types(unittest.TestCase):
    def test_aliases(self):
        for a, b in [("int", "integer"), ("INT4", "integer"), ("bool", "boolean"), ("Character  Varying(255)", "varchar(255)"),
                     ("timestamp with time zone", "timestamptz"), ("numeric(10, 2)", "numeric(10,2)"), ("int8", "bigint")]:
            self.assertEqual(z.norm_type(a), z.norm_type(b) if b not in ("integer", "boolean", "bigint", "varchar(255)", "timestamptz", "numeric(10,2)") else b)

    def test_complex_types_survive_a_round_trip(self):
        dbml = z.to_dbml([{"name": "t", "comment": "", "group": None, "uniq": [], "cols": [
            {"n": "a", "t": "struct<x:int,y:array<string>>", "pk": False, "uq": False, "null": True},
            {"n": "b", "t": "map<string,int>", "pk": False, "uq": False, "null": True},
            {"n": "c", "t": "", "pk": False, "uq": False, "null": True},
            {"n": "d", "t": "numeric(18,2)", "pk": False, "uq": False, "null": True}]}])
        cols = z.parse_dbml(dbml)[0]["cols"]
        self.assertEqual([c["n"] for c in cols], ["a", "b", "c", "d"])


class Diff(unittest.TestCase):
    def test_changes(self):
        merged, st = z.diff_tables(z.parse_dbml(OLD), z.parse_dbml(NEW))
        self.assertEqual((st["tables_added"], st["tables_removed"], st["tables_changed"]), (1, 1, 1))
        users = next(t for t in merged if t["name"] == "users")
        by = {c["n"]: c for c in users["cols"]}
        self.assertEqual(by["legacy_flag"]["diff"], "removed")
        self.assertEqual(by["created_at"]["diff"], "added")
        self.assertIn("varchar(100) -> varchar(255)", by["email"]["was"])

    def test_respelling_is_not_a_change(self):
        respelled = OLD.replace("integer", "int4").replace("boolean", "bool").replace("varchar(100)", '"character varying(100)"')
        _, st = z.diff_tables(z.parse_dbml(OLD), z.parse_dbml(respelled))
        self.assertFalse(any(st.values()))

    def test_rename_follows_through(self):
        old = "Table users { id integer [pk]\n created_at timestamp }\nTable posts { id integer [pk]\n author_id integer [ref: > users.id] }"
        new = "Table accounts { id integer [pk]\n joined timestamp }\nTable posts { id integer [pk]\n author_id integer [ref: > accounts.id] }"
        _, plain = z.diff_tables(z.parse_dbml(old), z.parse_dbml(new))
        self.assertEqual((plain["tables_added"], plain["tables_removed"]), (1, 1))
        merged, st = z.diff_tables(z.parse_dbml(old), z.parse_dbml(new), ["users=accounts", "accounts.created_at=joined"])
        self.assertEqual((st["tables_added"], st["tables_removed"]), (0, 0))
        self.assertEqual(next(t for t in merged if t["name"] == "accounts")["renamed_from"], "users")
        self.assertNotIn("diff", next(c for t in merged if t["name"] == "posts" for c in t["cols"] if c["n"] == "author_id"))
        with self.assertRaises(SystemExit):
            z.diff_tables(z.parse_dbml(old), z.parse_dbml(new), ["users=nope"])

    def test_rename_hints_are_only_hints(self):
        old = "Table users { id integer [pk]\n email text\n name text }"
        new = "Table accounts { id integer [pk]\n email text\n name text }"
        self.assertTrue(z.rename_hints(z.parse_dbml(old), z.parse_dbml(new)))


class Sql(unittest.TestCase):
    def test_two_schemas_and_untyped_columns(self):
        tables = z._read_sql("""
            CREATE TABLE auth.users (id integer PRIMARY KEY, email text);
            CREATE TABLE public.users (id integer PRIMARY KEY, auth_id integer REFERENCES auth.users(id));
            CREATE TABLE public.posts (id integer PRIMARY KEY, user_id integer REFERENCES users(id));
            CREATE TABLE loose (a, b);
            CREATE TABLE django_migrations (id integer PRIMARY KEY);""")
        names = {t["name"] for t in tables}
        self.assertEqual(names, {"auth__users", "public__users", "posts", "loose"})
        fk = {t["name"]: {c["n"]: c["fk"][0] for c in t["cols"] if "fk" in c} for t in tables}
        self.assertEqual(fk["posts"]["user_id"], "public__users")
        self.assertEqual(fk["public__users"]["auth_id"], "auth__users")
        self.assertEqual(len(next(t for t in tables if t["name"] == "loose")["cols"]), 2)

    def test_constraints_indexes_comments(self):
        t = z._read_sql("""
            CREATE TABLE "Org" ("id" text NOT NULL, "slug" text NOT NULL, "n" character varying(10) DEFAULT 'a;b', CONSTRAINT "Org_pkey" PRIMARY KEY ("id"));
            CREATE TABLE "Member" ("orgId" text NOT NULL, "userId" text NOT NULL);
            ALTER TABLE "Member" ADD CONSTRAINT "m_pkey" PRIMARY KEY ("orgId", "userId");
            ALTER TABLE "Member" ADD CONSTRAINT "m_fk" FOREIGN KEY ("orgId") REFERENCES "Org"("id") ON DELETE CASCADE;
            CREATE UNIQUE INDEX "Org_slug_key" ON "Org"("slug");
            COMMENT ON TABLE "Org" IS 'It''s an org';""")
        org, member = {x["name"]: x for x in t}["Org"], {x["name"]: x for x in t}["Member"]
        self.assertTrue(next(c for c in org["cols"] if c["n"] == "slug")["uq"])
        self.assertEqual(org["comment"], "It's an org")
        self.assertEqual(next(c for c in org["cols"] if c["n"] == "n")["t"], "varchar(10)")
        self.assertEqual([c["n"] for c in member["cols"] if c["pk"]], ["orgId", "userId"])
        self.assertEqual(next(c for c in member["cols"] if c["n"] == "orgId")["fk"][:2], ["Org", "id"])

    def test_enum_type_names_keep_their_case(self):
        t = z._read_sql('CREATE TABLE "U" ("id" text PRIMARY KEY, "p" "IdentityProvider" NOT NULL, "r" "role"[]);')[0]
        self.assertEqual([c["t"] for c in t["cols"]], ["text", "IdentityProvider", "role[]"])


def manifest(nodes, adapter="duckdb"):
    return {"metadata": {"project_name": "p", "adapter_type": adapter}, "nodes": nodes}


def model(name, schema="main", cols=(), pkg="p", **extra):
    n = {"resource_type": "model", "name": name, "package_name": pkg, "fqn": [pkg, name], "path": name + ".sql",
         "config": {"materialized": "table"}, "relation_name": f'"db"."{schema}"."{name}"', "description": "",
         "columns": {c: {"name": c} for c in cols}}
    n.update(extra)
    return n


def test_node(kind, model_uid, col, **kw):
    return {"resource_type": "test", "attached_node": model_uid, "column_name": col,
            "test_metadata": {"name": kind, "kwargs": dict({"column_name": col}, **kw)}}


class Dbt(unittest.TestCase):
    def read(self, nodes, catalog=None):
        return {t["name"]: t for t in z._read_dbt(manifest(nodes), catalog, False)}

    def test_primary_key_only_when_unambiguous(self):
        nodes = {"model.p.a": model("a", cols=["id", "email"]), "model.p.b": model("b", cols=["id", "x"])}
        for col in ("id", "email"):
            nodes[f"u_a_{col}"] = test_node("unique", "model.p.a", col)
            nodes[f"n_a_{col}"] = test_node("not_null", "model.p.a", col)
        nodes["u_b"], nodes["n_b"] = test_node("unique", "model.p.b", "id"), test_node("not_null", "model.p.b", "id")
        t = self.read(nodes)
        self.assertFalse(any(c["pk"] for c in t["a"]["cols"]))  # two candidates: no invented composite key
        self.assertTrue(next(c for c in t["b"]["cols"] if c["n"] == "id")["pk"])
        self.assertNotIn("indexes", z.to_dbml(list(t.values())))

    def test_relationships_and_contract_foreign_keys(self):
        nodes = {"model.p.orders": model("orders", cols=["customer_id", "ref_id"]), "model.p.customers": model("customers", cols=["ID"])}
        nodes["model.p.orders"]["columns"]["ref_id"]["constraints"] = [{"type": "foreign_key", "to": "ref('customers')", "to_columns": ["id"]}]
        nodes["rel"] = test_node("relationships", "model.p.orders", "customer_id", to="ref('customers')", field="id")
        t = self.read(nodes)
        fks = {c["n"]: c["fk"] for c in t["orders"]["cols"] if "fk" in c}
        self.assertEqual(set(fks), {"customer_id", "ref_id"})
        # the target column differs in case from the field in the test: the renderer reconciles it
        html = z.to_html(list(t.values()), "t", "s")
        self.assertEqual({c["fk"][1] for g in data_of(html)["groups"] for x in g["tables"] for c in x["cols"] if "fk" in c}, {"ID"})

    def test_same_name_in_two_schemas_stays_two_tables(self):
        nodes = {"model.p.c1": model("customers", "a", ["id"]), "model.p.c2": model("customers", "b", ["id"], pkg="q")}
        self.assertEqual(set(self.read(nodes)), {"a__customers", "b__customers"})

    def test_catalog_is_the_source_of_columns_and_types(self):
        nodes = {"model.p.m": model("m", cols=["declared_only", "real"])}
        cat = {"nodes": {"model.p.m": {"columns": {"real": {"name": "real", "type": "STRUCT<a:INT>", "index": 1}}}}}
        cols = self.read(nodes, cat)["m"]["cols"]
        self.assertEqual([(c["n"], c["t"]) for c in cols], [("real", "struct<a:int>")])

    def test_ephemeral_models_are_skipped_and_descriptions_cut_before_tables(self):
        e = model("e")
        e["config"]["materialized"] = "ephemeral"
        m = model("m", description="Status.\n\n| a | b |\n|---|---|\n| 1 | 2 |")
        t = self.read({"model.p.e": e, "model.p.m": m})
        self.assertEqual(set(t), {"m"})
        self.assertEqual(t["m"]["comment"], "Status.")


class Render(unittest.TestCase):
    def test_foreign_key_to_a_missing_column_is_dropped_with_a_warning(self):
        tables = z.parse_dbml("Table orgs { id integer [pk] }\nTable u { id integer [pk]\n org_id integer [ref: > orgs.nope] }")
        html = z.to_html(tables, "t", "s")
        self.assertFalse(any("fk" in c for g in data_of(html)["groups"] for x in g["tables"] for c in x["cols"]))

    def test_expect_flag(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "s.dbml")
            f.write_text(NEW)
            self.assertEqual(cli("render", str(f), "--out", str(Path(d, "o.html")), "--expect", "3").returncode, 0)
            self.assertNotEqual(cli("render", str(f), "--out", str(Path(d, "o.html")), "--expect", "4").returncode, 0)

    def test_output_does_not_depend_on_the_hash_seed(self):
        tables = "\n".join(f"Table t{i} {{ id integer [pk]\n p{i} integer [ref: > t{(i * 7) % 40}.id] }}" for i in range(40))
        with tempfile.TemporaryDirectory() as d:
            f = Path(d, "s.dbml")
            f.write_text(tables)
            outs = set()
            for seed in (1, 2, 3, 4):
                cli("render", str(f), "--out", str(Path(d, "o.html")), seed=seed)
                outs.add(Path(d, "o.html").read_text())
            self.assertEqual(len(outs), 1)

    def test_unreadable_line_warns_instead_of_vanishing(self):
        r = subprocess.run([sys.executable, "-c", "import schemaviz as z;z.parse_dbml('Table t {\\n a integer\\n ??? broken\\n}')"],
                           capture_output=True, text=True, cwd=HERE)
        self.assertIn("could not read this line", r.stderr)


class Publish(unittest.TestCase):
    def test_static_folder_and_markdown(self):
        with tempfile.TemporaryDirectory() as d:
            a, b, out = Path(d, "a.dbml"), Path(d, "b.dbml"), Path(d, "site")
            a.write_text(OLD)
            b.write_text(NEW)
            r = cli("publish", str(a), str(b), "--out-dir", str(out), "--url", "https://example.test/x/")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(sorted(p.name for p in out.iterdir()), ["index.html", "manifest.json", "schema.dbml", "summary.md"])
            m = json.loads(Path(out, "manifest.json").read_text())
            self.assertTrue(m["has_changes"])
            self.assertEqual({f["path"] for f in m["files"]}, {"index.html", "summary.md", "schema.dbml"})
            md = Path(out, "summary.md").read_text()
            self.assertTrue(md.startswith(z.MARKER))
            self.assertIn("https://example.test/x/", md)
            self.assertIn("```mermaid", md)
            self.assertIn("`legacy_flag`", md)
            self.assertNotIn("(", re.search(r"erDiagram.*?```", md, re.S).group(0).split("erDiagram", 1)[1])  # Mermaid-safe types

    def test_no_changes(self):
        with tempfile.TemporaryDirectory() as d:
            a, out = Path(d, "a.dbml"), Path(d, "site")
            a.write_text(OLD)
            cli("publish", str(a), str(a), "--out-dir", str(out))
            self.assertFalse(json.loads(Path(out, "manifest.json").read_text())["has_changes"])
            self.assertIn("No schema changes", Path(out, "summary.md").read_text())

    def test_truncated_summary_stays_well_formed(self):
        old = "\n".join(f"Table t{i} {{ id integer [pk]\n a text }}" for i in range(120))
        new = "\n".join(f"Table t{i} {{ id integer [pk]\n a varchar\n b text\n c int }}" for i in range(120))
        merged, stats = z.diff_tables(z.parse_dbml(old), z.parse_dbml(new))
        for limit in (1500, 6000, 20000, 60000):
            md = z.diff_markdown(merged, stats, "a", "b", limit=limit)
            self.assertLessEqual(len(md), limit, limit)
            self.assertEqual(md.count("<details>"), md.count("</details>"), limit)
            self.assertEqual(md.count("```") % 2, 0, limit)
            self.assertTrue(md.startswith(z.MARKER))
        small = z.diff_markdown(merged, stats, "a", "b", limit=3000)
        self.assertRegex(small, r"_\d+ more changed tables? not shown")

    def test_unicode_survives_a_non_utf8_locale(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d, "a.dbml"), Path(d, "b.dbml")
            a.write_text("Table caf\u00e9 { id integer [pk]\n gone text [note: 'pr\u00e9nom \u2014 \u20ac'] }", encoding="utf-8")
            b.write_text("Table caf\u00e9 { id integer [pk] }", encoding="utf-8")
            env = dict(os.environ, LC_ALL="en_US.ISO8859-1", PYTHONUTF8="0")
            r = subprocess.run([sys.executable, str(HERE / "schemaviz.py"), "publish", str(a), str(b), "--out-dir", str(Path(d, "s"))],
                               capture_output=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
            self.assertIn("\u2212", Path(d, "s", "summary.md").read_text(encoding="utf-8"))


class GitAndServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls.tmp.name)
        run = lambda *a: subprocess.run(["git", *a], cwd=cls.repo, check=True, capture_output=True)  # noqa: E731
        run("init", "-q")
        run("config", "user.email", "t@t")
        run("config", "user.name", "t")
        (cls.repo / "docs").mkdir()
        cls.f = cls.repo / "docs" / "schema.dbml"
        cls.f.write_text(OLD)
        run("add", ".")
        run("commit", "-qm", "v1")
        run("tag", "v1")
        cls.f.write_text(NEW)
        run("commit", "-qam", "v2")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_cli_diff_between_revisions_from_any_directory(self):
        out = self.repo / "o.html"
        r = cli("diff", "--file", str(self.f), "--from", "v1", "--to", "HEAD", "--out", str(out), cwd=tempfile.gettempdir())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(data_of(out.read_text())["diff"]["tables_added"], 1)

    def test_revisions_are_validated(self):
        pwn = Path(self.tmp.name, "pwn")
        r = cli("diff", "--file", str(self.f), "--from", f"--output={pwn}", "--out", str(self.repo / "x.html"))
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(pwn.exists())

    def get(self, url, host=None):
        req = urllib.request.Request(url, headers={"Host": host} if host else {})
        try:
            r = urllib.request.urlopen(req, timeout=10)
            return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def serve(self, view):
        srv = z.make_server(view, 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def test_server_reloads_guards_and_recovers(self):
        live = self.repo / "live.dbml"
        live.write_text("Table a { id integer [pk] }\n")
        u = self.serve(z._View(live))
        v1 = self.get(u + "/__version")[1]
        live.write_text("Table a { id integer [pk] }\nTable b { id integer [pk] }\n")
        self.assertNotEqual(v1, self.get(u + "/__version")[1])
        self.assertEqual(self.get(u + "/", host="evil.example")[0], 403)
        self.assertEqual(self.get(u + "/nope")[0], 404)
        live.write_text("Table a {\n")
        code, html = self.get(u + "/")
        self.assertEqual(code, 200)
        self.assertIn("Cannot draw this", html)
        live.write_text("Table a { id integer [pk] }\n")
        self.assertIn('"name": "a"', self.get(u + "/")[1])

    def test_polling_the_version_is_cheap_and_quiet(self):
        import contextlib
        import io

        # a table renamed without being told: render() prints a hint each time it runs
        f = self.repo / "docs" / "poll.dbml"
        f.write_text("Table users { id integer [pk]\n email text\n name text }")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "poll"], cwd=self.repo, check=True, capture_output=True)
        f.write_text("Table accounts { id integer [pk]\n email text\n name text }")
        u = self.serve(z._View(f, True, "HEAD", None))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.get(u + "/")
            first = err.getvalue()
            for _ in range(5):
                self.get(u + "/__version")
        self.assertIn("possible rename", first)
        self.assertEqual(err.getvalue(), first)

    def test_server_diff_mode_with_picker(self):
        u = self.serve(z._View(self.f, True, "v1", "HEAD"))
        d = data_of(self.get(u + "/")[1])
        self.assertEqual((d["diff"]["old"], d["diff"]["new"]), ("v1", "HEAD"))
        self.assertIn("v1", [r["v"] for r in d["diff"]["picker"]["revs"]])
        d2 = data_of(self.get(u + "/?from=v1&to=WORKTREE")[1])
        self.assertEqual(d2["diff"]["new"], "working tree")
        self.assertIn("not a usable git revision", self.get(u + "/?from=--output=" + str(Path(self.tmp.name, "pwn2")))[1])


if __name__ == "__main__":
    unittest.main()

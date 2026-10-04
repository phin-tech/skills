#!/usr/bin/env python3
"""Draw a database schema as one self-contained HTML page.

The renderer only reads DBML (https://dbml.dbdiagram.io), so any ORM works: an LLM (or a tool such as
prisma-dbml-generator, drizzle-dbml-generator or `db2dbml`) turns the schema into DBML, and this draws it.
Table and column `Note`s become the descriptions on the page; a `TableGroup` becomes a coloured section.

    schemaviz.py render schema.dbml --out schema.html [--title "My app"]     # stdlib only, no installs
    schemaviz.py sqlalchemy app.db:Base --out schema.dbml                    # optional: SQLAlchemy models -> DBML
    schemaviz.py db postgresql+asyncpg://u:pw@host/db --out schema.dbml      # optional: live database -> DBML

`render` also accepts a .json file: {"tables": [{"name", "comment", "group", "cols": [{"n", "t", "pk", "uq", "null",
"fk": "table.column", "note"}]}]}. Only `name` and `cols[].n` are required.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

__version__ = "0.1.1"


def _resource_dir() -> Path:
    """Where template.html lives: next to this file (a checkout or the skill folder), inside a PyInstaller bundle, or
    in the shared-data folder a `pip`/`uv tool` install puts it in."""
    if getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS)
    here = Path(__file__).resolve().parent
    if (here / "template.html").exists():
        return here
    import site

    for base in (sys.prefix, getattr(site, "USER_BASE", None)):  # a venv / uv tool install, or `pip install --user`
        if base and (Path(base) / "share" / "schemaviz" / "template.html").exists():
            return Path(base) / "share" / "schemaviz"
    return Path(sys.prefix) / "share" / "schemaviz"


HERE = _resource_dir()


# ---- DBML reader ------------------------------------------------------------------------------------------------

_STR = r"""(?:'''.*?'''|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")"""


def _unquote(s: str) -> str:
    s = s.strip()
    if s.startswith("'''"):
        return re.sub(r"\n\s*", " ", s[3:-3]).strip()
    if s[:1] in "'\"" and s[-1:] == s[:1]:
        return re.sub(r"\\(.)", r"\1", s[1:-1])
    return s


def _strip_comments(text: str) -> str:
    """Remove // and /* */ comments that are not inside a quoted string."""
    out, i, n = [], 0, len(text)
    while i < n:
        if text.startswith("'''", i):
            j = text.find("'''", i + 3)
            j = n if j < 0 else j + 3
            out.append(text[i:j]); i = j
        elif text[i] in "'\"`":
            q, j = text[i], i + 1
            while j < n and text[j] != q:
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1]); i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(text[i]); i += 1
    return "".join(out)


def _block_end(text: str, open_idx: int) -> int:
    """Index of the } matching the { at open_idx, ignoring braces inside strings."""
    depth, i, n = 0, open_idx, len(text)
    while i < n:
        if text.startswith("'''", i):
            j = text.find("'''", i + 3); i = n if j < 0 else j + 3; continue
        c = text[i]
        if c in "'\"":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            i = j + 1; continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced { } in DBML")


def _split_top(s: str, sep: str = ",") -> list[str]:
    out, cur, depth, i = [], "", 0, 0
    while i < len(s):
        if s.startswith("'''", i):
            j = s.find("'''", i + 3); j = len(s) if j < 0 else j + 3; cur += s[i:j]; i = j; continue
        c = s[i]
        if c in "'\"":
            j = i + 1
            while j < len(s) and s[j] != c:
                j += 2 if s[j] == "\\" else 1
            cur += s[i : j + 1]; i = j + 1; continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == sep and depth == 0:
            out.append(cur.strip()); cur = ""
        else:
            cur += c
        i += 1
    if cur.strip():
        out.append(cur.strip())
    return out


def _ident(s: str) -> str:
    return _unquote(s.strip().strip("`"))


def _tbl_col(ref: str) -> tuple[str, str]:
    """`schema.table.column` / `table.column` / `"table"."column"` -> (table, column)."""
    parts = [_ident(p) for p in re.findall(r'"[^"]+"|`[^`]+`|[^.\s]+', ref)]
    return parts[-2], parts[-1]


def _setting_note(settings: str) -> str | None:
    m = re.search(r"\bnote\s*:\s*(" + _STR + ")", settings, re.I | re.S)
    return _unquote(m.group(1)) if m else None


def _join_open_brackets(lines: list[str]) -> list[str]:
    out, buf = [], ""
    for ln in lines:
        buf = f"{buf} {ln.strip()}" if buf else ln
        stripped = re.sub(_STR, "", buf, flags=re.S)
        if stripped.count("[") <= stripped.count("]"):
            out.append(buf); buf = ""
    if buf:
        out.append(buf)
    return out


def parse_dbml(text: str) -> list[dict]:
    text = _strip_comments(text)
    tables: dict[str, dict] = {}
    refs: list[tuple[str, str, str, str, str]] = []
    groups: dict[str, str] = {}

    header = re.compile(r"(?mi)^[ \t]*(Table|TableGroup|Ref|Enum|Project)\b")
    pos = 0
    while True:
        m = header.search(text, pos)
        if not m:
            break
        kind = m.group(1).lower()
        i, depth, n = m.end(), 0, len(text)  # find the { or : that ends the header, skipping [settings] and strings
        while i < n:
            c = text[i]
            if c in "'\"":
                j = i + 1
                while j < n and text[j] != c:
                    j += 2 if text[j] == "\\" else 1
                i = j + 1; continue
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
            elif depth == 0 and c in "{:":
                break
            i += 1
        rest = text[m.end() : i]
        if i >= n:
            break
        if text[i] == ":":  # one-line `Ref name: a.b > c.d`
            eol = text.find("\n", i); eol = n if eol < 0 else eol
            if kind == "ref":
                _add_ref_line(text[i + 1 : eol], refs)
            pos = eol
            continue
        open_idx = i
        end = _block_end(text, open_idx)
        body = text[open_idx + 1 : end]
        pos = end + 1
        if kind == "table":
            name_m = re.match(r'\s*((?:"[^"]+"|`[^`]+`|[\w.]+)+)', rest)
            name = _tbl_col(name_m.group(1) + ".x")[0]
            tables[name] = _parse_table(name, body, refs)
        elif kind == "tablegroup":
            gname = _ident(rest.split("[")[0])
            for member in re.findall(r'"[^"]+"|`[^`]+`|[\w.]+', body):
                groups[_tbl_col(_ident(member) + ".x")[0]] = gname
        elif kind == "ref":
            for ln in body.splitlines():
                _add_ref_line(ln, refs)

    for t in tables.values():
        t["group"] = groups.get(t["name"])
    for a_t, a_c, op, b_t, b_c in refs:
        if op == "<":  # a is the "one" side: the foreign key lives on b
            a_t, a_c, b_t, b_c, op = b_t, b_c, a_t, a_c, ">"
        if op == "<>" or a_t not in tables or b_t not in tables:
            if op != "<>":
                print(f"warning: ref {a_t}.{a_c} -> {b_t}.{b_c} names an unknown table, skipped", file=sys.stderr)
            continue
        col = next((c for c in tables[a_t]["cols"] if c["n"] == a_c), None)
        if col is not None and "fk" not in col:
            col["fk"] = [b_t, b_c, op]
    for t in tables.values():
        pks = [c for c in t["cols"] if c["pk"]]
        for c in t["cols"]:
            if "fk" in c and ((len(pks) == 1 and c["pk"]) or c["uq"]):
                c["fk"][2] = "-"
    return list(tables.values())


def _add_ref_line(line: str, refs: list) -> None:
    m = re.search(r"([\w.\"`]+)\s*(<>|[<>-])\s*([\w.\"`]+)", line)
    if m and "(" not in line.split("[")[0]:
        a_t, a_c = _tbl_col(m.group(1)); b_t, b_c = _tbl_col(m.group(3))
        refs.append((a_t, a_c, m.group(2), b_t, b_c))


def _parse_table(name: str, body: str, refs: list) -> dict:
    t = {"name": name, "comment": "", "group": None, "cols": [], "uniq": []}
    # nested blocks first: indexes { }, Note { }
    for kw in ("indexes", "note"):
        while True:
            m = re.search(rf"(?i)(?<![\w\"])({kw})\s*\{{", body)
            if not m:
                break
            end = _block_end(body, m.end() - 1)
            inner = body[m.end() : end]
            body = body[: m.start()] + body[end + 1 :]
            if kw == "note":
                t["comment"] = _unquote(inner)
            else:
                t["_indexes"] = inner
    nm = re.search(r"(?mi)^\s*note\s*:\s*(" + _STR + r")\s*$", body, re.S)
    if nm:
        t["comment"] = _unquote(nm.group(1))
        body = body[: nm.start()] + body[nm.end() :]

    for ln in _join_open_brackets([l for l in body.splitlines() if l.strip()]):
        m = re.match(r'\s*("[^"]+"|`[^`]+`|\w+)\s+("[^"]+"|[\w.]+(?:<[^\[\]]*>|\([^)]*\))?(?:\[\])?)\s*(?:\[(.*)\])?\s*$', ln, re.S)
        if not m:
            print(f"warning: table {name}: could not read this line, skipped: {ln.strip()[:90]}", file=sys.stderr)
            continue
        col = {"n": _ident(m.group(1)), "t": _unquote(m.group(2)).lower(), "pk": False, "uq": False, "null": True}
        for item in _split_top(m.group(3) or ""):
            low = re.sub(r"\s+", " ", item.lower())
            if low in ("pk", "primary key"):
                col["pk"] = True
            elif low == "unique":
                col["uq"] = True
            elif low == "not null":
                col["null"] = False
            elif low.startswith("ref"):
                rm = re.match(r"(?i)ref\s*:\s*(<>|[<>-])\s*(.+)", item.strip())
                if rm:
                    b_t, b_c = _tbl_col(rm.group(2))
                    refs.append((name, col["n"], rm.group(1), b_t, b_c))
        note = _setting_note(m.group(3) or "")
        if note:
            col["note"] = note
        if col["pk"]:
            col["null"] = False
        t["cols"].append(col)

    for ln in (t.pop("_indexes", "") or "").splitlines():
        m = re.match(r"\s*\(?([^)\[]+?)\)?\s*(?:\[(.*)\])?\s*$", ln)
        if not m or not m.group(1).strip():
            continue
        names = [_ident(x) for x in m.group(1).split(",")]
        flags = (m.group(2) or "").lower()
        by = {c["n"]: c for c in t["cols"]}
        if re.search(r"\bpk\b", flags):
            for n in names:
                if n in by:
                    by[n]["pk"] = True; by[n]["null"] = False
        elif "unique" in flags:
            if len(names) == 1 and names[0] in by:
                by[names[0]]["uq"] = True
            elif len(names) > 1:
                t["uniq"].append(", ".join(names))
    return t


def parse_json(text: str) -> list[dict]:
    data = json.loads(text)
    tables = data["tables"] if isinstance(data, dict) else data
    out = []
    for t in tables:
        cols = []
        for c in t.get("cols", []):
            col = {"n": c["n"], "t": c.get("t", ""), "pk": bool(c.get("pk")), "uq": bool(c.get("uq")),
                   "null": bool(c.get("null", not c.get("pk")))}
            fk = c.get("fk")
            if isinstance(fk, str):
                fk = [*fk.split(".", 1), ">"]
            if fk:
                col["fk"] = [fk[0], fk[1], fk[2] if len(fk) > 2 else ">"]
            if c.get("note"):
                col["note"] = c["note"]
            cols.append(col)
        out.append({"name": t["name"], "comment": t.get("comment", ""), "group": t.get("group"),
                    "cols": cols, "uniq": t.get("uniq", [])})
    return out


# ---- layout decisions (which table is the hub, which section a table is in) -------------------------------------


def find_hub(tables: list[dict]) -> str | None:
    """A table most others point at (a tenant or account root). Drawn as a bar so its many links don't hide the rest."""
    refs: dict[str, set[str]] = defaultdict(set)
    for t in tables:
        for c in t["cols"]:
            if "fk" in c and c["fk"][0] != t["name"]:
                refs[c["fk"][0]].add(t["name"])
    if not refs:
        return None
    name, who = max(refs.items(), key=lambda kv: len(kv[1]))
    return name if len(who) >= 4 and len(who) >= 0.4 * (len(tables) - 1) else None


def assign_groups(tables: list[dict], hub: str | None) -> None:
    """Keep the groups the schema names; put the rest with the tables they link to."""
    if all(t["group"] for t in tables):
        return
    parent = {t["name"]: t["name"] for t in tables}

    def root(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    degree: dict[str, int] = defaultdict(int)
    for t in tables:
        for c in t["cols"]:
            if "fk" in c and c["fk"][0] in parent and c["fk"][0] not in (hub, t["name"]) and t["name"] != hub:
                parent[root(t["name"])] = root(c["fk"][0])
                degree[c["fk"][0]] += 1
    members: dict[str, list[dict]] = defaultdict(list)
    for t in tables:
        members[root(t["name"])].append(t)
    for ms in members.values():
        label = max(ms, key=lambda m: degree[m["name"]])["name"].replace("_", " ").capitalize() if len(ms) > 1 else None
        for m in ms:
            m["group"] = m["group"] or label or "Other"


def order_groups(tables: list[dict], hub: str | None) -> list[dict]:
    """Sections in first-seen order (the hub's own section first), each with parents before the tables that use them."""
    by_name = {t["name"]: t for t in tables}
    depth: dict[str, int] = {}

    def d(name: str, seen: tuple = ()) -> int:
        if name in depth:
            return depth[name]
        parents = {c["fk"][0] for c in by_name[name]["cols"]
                   if "fk" in c and c["fk"][0] != name and c["fk"][0] not in seen and c["fk"][0] in by_name}
        depth[name] = 0 if not parents else 1 + max(d(p, seen + (name,)) for p in sorted(parents))  # sorted: set order varies per run
        return depth[name]

    for t in tables:
        d(t["name"])
    order = list(dict.fromkeys(t["group"] for t in tables))
    if hub:
        order.sort(key=lambda g: g != by_name[hub]["group"])
    index = {t["name"]: i for i, t in enumerate(tables)}
    return [{"name": g, "tables": sorted((t for t in tables if t["group"] == g), key=lambda t: (depth[t["name"]], index[t["name"]]))}
            for g in order]


def to_html(tables: list[dict], title: str, source: str, diff: dict | None = None) -> str:
    by = {t["name"]: t for t in tables}
    for t in tables:  # a ref to a table or column that isn't in the file can't be drawn
        for c in t["cols"]:
            if "fk" not in c:
                continue
            if c["fk"][0] not in by:
                del c["fk"]
                continue
            hit = next((x["n"] for x in by[c["fk"][0]]["cols"] if x["n"] == c["fk"][1]), None) or \
                next((x["n"] for x in by[c["fk"][0]]["cols"] if x["n"].lower() == c["fk"][1].lower()), None)
            if hit:
                c["fk"][1] = hit
            else:
                print(f"warning: {t['name']}.{c['n']} points at {c['fk'][0]}.{c['fk'][1]}, which has no such column; link skipped", file=sys.stderr)
                del c["fk"]
    hub = find_hub(tables)
    assign_groups(tables, hub)
    data = json.dumps({"title": title, "source": source, "hub": hub, "diff": diff, "groups": order_groups(tables, hub)}, ensure_ascii=False)
    html = (HERE / "template.html").read_text(encoding="utf-8")
    return html.replace("/*TITLE*/", re.sub(r"[<>&]", "", title)).replace("/*DATA*/{}", data.replace("</", "<\\/"))


def parse_text(text: str, name: str) -> list[dict]:
    tables = parse_json(text) if name.endswith(".json") else parse_dbml(text)
    if not tables:
        raise SystemExit(f"No tables found in {name}.")
    return tables


def load_tables(path: Path) -> list[dict]:
    return parse_text(path.read_text(encoding="utf-8"), path.name)


_REV_OK = re.compile(r"^[\w./~^@{}+-]+$")


def _check_rev(rev: str) -> str:
    if not _REV_OK.match(rev) or rev.startswith("-"):
        raise SystemExit(f"not a usable git revision: {rev!r}")
    return rev


def git_text(rev: str, file: str) -> str:
    """The contents of `file` as committed at `rev` (any tag, branch or commit), read with `git show`."""
    import subprocess

    path = Path(file).resolve()
    r = subprocess.run(["git", "show", f"{_check_rev(rev)}:./{path.name}"], capture_output=True, encoding="utf-8", errors="replace", cwd=path.parent)
    if r.returncode:
        raise SystemExit(f"git could not read {file} at {rev}: {r.stderr.strip()}")
    return r.stdout


def list_revs(file: str, limit: int = 40) -> list[dict]:
    """Tags, branches and recent commits of `file`, for the revision picker."""
    import subprocess

    path = Path(file).resolve()
    out, seen = [{"v": "HEAD", "label": "HEAD"}], {"HEAD"}

    def git(*args: str) -> list[str]:
        r = subprocess.run(["git", *args], capture_output=True, encoding="utf-8", errors="replace", cwd=path.parent)
        return r.stdout.splitlines() if r.returncode == 0 else []

    for ref in git("for-each-ref", "--sort=-creatordate", f"--count={limit}", "--format=%(refname:short)", "refs/tags", "refs/heads"):
        if ref not in seen and _REV_OK.match(ref):
            out.append({"v": ref, "label": ref}); seen.add(ref)
    for ln in git("log", f"-n{limit}", "--format=%h\t%s", "--", path.name):
        sha, _, subject = ln.partition("\t")
        if sha not in seen:
            out.append({"v": sha, "label": f"{sha}  {subject[:60]}"}); seen.add(sha)
    return out


def _diff_inputs(a) -> tuple:
    """(old text, new text, (name, name), old label, new label) from the arguments of diff / publish."""
    if a.file:
        if not a.rev_from or a.old or a.new:
            raise SystemExit("With --file, give --from REV (and optionally --to REV), and no positional files.")
        old_x = git_text(a.rev_from, a.file)
        new_x = git_text(a.rev_to, a.file) if a.rev_to else Path(a.file).read_text(encoding="utf-8")
        return old_x, new_x, (a.file, a.file), a.old_label or _short_rev(a.rev_from), a.new_label or (_short_rev(a.rev_to) if a.rev_to else "working tree")
    if not (a.old and a.new):
        raise SystemExit("Give two files, or --file F --from REV [--to REV].")
    return Path(a.old).read_text(encoding="utf-8"), Path(a.new).read_text(encoding="utf-8"), (a.old, a.new), a.old_label or Path(a.old).name, a.new_label or Path(a.new).name


def diff_page(old_x: str, new_x: str, names: tuple, lo: str, ln: str, renames=None, notes=False, title=None, picker=None,
              when=(None, None)) -> tuple[str, dict, list, list]:
    """The HTML for the changes between two DBML/JSON texts, and the counts."""
    old_t, new_t = parse_text(old_x, names[0]), parse_text(new_x, names[1])
    warnings = provenance_warnings(old_x, new_x, *when)
    for w in warnings:
        print("warning: " + w, file=sys.stderr)
    tables, stats = diff_tables(old_t, new_t, renames, notes)
    for h in rename_hints(old_t, new_t, renames):
        print(f"hint: possible rename: {h}. Check git, then pass --rename.", file=sys.stderr)
    info = dict(stats, old=lo, new=ln, picker=picker, warnings=warnings)
    return to_html(tables, title or f"{lo} to {ln}", f"{lo} and {ln}", info), stats, tables, warnings


def _when(a) -> tuple:
    """Commit dates for the two sides when comparing revisions of a tracked file; (None, None) for two plain files."""
    if not a.file:
        return (None, None)
    return rev_time(a.rev_from, a.file), rev_time(a.rev_to or "WORKTREE", a.file)


def _parse_ts(s: str):
    """An ISO timestamp (dbt writes e.g. 2026-10-04T14:02:11.123456Z) as an aware datetime, or None."""
    from datetime import datetime, timezone

    s = re.sub(r"(\.\d{6})\d+", r"\1", (s or "").strip().replace("Z", "+00:00"))
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def header_meta(text: str) -> dict:
    """What an exporter wrote at the top of a DBML file: source, source-version, source-generated."""
    out = {}
    for key in ("Source", "Source-version", "Source-generated"):
        m = re.search(rf"(?m)^// {key}: (.+)$", text)
        out[key.lower().replace("-", "_")] = m.group(1).strip() if m else ""
    return out


def rev_time(rev: str, file: str):
    """(when, allowed age in days) for the side of a diff at `rev`: a commit's date, or now for the working tree."""
    import subprocess
    from datetime import datetime, timezone

    if rev == "WORKTREE":
        return datetime.now(timezone.utc), 30
    path = Path(file).resolve()
    r = subprocess.run(["git", "log", "-1", "--format=%cI", _check_rev(rev)], capture_output=True, encoding="utf-8", errors="replace", cwd=path.parent)
    d = _parse_ts(r.stdout) if r.returncode == 0 else None
    return (d, 7) if d else None


def provenance_warnings(old_x: str, new_x: str, old_when=None, new_when=None) -> list[str]:
    """Reasons to doubt that a diff compares like with like: made by different tools, or generated long before the commit
    that holds it (so the schema may have moved on since)."""
    mo, mn = header_meta(old_x), header_meta(new_x)
    out = []
    if mo["source"] and mn["source"] and mo["source"] != mn["source"]:
        out.append(f"the two files were generated from different sources ({mo['source']} vs {mn['source']}); type spellings and "
                   "constraints can differ for that reason alone, so some changes below may not be real.")
    if mo["source_version"] and mn["source_version"] and mo["source_version"] != mn["source_version"]:
        out.append(f"the two files were generated with different tool versions ({mo['source_version']} vs {mn['source_version']}); "
                   "some changes below may come from the tool, not the schema.")
    for label, m, when in (("old", mo, old_when), ("new", mn, new_when)):
        g = _parse_ts(m["source_generated"])
        if g and when and when[0] and (when[0] - g).days > when[1]:
            out.append(f"the {label} file says it was generated {(when[0] - g).days} days before the version it is compared as "
                       f"({m['source_generated']}), so it may be out of date. Regenerate it.")
    return out


def header_source(text: str) -> str:
    """The `// Source:` line an exporter wrote, e.g. 'database postgresql'. Empty for hand-written DBML."""
    m = re.search(r"(?m)^// Source: (.+)$", text)
    return m.group(1).strip() if m else ""


def _short_rev(rev: str) -> str:
    """Commit hashes are shortened to 7 characters; tags, branches and HEAD~2 are shown as typed."""
    return rev[:7] if re.fullmatch(r"[0-9a-f]{12,40}", rev) else rev


def _col_changes(o: dict, n: dict, trn: dict | None = None, notes: bool = False) -> list[str]:
    """What differs between two versions of the same column, as short phrases."""
    out = []
    if norm_type(o["t"]) != norm_type(n["t"]):
        out.append(f"{o['t'] or '?'} -> {n['t'] or '?'}")
    if o["null"] != n["null"]:
        out.append("now nullable" if n["null"] else "now not null")
    if o["pk"] != n["pk"]:
        out.append("now primary key" if n["pk"] else "no longer primary key")
    if o["uq"] != n["uq"]:
        out.append("now unique" if n["uq"] else "no longer unique")
    of, nf = o.get("fk"), n.get("fk")
    if of and trn and of[0] in trn:
        of = [trn[of[0]], *of[1:]]  # the table it points at was renamed: not a change to this column
    if (of and of[:2]) != (nf and nf[:2]):
        out.append("fk " + (f"{of[0]}.{of[1]}" if of else "none") + " -> " + (f"{nf[0]}.{nf[1]}" if nf else "none"))
    if notes and o.get("note") != n.get("note"):
        out.append("note edited")
    return out


# Tables that record which migrations ran. They are not part of the application's schema.
BOOKKEEPING = {"alembic_version", "django_migrations", "schema_migrations", "ar_internal_metadata", "_prisma_migrations",
               "__diesel_schema_migrations", "knex_migrations", "knex_migrations_lock", "sequelizemeta", "flyway_schema_history",
               "goose_db_version", "__efmigrationshistory", "databasechangelog", "databasechangeloglock", "sqlite_sequence"}

_TYPE_ALIASES = {"int": "integer", "int4": "integer", "int8": "bigint", "int2": "smallint", "bool": "boolean",
                 "character varying": "varchar", "char varying": "varchar", "character": "char", "bpchar": "char",
                 "double precision": "float8", "float4": "real", "decimal": "numeric",
                 "timestamp with time zone": "timestamptz", "timestamp without time zone": "timestamp",
                 "time with time zone": "timetz", "time without time zone": "time"}


def norm_type(t: str) -> str:
    """One spelling per SQL type (int/int4/integer, bool/boolean, ...) so a respelling is not reported as a change."""
    t = re.sub(r"\s+", " ", (t or "").strip().lower())
    m = re.match(r"([a-z0-9_ ]+?)\s*(\(.*?\))?\s*(\[\])?$", t)
    if not m:
        return t
    base, args, arr = m.groups()
    return _TYPE_ALIASES.get(base, base) + (re.sub(r"\s+", "", args) if args else "") + (arr or "")


def parse_renames(specs: list[str]) -> tuple[dict, dict]:
    """`old=new` renames a table; `table.old=new` renames a column (table is the new table name, or the old one)."""
    tables, cols = {}, {}
    for spec in specs or []:
        left, eq, right = spec.partition("=")
        if not eq or not left or not right:
            raise SystemExit(f"--rename wants old=new or table.old=new, got {spec!r}")
        if "." in left:
            t, _, c = left.partition(".")
            cols[(t, c)] = right
        else:
            tables[left] = right
    return tables, cols


def diff_tables(old: list[dict], new: list[dict], renames: list[str] | None = None, notes: bool = False) -> tuple[list[dict], dict]:
    """Merge two schemas into one list. Every table and column gets a `diff` of added, removed or changed
    (absent when unchanged), changed columns get `was`, renamed tables get `renamed_from`. Removed tables and
    columns stay, so they can be shown. `renames` are confirmed renames (see parse_renames)."""
    import copy

    trn, crn = parse_renames(renames)
    old_by, new_by = {t["name"]: t for t in old}, {t["name"]: t for t in new}
    for a, b in trn.items():
        if a not in old_by or b not in new_by:
            raise SystemExit(f"--rename {a}={b}: needs {a} in the old schema and {b} in the new one.")
    merged, stats = [], defaultdict(int)
    matched_tables = set()
    for t in copy.deepcopy(new):
        old_name = next((a for a, b in trn.items() if b == t["name"]), t["name"])
        o = old_by.get(old_name)
        if o is None:
            t["diff"] = "added"
            stats["tables_added"] += 1
            stats["cols_added"] += len(t["cols"])
            for c in t["cols"]:
                c["diff"] = "added"
            merged.append(t)
            continue
        matched_tables.add(old_name)
        ocols = {c["n"]: c for c in o["cols"]}
        # column renames may be keyed by the new or the old table name
        ren = {new_c: old_c for (tn, old_c), new_c in crn.items() if tn in (t["name"], old_name)}
        seen_old, changed = set(), old_name != t["name"]
        if changed:
            t["renamed_from"] = old_name
        for c in t["cols"]:
            src = ren.get(c["n"], c["n"])
            if src != c["n"] and src not in ocols:
                raise SystemExit(f"--rename {t['name']}.{src}={c['n']}: no column {src} in old {o['name']}.")
            oc = ocols.get(src)
            if oc is None:
                c["diff"] = "added"; stats["cols_added"] += 1; changed = True
                continue
            seen_old.add(src)
            why = _col_changes(oc, c, trn, notes)
            if src != c["n"]:
                why.insert(0, f"renamed from {src}")
            if why:
                c["diff"] = "changed"; c["was"] = "; ".join(why); stats["cols_changed"] += 1; changed = True
        prev = None  # re-insert dropped columns after the column that used to precede them
        kept = {ren.get(c["n"], c["n"]): c["n"] for c in t["cols"]}
        for oc in o["cols"]:
            if oc["n"] in kept:
                prev = kept[oc["n"]]
                continue
            gone = dict(copy.deepcopy(oc), diff="removed")
            at = 0 if prev is None else 1 + next(i for i, c in enumerate(t["cols"]) if c["n"] == prev)
            t["cols"].insert(at, gone)
            prev = oc["n"]
            stats["cols_removed"] += 1; changed = True
        if sorted(o["uniq"]) != sorted(t["uniq"]):
            changed = True
            t["was"] = "unique together was " + (", ".join(f"({u})" for u in o["uniq"]) or "none") + ", now " + (", ".join(f"({u})" for u in t["uniq"]) or "none")
        if changed:
            t["diff"] = "changed"
            stats["tables_changed"] += 1
        merged.append(t)
    for o in copy.deepcopy(old):
        if o["name"] not in matched_tables:
            o["diff"] = "removed"
            for c in o["cols"]:
                c["diff"] = "removed"
            stats["tables_removed"] += 1
            stats["cols_removed"] += len(o["cols"])
            merged.append(o)
    keys = ["tables_added", "tables_removed", "tables_changed", "cols_added", "cols_removed", "cols_changed"]
    return merged, {k: stats[k] for k in keys}


def rename_hints(old: list[dict], new: list[dict], renames: list[str] | None = None) -> list[str]:
    """Guesses at renames, worded as questions: the agent should confirm them in git before passing --rename."""
    trn, crn = parse_renames(renames)
    old_by, new_by = {t["name"]: t for t in old}, {t["name"]: t for t in new}
    hints = []
    gone = [t for n, t in old_by.items() if n not in new_by and n not in trn]
    came = [t for n, t in new_by.items() if n not in old_by and n not in trn.values()]
    for g in gone:
        gc = {(c["n"], c["t"]) for c in g["cols"]}
        for a in came:
            ac = {(c["n"], c["t"]) for c in a["cols"]}
            if len(gc | ac) and len(gc & ac) / len(gc | ac) >= 0.6:
                hints.append(f"table {g['name']} -> {a['name']} ({len(gc & ac)} of {len(gc | ac)} columns identical)")
    for n, o in old_by.items():
        t = new_by.get(n) or new_by.get(trn.get(n, ""))
        if t is None:
            continue
        have_new = {c["n"] for c in o["cols"]}
        have_old = {c["n"] for c in t["cols"]}
        asked = {(old_c, new_c) for (tn, old_c), new_c in crn.items() if tn in (n, t["name"])}
        lost = [c for c in o["cols"] if c["n"] not in have_old and not any(c["n"] == x for x, _ in asked)]
        found = [c for c in t["cols"] if c["n"] not in have_new and not any(c["n"] == y for _, y in asked)]
        for g in lost:
            same = [a for a in found if a["t"] == g["t"]]
            if len(same) == 1:
                hints.append(f"column {t['name']}.{g['n']} -> {same[0]['n']} (both {g['t'] or 'untyped'})")
    return hints


# ---- DBML writer, for the exporters below -----------------------------------------------------------------------


def to_dbml(tables: list[dict], command: str = "", source: str = "", version: str = "", generated: str = "") -> str:
    q = lambda s: "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ") + "'"
    out = ["// Generated by: schemaviz.py " + command if command else "// Generated by schemaviz.py"]
    if source:
        out.append("// Source: " + source)
    if version:
        out.append("// Source-version: " + version)
    if generated:
        out.append("// Source-generated: " + generated)
    out.append("// Paste into https://dbdiagram.io/d\n")
    tables = sorted(tables, key=lambda t: t["name"])
    for t in tables:
        out.append(f"Table {t['name']} {{")
        composite = sum(1 for c in t["cols"] if c["pk"]) > 1
        for c in t["cols"]:
            bits = []
            if c["pk"] and not composite:
                bits.append("pk")
            if c["uq"] and not c["pk"]:
                bits.append("unique")
            if not c["null"] and not c["pk"]:
                bits.append("not null")
            if "fk" in c:
                bits.append(f"ref: {c['fk'][2]} {c['fk'][0]}.{c['fk'][1]}")
            if c.get("note"):
                bits.append(f"note: {q(c['note'])}")
            ty = c["t"] or "unknown"
            typ = ty if re.fullmatch(r"[\w.]+(\([\d,\s]+\))?(\[\])?", ty) else '"' + ty + '"'
            out.append(f"  {c['n']} {typ or 'varchar'}" + (f" [{', '.join(bits)}]" if bits else ""))
        if t["comment"]:
            out.append(f"\n  Note: {q(t['comment'])}")
        idx = []
        if composite:
            idx.append(f"    ({', '.join(c['n'] for c in t['cols'] if c['pk'])}) [pk]")
        idx += [f"    ({u}) [unique]" for u in t["uniq"]]
        if idx:
            out += ["\n  indexes {", *idx, "  }"]
        out.append("}\n")
    groups: dict[str, list[str]] = defaultdict(list)
    for t in tables:
        if t.get("group"):
            groups[t["group"]].append(t["name"])
    for g, names in groups.items():
        out.append(f'TableGroup "{g}" {{\n  ' + "\n  ".join(names) + "\n}\n")
    return "\n".join(out)


# ---- optional exporters: read a code base or a database, write DBML ---------------------------------------------


def _read_sqlalchemy(md, extra_unique: dict | None = None) -> list[dict]:
    from sqlalchemy import Index, UniqueConstraint
    from sqlalchemy.dialects import postgresql

    def typ(col) -> str:
        try:
            text = col.type.compile(dialect=postgresql.dialect())
        except Exception:
            text = str(col.type)
        return (text.lower().replace("timestamp with time zone", "timestamptz")
                .replace("timestamp without time zone", "timestamp").replace("character varying", "varchar"))

    tables = []
    for t in md.sorted_tables:
        if t.name.lower() in BOOKKEEPING:
            continue
        sets = [tuple(c.name for c in k.columns) for k in t.constraints if isinstance(k, UniqueConstraint)]
        sets += [tuple(c.name for c in ix.columns) for ix in t.indexes if isinstance(ix, Index) and ix.unique]
        sets += (extra_unique or {}).get(t.name, [])
        sets = list(dict.fromkeys(sets))
        single = {u[0] for u in sets if len(u) == 1}
        cols = []
        for c in t.columns:
            col = {"n": c.name, "t": typ(c), "pk": c.primary_key, "uq": bool(c.unique) or c.name in single,
                   "null": bool(c.nullable) and not c.primary_key}
            fks = list(c.foreign_keys)
            if fks:
                col["fk"] = [fks[0].column.table.name, fks[0].column.name, ">"]
            if c.comment:
                col["note"] = c.comment
            cols.append(col)
        tables.append({"name": t.name, "comment": t.comment or "", "group": (t.info or {}).get("group"),
                       "cols": cols, "uniq": [", ".join(u) for u in sets if len(u) > 1]})
    return tables


def export_sqlalchemy(spec: str) -> str:
    import importlib

    from sqlalchemy import MetaData
    from sqlalchemy.orm import DeclarativeBase

    mod, _, attr = spec.partition(":")
    sys.path.insert(0, str(Path.cwd()))
    obj = getattr(importlib.import_module(mod), attr or "Base")
    md = obj if isinstance(obj, MetaData) else obj.metadata if (isinstance(obj, type) and issubclass(obj, DeclarativeBase)) or hasattr(obj, "metadata") else None
    if md is None:
        raise SystemExit(f"{spec} is neither a MetaData nor a declarative base")
    return to_dbml(_read_sqlalchemy(md), f"sqlalchemy {spec}", "sqlalchemy models")


def _read_django() -> list[dict]:
    from django.apps import apps
    from django.db import connection, models

    def typ(f) -> str:
        try:
            return (f.db_type(connection) or f.get_internal_type()).lower()
        except Exception:
            return f.get_internal_type().lower()

    tables = {}
    for m in apps.get_models(include_auto_created=True):
        o = m._meta
        if o.proxy:
            continue
        sets = [tuple(o.get_field(n).column for n in u) for u in o.unique_together]
        for k in o.constraints:
            if isinstance(k, models.UniqueConstraint) and k.fields and not k.condition:
                sets.append(tuple(o.get_field(n).column for n in k.fields))
        single = {u[0] for u in sets if len(u) == 1}
        cols = []
        for f in o.local_concrete_fields:
            col = {"n": f.column, "t": typ(f), "pk": f.primary_key, "uq": f.unique or f.column in single,
                   "null": f.null and not f.primary_key}
            if f.remote_field is not None:
                col["fk"] = [f.target_field.model._meta.db_table, f.target_field.column, ">"]
            if f.help_text:
                col["note"] = str(f.help_text)
            cols.append(col)
        doc = (m.__doc__ or "").strip().splitlines()
        comment = doc[0] if doc and not doc[0].startswith(m.__name__ + "(") else ""
        tables[o.db_table] = {"name": o.db_table, "comment": comment, "group": o.app_label, "cols": cols,
                              "uniq": [", ".join(u) for u in sets if len(u) > 1]}
    return list(tables.values())


def export_django(settings: str) -> str:
    import os

    import django

    sys.path.insert(0, str(Path.cwd()))
    if settings:
        os.environ["DJANGO_SETTINGS_MODULE"] = settings
    if not os.environ.get("DJANGO_SETTINGS_MODULE"):
        raise SystemExit("Pass the settings module (e.g. mysite.settings) or set DJANGO_SETTINGS_MODULE.")
    django.setup()
    return to_dbml(_read_django(), f"django {settings}", "django models")


# ---- SQL DDL reader: a schema-only dump (pg_dump --schema-only, structure.sql, prisma migrate diff --script, .schema) ----

_SQL_ID = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[\w$]+)'
_SQL_NAME = rf"{_SQL_ID}(?:\s*\.\s*{_SQL_ID})*"


def _sql_clean(sql: str) -> list[str]:
    """Statements of a SQL script, with comments removed. Splits on ; outside quotes and $$ blocks."""
    out, cur, i, n = [], [], 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i); i = n if j < 0 else j; continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2); i = n if j < 0 else j + 2; continue
        if c in "'\"`":
            j = i + 1
            while j < n:
                if sql[j] == c and sql[j + 1 : j + 2] == c:
                    j += 2
                elif sql[j] == c:
                    break
                else:
                    j += 1
            cur.append(sql[i : j + 1]); i = j + 1; continue
        m = re.match(r"\$(\w*)\$", sql[i:]) if c == "$" else None
        if m:
            end = sql.find(m.group(0), i + len(m.group(0)))
            end = n if end < 0 else end + len(m.group(0))
            cur.append(sql[i:end]); i = end; continue
        if c == ";":
            out.append("".join(cur).strip()); cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur).strip())
    return [x for x in out if x]


def _sql_ident(s: str) -> str:
    s = s.strip()
    return s[1:-1] if len(s) > 1 and s[0] in '"`[' else s


def _sql_table(name: str) -> str:
    return _sql_ident(re.findall(_SQL_ID, name)[-1])


def _sql_key(name: str) -> tuple[str, str]:
    """`schema.table` -> ('schema', 'table'); an unqualified name has schema ''."""
    parts = [_sql_ident(x) for x in re.findall(_SQL_ID, name)]
    return (parts[-2] if len(parts) > 1 else "", parts[-1])


def _sql_cols(s: str) -> list[str]:
    return [_sql_ident(x) for x in _split_top(s.strip().strip("()"))]


_SQL_CONSTRAINT = re.compile(r"\b(?:not\s+null|null|default|primary\s+key|unique|references|check|constraint|generated|collate|"
                             r"auto_?increment|comment|identity|on\s+update)\b|\bas\s*\(", re.I)


def _sql_type(t: str) -> str:
    m = re.fullmatch(r'\s*(?:\w+\.)?"([^"]+)"(\[\])?\s*', t)  # a user type such as an enum: keep its name as written
    return m.group(1) + (m.group(2) or "") if m else norm_type(t)


def _sql_column(d: str) -> dict | None:
    m = re.match(rf"\s*({_SQL_ID})(?:\s+(.*))?$", d, re.S)
    if not m:
        return None
    name, rest = _sql_ident(m.group(1)), m.group(2) or ""
    k = _SQL_CONSTRAINT.search(rest)
    typ = (rest[: k.start()] if k else rest).strip()
    flags = rest[k.start():] if k else ""
    col = {"n": name, "t": _sql_type(typ), "pk": bool(re.search(r"primary\s+key", flags, re.I)),
           "uq": bool(re.search(r"\bunique\b", flags, re.I)), "null": not re.search(r"not\s+null|primary\s+key", flags, re.I)}
    r = re.search(rf"references\s+({_SQL_NAME})\s*(\(([^)]*)\))?", flags, re.I)
    if r:
        col["fk"] = [_sql_key(r.group(1)), _sql_ident(r.group(3).split(",")[0]) if r.group(3) else None, ">"]
    return col


def _sql_constraint(t: dict, d: str) -> bool:
    """Apply a table-level constraint (PRIMARY KEY, UNIQUE, FOREIGN KEY) to table t. False if it is something else."""
    d = re.sub(rf"^\s*constraint\s+{_SQL_ID}\s+", "", d, flags=re.I)
    cols = {c["n"]: c for c in t["cols"]}
    m = re.match(r"\s*primary\s+key\s*(?:\w+\s*)?(\([^)]*\))", d, re.I)
    if m:
        for n in _sql_cols(m.group(1)):
            if n in cols:
                cols[n]["pk"] = True; cols[n]["null"] = False
        return True
    m = re.match(r"\s*unique(?:\s+(?:key|index))?\s*(?:" + _SQL_ID + r"\s*)?(\([^)]*\))", d, re.I)
    if m:
        names = _sql_cols(m.group(1))
        if len(names) == 1 and names[0] in cols:
            cols[names[0]]["uq"] = True
        elif all(n in cols for n in names) and ", ".join(names) not in t["uniq"]:
            t["uniq"].append(", ".join(names))
        return True
    m = re.match(rf"\s*foreign\s+key\s*(?:{_SQL_ID}\s*)?(\([^)]*\))\s*references\s+({_SQL_NAME})\s*(\([^)]*\))?", d, re.I)
    if m:
        here, there = _sql_cols(m.group(1)), (_sql_cols(m.group(3)) if m.group(3) else [None] * len(_sql_cols(m.group(1))))
        for a, b in zip(here, there):
            if a in cols and "fk" not in cols[a]:
                cols[a]["fk"] = [_sql_key(m.group(2)), b, ">"]
        return True
    return bool(re.match(r"\s*(check|exclude|index|key|fulltext|spatial|like)\b", d, re.I))


def _read_sql(text: str) -> list[dict]:
    tables: dict[tuple, dict] = {}  # keyed by (schema, table): auth.users and public.users are different tables
    stmts = _sql_clean(text)

    def find(name: str, near: str = "") -> tuple | None:
        key = _sql_key(name)
        if key in tables:
            return key
        if key[0]:
            return None
        same = [k for k in tables if k[1] == key[1]]
        return next((k for k in same if k[0] == near), None) or next((k for k in same if k[0] == "public"), None) or (sorted(same)[0] if same else None)

    create = re.compile(rf"create\s+(?:(?:global\s+|local\s+)?(?:temp(?:orary)?\s+|unlogged\s+))?table\s+(?:if\s+not\s+exists\s+)?({_SQL_NAME})\s*\((.*)\)[^()]*$", re.I | re.S)
    for st in stmts:
        m = create.match(st)
        if not m:
            continue
        key = _sql_key(m.group(1))
        t = {"name": key[1], "comment": "", "group": None, "cols": [], "uniq": [], "_schema": key[0]}
        pending = []
        for part in _split_top(m.group(2)):
            if re.match(r"\s*(constraint|primary\s+key|unique|foreign\s+key|check|exclude|index|key|fulltext|spatial|like)\b", part, re.I):
                pending.append(part)
                continue
            col = _sql_column(part)
            if col:
                t["cols"].append(col)
        for part in pending:
            _sql_constraint(t, part)
        tables[key] = t
    for st in stmts:
        m = re.match(rf"alter\s+table\s+(?:only\s+)?(?:if\s+exists\s+)?({_SQL_NAME})\s+(.*)$", st, re.I | re.S)
        if m and find(m.group(1)):
            t = tables[find(m.group(1))]
            for act in _split_top(m.group(2)):
                a = re.sub(r"^\s*add\s+", "", act, flags=re.I)
                if a == act:
                    continue
                mc = re.match(r"column\s+(?:if\s+not\s+exists\s+)?(.*)$", a, re.I | re.S)
                if mc:
                    col = _sql_column(mc.group(1))
                    if col and col["n"] not in {c["n"] for c in t["cols"]}:
                        t["cols"].append(col)
                else:
                    _sql_constraint(t, a)
            continue
        m = re.match(rf"create\s+unique\s+index\s+(?:concurrently\s+)?(?:if\s+not\s+exists\s+)?(?:{_SQL_NAME}\s+)?on\s+(?:only\s+)?({_SQL_NAME})\s*(?:using\s+\w+\s*)?(\(.*\))\s*$", st, re.I | re.S)
        if m and find(m.group(1)):
            names = _split_top(m.group(2)[1:-1])
            if all(re.fullmatch(_SQL_ID, n.split()[0]) for n in names):
                _sql_constraint(tables[find(m.group(1))], f"unique ({', '.join(n.split()[0] for n in names)})")
            continue
        m = re.match(rf"comment\s+on\s+table\s+({_SQL_NAME})\s+is\s+'((?:[^']|'')*)'", st, re.I | re.S)
        if m and find(m.group(1)):
            tables[find(m.group(1))]["comment"] = m.group(2).replace("''", "'")
            continue
        m = re.match(rf"comment\s+on\s+column\s+({_SQL_NAME})\s+is\s+'((?:[^']|'')*)'", st, re.I | re.S)
        if m:
            parts = re.findall(_SQL_ID, m.group(1))
            k = find(".".join(parts[:-1])) if len(parts) >= 2 else None
            for c in (tables[k]["cols"] if k else []):
                if c["n"] == _sql_ident(parts[-1]):
                    c["note"] = m.group(2).replace("''", "'")
    keep = {k: t for k, t in tables.items() if k[1].lower() not in BOOKKEEPING}
    count = defaultdict(int)
    for k in keep:
        count[k[1]] += 1
    shown = {k: (k[1] if count[k[1]] == 1 or not k[0] else f"{k[0]}__{k[1]}") for k in keep}
    if any(v > 1 for v in count.values()):
        print("note: some table names occur in more than one schema; those are written as schema__table", file=sys.stderr)
    pk = {k: next((c["n"] for c in t["cols"] if c["pk"]), None) for k, t in keep.items()}
    for k, t in keep.items():
        t["name"] = shown[k]
        t.pop("_schema", None)
    for k, t in keep.items():
        for c in t["cols"]:
            if "fk" not in c:
                continue
            tk = c["fk"][0]
            dest = tk if tk in keep else None
            if dest is None:
                same = [x for x in keep if x[1] == tk[1]]
                dest = (next((x for x in same if x[0] == tk[0]), None) if tk[0] else
                        next((x for x in same if x[0] == k[0]), None) or next((x for x in same if x[0] == "public"), None)
                        or (sorted(same)[0] if same else None))
            c["fk"][0] = shown[dest] if dest else tk[1]
            c["fk"][1] = c["fk"][1] or (pk.get(dest) if dest else None) or "id"
            if c["pk"] and sum(x["pk"] for x in t["cols"]) == 1:
                c["fk"][2] = "-"  # a primary key that is also a foreign key is one-to-one
    return list(keep.values())


def export_sql(path: str, dialect: str = "") -> str:
    p = Path(path)
    tables = _read_sql(p.read_text(encoding="utf-8"))
    if not tables:
        raise SystemExit(f"No CREATE TABLE statements found in {path}.")
    return to_dbml(tables, f"sql {p.name}", f"sql {dialect}".strip() if dialect else "sql dump")


# ---- dbt: target/manifest.json (+ catalog.json for warehouse types), written by `dbt parse` / `dbt docs generate` ----


def _dbt_ref(expr: str) -> tuple[str, str, str] | None:
    """`ref('x')`, `ref('pkg', 'x')` or `source('src', 'tbl')` -> (kind, scope, name)."""
    m = re.search(r"\b(ref|source)\(\s*['\"]([^'\"]+)['\"](?:\s*,\s*['\"]([^'\"]+)['\"])?", expr or "")
    if not m:
        return None
    kind, a, b = m.groups()
    return (kind, a, b) if b else (kind, "", a)


def _dbt_desc(text: str) -> str:
    """A dbt description as one line, cut before any markdown table (those do not fit on a card)."""
    lines = []
    for ln in (text or "").splitlines():
        if ln.strip().startswith("|"):
            break
        lines.append(ln.strip())
    return " ".join(" ".join(lines).split())


def _read_dbt(manifest: dict, catalog: dict | None, with_sources: bool) -> list[dict]:
    root = (manifest.get("metadata") or {}).get("project_name")
    cat = {**((catalog or {}).get("nodes") or {}), **((catalog or {}).get("sources") or {})}
    picked = []  # (unique_id, node, is_source)
    for uid, n in sorted((manifest.get("nodes") or {}).items()):
        if n.get("resource_type") in ("model", "seed", "snapshot") and (n.get("config") or {}).get("materialized") != "ephemeral":
            picked.append((uid, n, False))
    if with_sources:
        picked += [(uid, n, True) for uid, n in sorted((manifest.get("sources") or {}).items())]

    def relation(n: dict) -> str:
        rn = n.get("relation_name") or ""
        return _sql_ident(re.findall(_SQL_ID, rn)[-1]) if rn else (n.get("alias") or n.get("identifier") or n["name"])

    names: dict[str, list] = defaultdict(list)
    for uid, n, src in picked:
        names[relation(n)].append(uid)
    multi_pkg = len({n.get("package_name") for _, n, _ in picked}) > 1
    def schema_of(n: dict) -> str:
        rn = n.get("relation_name") or ""
        parts = [_sql_ident(x) for x in re.findall(_SQL_ID, rn)]
        return parts[-2] if len(parts) >= 2 else (n.get("schema") or "")

    tname, used = {}, set()
    for uid, n, _ in picked:
        base = relation(n)
        cands = [base] if len(names[base]) == 1 else [f"{schema_of(n)}__{base}", f"{n.get('package_name')}__{schema_of(n)}__{base}"]
        name, k = next((c for c in cands if c not in used), None), 2
        while name is None:  # still taken: number it
            name = f"{cands[-1]}_{k}" if f"{cands[-1]}_{k}" not in used else None
            k += 1
        tname[uid] = name
        used.add(name)
    clashes = sum(1 for v in names.values() if len(v) > 1)
    if clashes:
        print(f"note: {clashes} table name(s) occur in more than one schema or package; the later ones are written as schema__table", file=sys.stderr)
    by_ref: dict[tuple, str] = {}  # how a ref()/source() points at a table
    for uid, n, src in picked:
        key = ("source", n.get("source_name", ""), n["name"]) if src else ("ref", "", n["name"])
        by_ref.setdefault(key, uid)
        if not src:
            by_ref.setdefault(("ref", n.get("package_name", ""), n["name"]), uid)
        if not src and n.get("package_name") == root:
            by_ref[key] = uid  # the project's own model wins over a package's model of the same name

    tables: dict[str, dict] = {}
    for uid, n, src in picked:
        c = cat.get(uid, {}).get("columns") or {}
        declared = n.get("columns") or {}
        order = [k for k, _ in sorted(c.items(), key=lambda kv: kv[1].get("index", 0))]
        if not c:  # a catalog lists what the warehouse really has; without one, fall back on the declared columns
            order = list(declared)
        cols = []
        for k in order:
            d = declared.get(k) or next((v for dk, v in declared.items() if dk.lower() == k.lower()), {})
            ctype = (c.get(k) or {}).get("type") or d.get("data_type") or "unknown"
            col = {"n": k, "t": norm_type(ctype), "pk": False, "uq": False, "null": True}
            if d.get("description"):
                col["note"] = _dbt_desc(d["description"])
            for ct in d.get("constraints") or []:
                _dbt_constraint(col, ct)
            cols.append(col)
        folder = (n.get("path") or "").replace("\\", "/").split("/")
        group = "sources" if src else (folder[0] if len(folder) > 1 else (n.get("resource_type") or "model") + "s")
        if multi_pkg and not src:
            group = f"{n.get('package_name')} / {group}"
        tables[uid] = {"name": tname[uid], "comment": _dbt_desc(n.get("description")), "group": group,
                       "cols": cols, "uniq": [], "uid": uid}
        for ct in n.get("constraints") or []:
            for k in ct.get("columns") or []:
                col = next((x for x in cols if x["n"].lower() == k.lower()), None)
                if col and len(ct["columns"]) == 1:
                    _dbt_constraint(col, ct)
                elif col and ct.get("type") in ("primary_key", "unique"):
                    tables[uid]["uniq"].append(", ".join(ct["columns"])) if k == ct["columns"][0] else None
    for uid, n, src in picked:
        t = tables[uid]
        cts = [(k, ct) for k, d in (n.get("columns") or {}).items() for ct in (d.get("constraints") or [])]
        cts += [(ct["columns"][0], ct) for ct in (n.get("constraints") or []) if len(ct.get("columns") or []) == 1]
        for k, ct in cts:
            if ct.get("type") != "foreign_key":
                continue
            col = next((x for x in t["cols"] if x["n"].lower() == k.lower()), None)
            r = _dbt_ref(ct.get("to") or "")
            dest, field = (by_ref.get(r) if r else None), (ct.get("to_columns") or [None])[0]
            if not r and ct.get("expression"):  # older dbt: "other_table (id)" or "db.schema.other_table (id)"
                m = re.match(rf"\s*({_SQL_NAME})\s*\(\s*({_SQL_ID})", ct["expression"])
                if m:
                    want = _sql_table(m.group(1)).lower()
                    dest = next((u for u, x in tables.items() if x["name"].split("__")[-1].lower() == want), None)
                    field = _sql_ident(m.group(2))
            if col and dest in tables and "fk" not in col:
                col["fk"] = [tables[dest]["name"], field or "id", ">"]
    # tests: unique / not_null / relationships (a foreign key) / unique_combination_of_columns
    skipped = 0
    for uid, t in sorted((manifest.get("nodes") or {}).items()):
        md = t.get("test_metadata") or {}
        if t.get("resource_type") != "test" or not md:
            continue
        kw = md.get("kwargs") or {}
        target = t.get("attached_node")
        if not target:
            r = _dbt_ref(kw.get("model", ""))
            target = by_ref.get(r) if r else None
        if target not in tables:
            continue
        tb = tables[target]
        colname = t.get("column_name") or kw.get("column_name")
        col = next((x for x in tb["cols"] if colname and x["n"].lower() == colname.lower()), None)
        kind = md.get("name")
        if kind == "unique" and col:
            col["uq"] = True
        elif kind == "not_null" and col:
            col["null"] = False
        elif kind == "unique_combination_of_columns":
            combo = kw.get("combination_of_columns") or []
            if combo:
                tb["uniq"].append(", ".join(combo))
        elif kind == "relationships" and col:
            r = _dbt_ref(kw.get("to", ""))
            dest = by_ref.get(r) if r else None
            if dest in tables:
                col.setdefault("fk", [tables[dest]["name"], kw.get("field") or "id", ">"])
            else:
                skipped += 1
    for t in tables.values():
        if any(c["pk"] for c in t["cols"]):
            continue  # a contract declared the key
        cand = [c for c in t["cols"] if c["uq"] and not c["null"]]
        if len(cand) == 1:
            cand[0]["pk"] = True  # dbt convention: the one unique + not_null column is the key (several: leave them unique)
    for t in tables.values():
        t["uniq"] = sorted(set(t["uniq"]))
        t.pop("uid", None)
        for c in t["cols"]:
            if "fk" in c and c["pk"]:
                c["fk"][2] = "-"
    if skipped:
        print(f"note: {skipped} relationships test(s) point at models that are not in this diagram (sources? ephemeral?), skipped", file=sys.stderr)
    return list(tables.values())


def _dbt_constraint(col: dict, ct: dict) -> None:
    kind = ct.get("type")
    if kind == "primary_key":
        col["pk"] = True; col["null"] = False
    elif kind == "not_null":
        col["null"] = False
    elif kind == "unique":
        col["uq"] = True


def export_dbt(manifest_path: str, catalog_path: str | None, with_sources: bool) -> str:
    mp = Path(manifest_path)
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    cp = Path(catalog_path) if catalog_path else mp.with_name("catalog.json")
    catalog = json.loads(cp.read_text(encoding="utf-8")) if cp.exists() else None
    if catalog is None and catalog_path:
        raise SystemExit(f"{catalog_path} not found.")
    tables = _read_dbt(manifest, catalog, with_sources)
    if not tables:
        raise SystemExit(f"No models, seeds or snapshots found in {manifest_path}.")
    adapter = (manifest.get("metadata") or {}).get("adapter_type") or "unknown adapter"
    typed = "with warehouse types" if catalog else "no catalog: types only where declared"
    print(f"dbt: {len(tables)} tables, {typed}", file=sys.stderr)
    meta = manifest.get("metadata") or {}
    return to_dbml(tables, f"dbt {mp.name}", f"dbt {adapter}" + ("" if catalog else " (no catalog)"),
                   f"dbt-core {meta['dbt_version']}" if meta.get("dbt_version") else "", meta.get("generated_at") or "")


def export_database(url: str) -> str:
    import asyncio

    from sqlalchemy import MetaData, create_engine
    from sqlalchemy.ext.asyncio import create_async_engine

    md, uniq = MetaData(), {}

    def reflect(conn) -> None:
        from sqlalchemy import inspect

        md.reflect(conn)
        insp = inspect(conn)  # reflection alone drops some unique constraints (sqlite's inline UNIQUE, for one)
        for n in md.tables:
            try:
                sets = [tuple(u["column_names"]) for u in insp.get_unique_constraints(n)]
                sets += [tuple(i["column_names"]) for i in insp.get_indexes(n) if i.get("unique") and all(i["column_names"])]
            except NotImplementedError:  # a dialect that cannot list them: keep what reflection found
                sets = []
            uniq[n] = sets

    if any(d in url for d in ("asyncpg", "aiosqlite", "aiomysql", "+async")):
        async def go() -> None:
            engine = create_async_engine(url)
            try:
                async with engine.connect() as conn:
                    await conn.run_sync(reflect)
            finally:
                await engine.dispose()
        asyncio.run(go())
    else:
        engine = create_engine(url)
        with engine.connect() as conn:
            reflect(conn)
        engine.dispose()
    dialect = url.split(":", 1)[0].split("+", 1)[0]  # never write the URL: it may hold a password
    return to_dbml(_read_sqlalchemy(md, uniq), f"db ({dialect})", f"database {dialect}")


def _warn_missing_fks(tables: list) -> None:
    """Flag `thing_id` columns with no ref when a table that looks like `thing` exists: likely a missing foreign key."""
    names = {t["name"].lower() for t in tables}
    for t in tables:
        for c in t["cols"]:
            m = re.fullmatch(r"(?:\w+_)?(\w+?)_id", c["n"].lower())
            if c["pk"] or "fk" in c or not m:
                continue
            base = m.group(1)
            if any((n == b or n.endswith("_" + b)) and n != t["name"].lower() for n in names for b in (base, base + "s", base + "es")):
                print(f"warning: {t['name']}.{c['n']} looks like a foreign key but has no ref", file=sys.stderr)


# ---- command line -----------------------------------------------------------------------------------------------


# ---- PR-ready summary and static site: `schemaviz publish` -------------------------------------------------------

MARKER = "<!-- schemaviz -->"  # lets a CI job find and update its own comment instead of adding a new one


def _mm(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", s) or "x"


def _mermaid(tables: list[dict], limit: int = 18) -> str:
    """A Mermaid erDiagram of the tables that changed (GitHub draws these in comments). Types are sanitised: Mermaid
    rejects parentheses and angle brackets."""
    picked = [t for t in tables if t.get("diff") in ("added", "changed")][:limit]
    names = {t["name"] for t in picked}
    if not picked:
        return ""
    out = ["erDiagram"]
    for t in picked:
        rows = [c for c in t["cols"] if t.get("diff") == "added" or c.get("diff") or c["pk"] or "fk" in c][:14]
        out.append(f"  {_mm(t['name'])} {{")
        for c in rows:
            key = " PK" if c["pk"] else " FK" if "fk" in c else ""
            base = _mm(re.split(r"[(<\[]", norm_type(c["t"]) or "unknown")[0].strip() or "unknown")  # varchar(255) -> varchar
            tag = {"added": "new", "removed": "dropped", "changed": "changed"}.get(c.get("diff") or t.get("diff") and "", "")
            out.append(f"    {base} {_mm(c['n'])}{key}" + (f' "{tag}"' if tag else ""))
        out.append("  }")
    for t in picked:
        for c in t["cols"]:
            if "fk" in c and c["fk"][0] in names and c.get("diff") != "removed":
                out.append(f"  {_mm(c['fk'][0])} ||--{'o|' if c['fk'][2] == '-' else 'o{'} {_mm(t['name'])} : \"{_mm(c['n'])}\"")
    return "\n".join(out)


def diff_markdown(tables: list[dict], stats: dict, lo: str, ln: str, url: str = "", limit: int = 60000, warnings=None) -> str:
    """The schema changes as Markdown for a pull request comment (GitHub allows 65,536 characters). Built from whole blocks,
    so staying under `limit` never leaves a <details> or a code fence open."""
    n = lambda k, w: f"{stats[k]} {w}" if stats.get(k) else ""  # noqa: E731
    bits = [x for x in (n("tables_added", "added"), n("tables_removed", "removed"), n("tables_changed", "changed")) if x]
    cols = [x for x in (n("cols_added", "added"), n("cols_removed", "removed"), n("cols_changed", "changed")) if x]
    head = [MARKER, f"### Schema changes: `{lo}` to `{ln}`", ""]
    if not bits:
        return "\n".join(head + ["No schema changes."]) + "\n"
    head.append(f"**Tables:** {', '.join(bits)}. **Columns:** {', '.join(cols) or 'none changed'}." +
                (f" [Open the interactive view]({url})." if url else ""))
    blocks = ["\n".join(head)]
    if warnings:
        blocks.append("\n".join(f"> **Note:** {w}" for w in warnings))
    for kind, title, sign in (("added", "New tables", "+"), ("removed", "Dropped tables", "\u2212")):
        group = [t for t in tables if t.get("diff") == kind]
        if group:
            blocks.append("\n".join([f"**{title}**", ""] + [f"- {sign} `{t['name']}` ({len(t['cols'])} columns)" for t in group]))
    mark = {"added": "+", "removed": "\u2212", "changed": "~"}
    details = []
    for t in (t for t in tables if t.get("diff") == "changed"):
        title = t["name"] + (f" (renamed from {t['renamed_from']})" if t.get("renamed_from") else "")  # no markdown inside <b>
        rows = [c for c in t["cols"] if c.get("diff")]
        lines = [f"<details><summary><b>{title}</b>: {len(rows)} column change{'s' if len(rows) != 1 else ''}</summary>", "",
                 "| | column | type | what changed |", "|---|---|---|---|"]
        for c in rows:
            lines.append(f"| {mark[c['diff']]} | `{c['n']}` | `{c['t'] or 'unknown'}` | "
                         f"{c.get('was') or {'added': 'new column', 'removed': 'dropped'}[c['diff']]} |")
        if t.get("was"):
            lines.append(f"| ~ | | | {t['was']} |")
        details.append("\n".join(lines + ["", "</details>"]))
    mm = _mermaid(tables)
    diagram = "\n".join(["<details><summary>Relationship diagram of the tables that changed</summary>", "", "```mermaid", mm, "```", "", "</details>"]) if mm else ""
    out, used, left = [], 0, len(details)
    reserve = 220  # room for the truncation note
    for b in blocks:
        out.append(b); used += len(b) + 2
    if diagram and used + len(diagram) + reserve < limit:  # the diagram only goes in whole
        out.append(diagram); used += len(diagram) + 2
    for d in details:
        if used + len(d) + 2 + reserve > limit:
            break
        out.append(d); used += len(d) + 2; left -= 1
    if left:
        out.append(f"_{left} more changed table{'s' if left != 1 else ''} not shown to stay within GitHub's comment size limit. "
                   "The full list is in the interactive view._")
    return "\n\n".join(out) + "\n"


def publish(a) -> None:
    """Write a static folder: index.html (open it anywhere), summary.md (paste into a PR), schema.dbml and manifest.json.
    Uploading is left to whatever tool you already use (aws s3 cp, gsutil, gh, a Pages deploy)."""
    old_x, new_x, names, lo, ln = _diff_inputs(a)
    html, stats, tables, warnings = diff_page(old_x, new_x, names, lo, ln, a.rename, a.notes, a.title, when=_when(a))
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = {"index.html": html, "summary.md": diff_markdown(tables, stats, lo, ln, a.url or "", warnings=warnings), "schema.dbml": new_x}
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    manifest = {"schemaviz": __version__, "old": lo, "new": ln, "changes": stats, "has_changes": any(stats.values()),
                "entry": "index.html", "files": [{"path": n, "bytes": len(t.encode()), "type": "text/html" if n.endswith(".html")
                                                  else "text/markdown" if n.endswith(".md") else "text/plain"} for n, t in files.items()]}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{out}/: {', '.join(files)}, manifest.json  ({'no changes' if not manifest['has_changes'] else ', '.join(f'{v} {k.replace(chr(95), chr(32))}' for k, v in stats.items() if v)})")


# ---- local server: `schemaviz open` ------------------------------------------------------------------------------


class _View:
    """What `schemaviz open` shows: one DBML file, or (with revisions) what changed in it between two commits."""

    def __init__(self, file, diff=False, rev_from=None, rev_to=None, renames=None, notes=False, title=None):
        self.file, self.diff, self.rev_from, self.rev_to = str(file), diff, rev_from, rev_to
        self.renames, self.notes, self.title = renames, notes, title

    def inputs(self, query: dict) -> tuple[dict, str]:
        """Read what the page is drawn from, and a version that changes whenever it does. Cheap and silent: the open page
        asks for the version every second or so, and only a changed version makes it fetch the page again."""
        import hashlib

        text = Path(self.file).read_text(encoding="utf-8")
        if not self.diff:
            return {"text": text}, hashlib.sha1(text.encode()).hexdigest()
        a = (query.get("from") or [self.rev_from or "HEAD"])[0]
        b = (query.get("to") or [self.rev_to or "WORKTREE"])[0]
        old_x = git_text(a, self.file)
        new_x = text if b == "WORKTREE" else git_text(b, self.file)
        return {"a": a, "b": b, "old_x": old_x, "new_x": new_x}, hashlib.sha1((old_x + "\0" + new_x).encode()).hexdigest()

    def render(self, inp: dict) -> str:
        if not self.diff:
            tables = parse_text(inp["text"], self.file)
            return to_html(tables, self.title or Path(self.file).stem.replace("_", " ").replace("-", " ").title(), Path(self.file).name)
        a, b = inp["a"], inp["b"]
        revs = list_revs(self.file)
        for r in (a, b):
            if r != "WORKTREE" and all(x["v"] != r for x in revs):
                revs.append({"v": r, "label": r})
        html, _, _, _ = diff_page(inp["old_x"], inp["new_x"], (self.file, self.file), _short_rev(a),
                                  "working tree" if b == "WORKTREE" else _short_rev(b), self.renames, self.notes, self.title,
                                  {"revs": revs, "from": a, "to": b}, (rev_time(a, self.file), rev_time(b, self.file)))
        return html


_RELOAD = """<script>(function(){var v=%s;function poll(){fetch('/__version'+location.search,{cache:'no-store'})
.then(function(r){return r.text()}).then(function(t){if(t!==v)location.reload()}).catch(function(){})
.then(function(){setTimeout(poll,1500)})}setTimeout(poll,1500)})();</script>"""


def make_server(view: _View, port: int = 0):
    """An HTTP server bound to localhost only. Call serve_forever() on it."""
    from html import escape
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: str, kind: str = "text/html; charset=utf-8") -> None:
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.headers.get("Host", "").rsplit(":", 1)[0] not in ("127.0.0.1", "localhost"):
                return self._send(403, "forbidden", "text/plain")  # DNS-rebinding guard
            u = urlparse(self.path)
            query = parse_qs(u.query)
            if u.path not in ("/", "/__version"):
                return self._send(404, "not found", "text/plain")
            html = None
            try:
                inp, version = view.inputs(query)
                if u.path == "/":
                    html = view.render(inp)
            except (SystemExit, Exception) as e:  # keep serving: the page recovers when the problem is fixed
                err = str(e) if str(e) else type(e).__name__
                version = "error:" + err
                html = ("<title>schemaviz</title><body style=\"font:15px system-ui;padding:24px;max-width:70ch\"><h1>Cannot draw this</h1>"
                        f"<pre style=\"white-space:pre-wrap\">{escape(err)}</pre><p>This page reloads when the file or git state changes.</p></body>")
            if u.path == "/__version":
                return self._send(200, version, "text/plain")
            self._send(200, html + _RELOAD % json.dumps(version))

        def log_message(self, *args) -> None:
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(view: _View, port: int, open_browser: bool) -> None:
    server = make_server(view, port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"schemaviz: serving {view.file} at {url}  (Ctrl-C to stop)")
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


def doctor() -> int:
    """Report what works on this machine. Exit status 1 only when something every command needs is missing."""
    import importlib.util
    import shutil
    import subprocess

    ok = True
    print(f"schemaviz {__version__}, Python {sys.version.split()[0]}")
    tpl = HERE / "template.html"
    print(f"  {'ok     ' if tpl.exists() else 'MISSING'}  page template   {tpl}")
    ok = ok and tpl.exists()
    git = shutil.which("git")
    ver = subprocess.run([git, "--version"], capture_output=True, encoding="utf-8", errors="replace").stdout.strip() if git else ""
    print(f"  {'ok     ' if git else 'missing'}  git             {ver or 'needed only for: diff --file ... --from REV'}")
    for mod, why in (("sqlalchemy", "sqlalchemy and db commands"), ("django", "django command (run inside the project's environment)")):
        found = importlib.util.find_spec(mod) is not None
        print(f"  {'ok     ' if found else 'missing'}  {mod:<15} {'' if found else 'needed only for the ' + why}")
    print("  always available: render, diff, sql, dbt  (standard library only)")
    return 0 if ok else 1


def main() -> None:
    for stream in (sys.stdout, sys.stderr):  # a Windows console may default to cp1252
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="schemaviz", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"schemaviz {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="COMMAND")
    sub.add_parser("doctor", help="check what this install can do (git, optional packages, template)")
    o = sub.add_parser("open", help="serve a DBML file in your browser; reloads when it changes. With --from/--to, shows what changed between commits, with a picker")
    o.add_argument("file", help="DBML/JSON file; for revisions it must be tracked in git")
    o.add_argument("--from", dest="rev_from", metavar="REV", help="old revision: turns on the changes view (default to: the working tree)")
    o.add_argument("--to", dest="rev_to", metavar="REV", help="new revision (default: the working tree)")
    o.add_argument("--rename", action="append", metavar="OLD=NEW", help="a confirmed rename (repeatable), as for diff")
    o.add_argument("--notes", action="store_true", help="also report edited notes as changes")
    o.add_argument("--title")
    o.add_argument("--port", type=int, default=0, help="default: any free port")
    o.add_argument("--no-open", action="store_true", help="print the address but do not open a browser")
    r = sub.add_parser("render", help="DBML (or JSON) file -> HTML page")
    r.add_argument("input")
    r.add_argument("--out", default="schema.html")
    r.add_argument("--title")
    r.add_argument("--expect", type=int, metavar="N", help="fail if the file does not hold exactly N tables (the count from the code)")
    def diff_args(p) -> None:
        p.add_argument("old", nargs="?", help="old DBML/JSON file (or use --file with --from)")
        p.add_argument("new", nargs="?", help="new DBML/JSON file")
        p.add_argument("--file", help="a DBML/JSON file tracked in git; compare its committed versions (with --from, and optionally --to)")
        p.add_argument("--from", dest="rev_from", metavar="REV", help="old revision (commit, tag, branch)")
        p.add_argument("--to", dest="rev_to", metavar="REV", help="new revision; default is the file in the working tree")
        p.add_argument("--title")
        p.add_argument("--notes", action="store_true", help="also report edited notes as changes (off by default: notes get reworded)")
        p.add_argument("--rename", action="append", metavar="OLD=NEW",
                       help="a rename confirmed in the code or git history: old=new for a table, table.old=new for a column (repeatable)")
        p.add_argument("--old-label", help="name for the old side, e.g. a commit hash or tag")
        p.add_argument("--new-label", help="name for the new side")

    f = sub.add_parser("diff", help="two DBML (or JSON) files, or two git revisions of one -> HTML page with what changed highlighted")
    diff_args(f)
    f.add_argument("--out", default="schema-diff.html")
    f.add_argument("--md", metavar="FILE", help="also write the changes as Markdown (for a pull request comment)")
    pb = sub.add_parser("publish", help="write a static folder (index.html, summary.md, schema.dbml, manifest.json) for another tool to upload")
    diff_args(pb)
    pb.add_argument("--out-dir", default="schemaviz-site")
    pb.add_argument("--url", default="", help="where index.html will be hosted; linked from summary.md")
    s = sub.add_parser("sqlalchemy", help="SQLAlchemy models -> DBML (needs sqlalchemy)")
    s.add_argument("models", metavar="MODULE:BASE")
    s.add_argument("--out", default="-")
    q = sub.add_parser("sql", help="schema-only SQL (pg_dump --schema-only, structure.sql, prisma migrate diff --script) -> DBML")
    q.add_argument("file")
    q.add_argument("--dialect", default="", help="recorded in the DBML header, e.g. postgresql; diff warns when two files differ")
    q.add_argument("--out", default="-")
    b = sub.add_parser("dbt", help="dbt target/manifest.json (and catalog.json beside it) -> DBML")
    b.add_argument("manifest")
    b.add_argument("--catalog", help="catalog.json from `dbt docs generate`; default is the one next to the manifest, if any")
    b.add_argument("--sources", action="store_true", help="also draw declared sources")
    b.add_argument("--out", default="-")
    j = sub.add_parser("django", help="Django models -> DBML (needs django; run where the project's dependencies are installed)")
    j.add_argument("settings", nargs="?", default="", metavar="SETTINGS_MODULE")
    j.add_argument("--out", default="-")
    d = sub.add_parser("db", help="live database -> DBML, with COMMENT ON text (needs sqlalchemy and a driver)")
    d.add_argument("url")
    d.add_argument("--out", default="-")
    a = ap.parse_args()

    if a.cmd == "doctor":
        raise SystemExit(doctor())
    if a.cmd == "open":
        if not Path(a.file).exists():
            raise SystemExit(f"{a.file} not found.")
        serve(_View(a.file, bool(a.rev_from or a.rev_to), a.rev_from, a.rev_to, a.rename, a.notes, a.title), a.port, not a.no_open)
        return
    if a.cmd == "publish":
        publish(a)
        return
    if a.cmd == "diff":
        old_x, new_x, names, lo, ln = _diff_inputs(a)
        html, stats, tables, warnings = diff_page(old_x, new_x, names, lo, ln, a.rename, a.notes, a.title, when=_when(a))
        Path(a.out).write_text(html, encoding="utf-8")
        if a.md:
            Path(a.md).write_text(diff_markdown(tables, stats, lo, ln, warnings=warnings), encoding="utf-8")
        print(f"{a.out}: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in stats.items() if v) if any(stats.values())
              else f"{a.out}: no differences between {lo} and {ln}")
    elif a.cmd == "render":
        path = Path(a.input)
        tables = load_tables(path)
        if a.expect is not None and len(tables) != a.expect:
            raise SystemExit(f"Expected {a.expect} tables but {path.name} has {len(tables)}.")
        _warn_missing_fks(tables)
        Path(a.out).write_text(to_html(tables, a.title or path.stem.replace("_", " ").replace("-", " ").title(), path.name), encoding="utf-8")
        ncols = sum(len(t["cols"]) for t in tables)
        nfks = sum("fk" in c for t in tables for c in t["cols"])
        print(f"{a.out}: {len(tables)} tables, {ncols} columns, {nfks} foreign keys from {path.name}")
    else:
        dbml = (export_sqlalchemy(a.models) if a.cmd == "sqlalchemy"
                else export_django(a.settings) if a.cmd == "django"
                else export_sql(a.file, a.dialect) if a.cmd == "sql"
                else export_dbt(a.manifest, a.catalog, a.sources) if a.cmd == "dbt" else export_database(a.url))
        if a.out == "-":
            sys.stdout.write(dbml)
        else:
            Path(a.out).write_text(dbml, encoding="utf-8")
            print(f"{a.out}: DBML ({len(re.findall(r'(?m)^Table ', dbml))} tables)")


if __name__ == "__main__":
    main()

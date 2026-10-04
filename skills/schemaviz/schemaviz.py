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

HERE = Path(__file__).parent


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
        m = re.match(r'\s*("[^"]+"|`[^`]+`|\w+)\s+("[^"]+"|[\w.]+(?:\([^)]*\))?(?:\[\])?)\s*(?:\[(.*)\])?\s*$', ln, re.S)
        if not m:
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
        depth[name] = 0 if not parents else 1 + max(d(p, seen + (name,)) for p in parents)
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
    names = {t["name"] for t in tables}
    for t in tables:  # a ref to a table that isn't in the file can't be drawn
        for c in t["cols"]:
            if "fk" in c and c["fk"][0] not in names:
                del c["fk"]
    hub = find_hub(tables)
    assign_groups(tables, hub)
    data = json.dumps({"title": title, "source": source, "hub": hub, "diff": diff, "groups": order_groups(tables, hub)}, ensure_ascii=False)
    html = (HERE / "template.html").read_text()
    return html.replace("/*TITLE*/", re.sub(r"[<>&]", "", title)).replace("/*DATA*/{}", data.replace("</", "<\\/"))


def load_tables(path: Path) -> list[dict]:
    text = path.read_text()
    tables = parse_json(text) if path.suffix == ".json" else parse_dbml(text)
    if not tables:
        raise SystemExit(f"No tables found in {path}.")
    return tables


def load_from_git(rev: str, file: str) -> list[dict]:
    """The tables in `file` as committed at `rev` (any tag, branch or commit), read with `git show`."""
    import subprocess

    r = subprocess.run(["git", "show", f"{rev}:./{file}"], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"git could not read {file} at {rev}: {r.stderr.strip()}")
    tables = parse_json(r.stdout) if file.endswith(".json") else parse_dbml(r.stdout)
    if not tables:
        raise SystemExit(f"No tables found in {file} at {rev}.")
    return tables


def _short_rev(rev: str) -> str:
    """Commit hashes are shortened to 7 characters; tags, branches and HEAD~2 are shown as typed."""
    return rev[:7] if re.fullmatch(r"[0-9a-f]{12,40}", rev) else rev


def _col_changes(o: dict, n: dict, trn: dict | None = None) -> list[str]:
    """What differs between two versions of the same column, as short phrases."""
    out = []
    if o["t"] != n["t"]:
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
    if o.get("note") != n.get("note"):
        out.append("note edited")
    return out


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


def diff_tables(old: list[dict], new: list[dict], renames: list[str] | None = None) -> tuple[list[dict], dict]:
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
            why = _col_changes(oc, c, trn)
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


def to_dbml(tables: list[dict]) -> str:
    q = lambda s: "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ") + "'"
    out = ["// Generated by scripts/schemaviz. Paste into https://dbdiagram.io/d\n"]
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
            typ = f'"{c["t"]}"' if " " in c["t"] and "(" not in c["t"] else c["t"]
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


def _read_sqlalchemy(md) -> list[dict]:
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
        if t.name == "alembic_version":
            continue
        sets = [tuple(c.name for c in k.columns) for k in t.constraints if isinstance(k, UniqueConstraint)]
        sets += [tuple(c.name for c in ix.columns) for ix in t.indexes if isinstance(ix, Index) and ix.unique]
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
    return to_dbml(_read_sqlalchemy(md))


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
    return to_dbml(_read_django())


def export_database(url: str) -> str:
    import asyncio

    from sqlalchemy import MetaData, create_engine
    from sqlalchemy.ext.asyncio import create_async_engine

    md = MetaData()
    if any(d in url for d in ("asyncpg", "aiosqlite", "aiomysql", "+async")):
        async def go() -> None:
            engine = create_async_engine(url)
            try:
                async with engine.connect() as conn:
                    await conn.run_sync(md.reflect)
            finally:
                await engine.dispose()
        asyncio.run(go())
    else:
        engine = create_engine(url)
        with engine.connect() as conn:
            md.reflect(conn)
        engine.dispose()
    return to_dbml(_read_sqlalchemy(md))


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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="DBML (or JSON) file -> HTML page")
    r.add_argument("input")
    r.add_argument("--out", default="schema.html")
    r.add_argument("--title")
    r.add_argument("--expect", type=int, metavar="N", help="fail if the file does not hold exactly N tables (the count from the code)")
    f = sub.add_parser("diff", help="two DBML (or JSON) files -> HTML page with what changed highlighted")
    f.add_argument("old", nargs="?", help="old DBML/JSON file (or use --file with --from)")
    f.add_argument("new", nargs="?", help="new DBML/JSON file")
    f.add_argument("--file", help="a DBML/JSON file tracked in git; compare its committed versions (with --from, and optionally --to)")
    f.add_argument("--from", dest="rev_from", metavar="REV", help="old revision (commit, tag, branch)")
    f.add_argument("--to", dest="rev_to", metavar="REV", help="new revision; default is the file in the working tree")
    f.add_argument("--out", default="schema-diff.html")
    f.add_argument("--title")
    f.add_argument("--rename", action="append", metavar="OLD=NEW",
                   help="a rename confirmed in the code or git history: old=new for a table, table.old=new for a column (repeatable)")
    f.add_argument("--old-label", help="name for the old side, e.g. a commit hash or tag")
    f.add_argument("--new-label", help="name for the new side")
    s = sub.add_parser("sqlalchemy", help="SQLAlchemy models -> DBML (needs sqlalchemy)")
    s.add_argument("models", metavar="MODULE:BASE")
    s.add_argument("--out", default="-")
    j = sub.add_parser("django", help="Django models -> DBML (needs django; run where the project's dependencies are installed)")
    j.add_argument("settings", nargs="?", default="", metavar="SETTINGS_MODULE")
    j.add_argument("--out", default="-")
    d = sub.add_parser("db", help="live database -> DBML, with COMMENT ON text (needs sqlalchemy and a driver)")
    d.add_argument("url")
    d.add_argument("--out", default="-")
    a = ap.parse_args()

    if a.cmd == "diff":
        if a.file:
            if not a.rev_from or a.old or a.new:
                raise SystemExit("With --file, give --from REV (and optionally --to REV), and no positional files.")
            old_t = load_from_git(a.rev_from, a.file)
            new_t = load_from_git(a.rev_to, a.file) if a.rev_to else load_tables(Path(a.file))
            lo, ln = a.old_label or _short_rev(a.rev_from), a.new_label or (_short_rev(a.rev_to) if a.rev_to else "working tree")
        else:
            if not (a.old and a.new):
                raise SystemExit("Give two files, or --file F --from REV [--to REV].")
            lo, ln = a.old_label or Path(a.old).name, a.new_label or Path(a.new).name
            old_t, new_t = load_tables(Path(a.old)), load_tables(Path(a.new))
        tables, stats = diff_tables(old_t, new_t, a.rename)
        for h in rename_hints(old_t, new_t, a.rename):
            print(f"hint: possible rename: {h}. Check git, then pass --rename.", file=sys.stderr)
        info = dict(stats, old=lo, new=ln)
        Path(a.out).write_text(to_html(tables, a.title or f"{lo} to {ln}", f"{lo} and {ln}", info))
        print(f"{a.out}: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in stats.items() if v) if any(stats.values())
              else f"{a.out}: no differences between {lo} and {ln}")
    elif a.cmd == "render":
        path = Path(a.input)
        tables = load_tables(path)
        if a.expect is not None and len(tables) != a.expect:
            raise SystemExit(f"Expected {a.expect} tables but {path.name} has {len(tables)}.")
        _warn_missing_fks(tables)
        Path(a.out).write_text(to_html(tables, a.title or path.stem.replace("_", " ").replace("-", " ").title(), path.name))
        ncols = sum(len(t["cols"]) for t in tables)
        nfks = sum("fk" in c for t in tables for c in t["cols"])
        print(f"{a.out}: {len(tables)} tables, {ncols} columns, {nfks} foreign keys from {path.name}")
    else:
        dbml = (export_sqlalchemy(a.models) if a.cmd == "sqlalchemy"
                else export_django(a.settings) if a.cmd == "django" else export_database(a.url))
        if a.out == "-":
            sys.stdout.write(dbml)
        else:
            Path(a.out).write_text(dbml)
            print(f"{a.out}: DBML ({len(re.findall(r'(?m)^Table ', dbml))} tables)")


if __name__ == "__main__":
    main()

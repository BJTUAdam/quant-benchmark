"""Read and search the bundled official Pro docs without a network connection."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

SKILL_ROOT = Path(__file__).resolve().parents[1]


def load_index(skill_root=None):
    base = Path(skill_root or SKILL_ROOT).resolve() / "references/tushare"
    obj = json.loads((base / "snapshot.json").read_text(encoding="utf-8"))
    return base, obj["documents"]


def read_source(base, row):
    base = Path(base).resolve()
    path = base / row["path"]
    if path.is_symlink() or not path.resolve().is_relative_to(base):
        raise ValueError("DOCUMENT_PATH_OUTSIDE_SKILL")
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != row["sha256"]:
        raise ValueError("DOCUMENT_HASH_CHANGED")
    return body.decode("utf-8")


def summary(row):
    return {k: row.get(k) for k in ("api_name", "doc_id", "title", "group", "path", "content_status")}


def document(*, api=None, doc_id=None, start=0, max_chars=12000, skill_root=None):
    if start < 0 or not 1 <= max_chars <= 100000:
        raise ValueError("INVALID_DOCUMENT_SLICE")
    if (api is None) == (doc_id is None):
        raise ValueError("SELECT_ONE_API_OR_DOCUMENT")
    base, rows = load_index(skill_root)
    row = next((r for r in rows if (r.get("api_name") == api if api is not None else str(r["doc_id"]) == str(doc_id))), None)
    if row is None:
        raise ValueError("API_OR_DOCUMENT_NOT_FOUND")
    content = read_source(base, row)
    stop = min(start + max_chars, len(content))
    return {**summary(row), "start": start, "stop": stop, "total_chars": len(content),
            "next_start": stop if stop < len(content) else None,
            "content": content[start:stop], "offline": True}


def search(query, *, group=None, limit=8, skill_root=None):
    if not query.strip() or not 1 <= limit <= 100:
        raise ValueError("INVALID_SEARCH")
    base, rows = load_index(skill_root)
    terms = query.lower().split()
    matches = []
    for row in rows:
        if group and group.lower() not in row.get("group", "").lower():
            continue
        title = " ".join(str(row.get(k) or "") for k in ("api_name", "title", "group"))
        metadata = title + " " + " ".join(row.get("official_params", [])) + " " + " ".join(row.get("official_fields", []))
        body = read_source(base, row)
        haystack = (metadata + "\n" + body).lower()
        if not all(t in haystack for t in terms):
            continue
        name = row.get("api_name") or ""
        score = (100 if name.lower() == query.lower() else 0) + sum(
            (25 if name.lower().startswith(t) else 0) + (8 if t in title.lower() else 0) +
            (3 if t in metadata.lower() else 0) for t in terms)
        location = body.lower().find(terms[0])
        if location < 0:
            location = 0
        excerpt = re.sub(r"\s+", " ", body[max(0, location - 50):location + 190]).strip()
        matches.append((score, name or "~" + row["doc_id"], {**summary(row), "excerpt": excerpt}))
    matches.sort(key=lambda r: (-r[0], r[1]))
    return {"query": query, "total_matches": len(matches), "returned": min(limit, len(matches)),
            "results": [r[2] for r in matches[:limit]], "offline": True}


def listing(*, group=None, limit=30, start=0, skill_root=None):
    if start < 0 or not 1 <= limit <= 100:
        raise ValueError("INVALID_LIST_SLICE")
    _, rows = load_index(skill_root)
    groups = {}
    for row in rows:
        groups[row["group"]] = groups.get(row["group"], 0) + 1
    selected = [r for r in rows if not group or group.lower() in r["group"].lower()]
    end = min(start + limit, len(selected))
    return {"groups": groups, "total": len(selected), "next_start": end if end < len(selected) else None,
            "results": [summary(r) for r in selected[start:end]], "offline": True}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    selector = p.add_mutually_exclusive_group()
    selector.add_argument("--api", help="Exact registered API name, for example daily")
    selector.add_argument("--doc-id", help="Official guide document id, for example 109 (pro_bar)")
    selector.add_argument("--search", help="Search names, fields, titles and full text")
    selector.add_argument("--list", action="store_true", help="List the bundled catalog")
    p.add_argument("--group", help="Filter search/list by part of a Chinese group name")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--start", type=int, default=0, help="Character offset for a document; row offset for --list")
    p.add_argument("--max-chars", type=int, default=12000)
    args = p.parse_args(argv)
    try:
        if args.api is not None or args.doc_id is not None:
            result = document(api=args.api, doc_id=args.doc_id, start=args.start, max_chars=args.max_chars)
        elif args.search is not None:
            result = search(args.search, group=args.group, limit=args.limit)
        else:
            result = listing(group=args.group, start=args.start, limit=args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

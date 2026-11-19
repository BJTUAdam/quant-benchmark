"""Copy the five empty-position templates to a new submission directory."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
FILES = ("strategy.py", "backtest.py", "params.json", "RESEARCH.md", "CHANGELOG.md")


def create(output, *, skill_root=None):
    supplied = Path(output).expanduser()
    if ".." in supplied.parts:
        raise ValueError("OUTPUT_PARENT_TRAVERSAL")
    if supplied.exists() or supplied.is_symlink():
        raise ValueError("OUTPUT_ALREADY_EXISTS")
    parent = supplied.parent.absolute()
    if not parent.is_dir() or any(p.is_symlink() for p in [parent, *parent.parents]):
        raise ValueError("OUTPUT_PARENT_MUST_BE_EXISTING_PLAIN_DIRECTORY")
    destination = parent / supplied.name
    assets = Path(skill_root or SKILL_ROOT).resolve() / "assets/submission"
    contents = {}
    for name in FILES:
        source = assets / name
        if not source.is_file() or source.is_symlink():
            raise ValueError("TEMPLATE_MISSING_OR_LINK: " + name)
        contents[name] = source.read_bytes()
    # mkdir(exist_ok=False) also refuses a destination created by another process.
    destination.mkdir(exist_ok=False)
    written = []
    try:
        for name, body in contents.items():
            with (destination / name).open("xb") as stream:
                stream.write(body)
            written.append(name)
    except Exception:
        # Leave this newly created partial directory for inspection; never remove
        # another process's files or perform recursive cleanup.
        raise
    return {"created": str(destination), "files": {n: hashlib.sha256(contents[n]).hexdigest() for n in written},
            "template_only": True, "strategy_execution": False}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path)
    args = p.parse_args(argv)
    try:
        result = create(args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

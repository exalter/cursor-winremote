#!/usr/bin/env python3
"""List 1C bases from ibases.v8i (UTF-16). Optional --filter erp."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def ibases_path() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
    return Path(appdata) / "1C" / "1CEStart" / "ibases.v8i"


def read_ibases_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    # 1C often writes UTF-16 LE without BOM
    if len(raw) >= 4 and raw[1] == 0 and raw[3] == 0:
        return raw.decode("utf-16-le")
    for enc in ("utf-8-sig", "cp1251", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-16-le", errors="replace")


def parse_ibases(path: Path) -> list[dict]:
    text = read_ibases_text(path)
    entries: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        if line.startswith("[") and line.endswith("]"):
            if cur:
                entries.append(cur)
            cur = {"name": line[1:-1], "connect": "", "folder": "", "version": ""}
            continue
        if cur is None:
            continue
        if line.startswith("Connect="):
            cur["connect"] = line[len("Connect=") :]
        elif line.startswith("Folder="):
            cur["folder"] = line[len("Folder=") :]
        elif line.startswith("DefaultVersion="):
            cur["version"] = line[len("DefaultVersion=") :]
        elif line.startswith("Version="):
            # Prefer DefaultVersion; keep Version only as weak fallback
            if not cur["version"] or cur["version"].count(".") < 3:
                ver = line[len("Version=") :]
                if ver.count(".") >= 3 or not cur["version"]:
                    cur["version"] = ver
    if cur:
        entries.append(cur)
    return entries


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--filter", default="", help="substring in name or connect")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    path = ibases_path()
    if not path.is_file():
        print(json.dumps({"ok": False, "error": f"not found: {path}"}, ensure_ascii=False))
        return 1
    entries = parse_ibases(path)
    needle = (args.filter or "").lower()
    if needle:
        entries = [
            e
            for e in entries
            if needle in e["name"].lower() or needle in e["connect"].lower()
        ]
    if args.json:
        print(json.dumps({"ok": True, "path": str(path), "count": len(entries), "bases": entries}, ensure_ascii=False, indent=2))
    else:
        for e in entries:
            print(f"{e['name']}\t{e['connect']}\t{e['folder']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Open a 1C infobase by name/ref from ibases.v8i via 1cv8 CLI; wait via PID/titles (no VLM)."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from credentials import load_credentials, redact_cmd
from list_ibases import ibases_path, parse_ibases
from wait_window import list_1c_pids, wait_for


def _version_dirs() -> list[Path]:
    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "1cv8_x64",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "1cv8",
        Path(r"C:\Program Files\1cv8"),
        Path(r"C:\Program Files (x86)\1cv8"),
    ]
    found: list[Path] = []
    for root in roots:
        if not root or not root.is_dir():
            continue
        for p in root.iterdir():
            if p.is_dir() and re.match(r"8\.\d+\.\d+\.\d+", p.name):
                found.append(p)
    # unique by version name, prefer Local\Programs over Program Files
    by_ver: dict[str, Path] = {}
    for p in found:
        prev = by_ver.get(p.name)
        if prev is None:
            by_ver[p.name] = p
            continue
        prev_local = "\\Programs\\1cv8" in str(prev)
        cur_local = "\\Programs\\1cv8" in str(p)
        if cur_local and not prev_local:
            by_ver[p.name] = p
    return sorted(
        by_ver.values(),
        key=lambda p: [int(x) for x in p.name.split(".")],
        reverse=True,
    )


def find_platform(version_hint: str = "") -> Path:
    versions = _version_dirs()
    if not versions:
        raise RuntimeError("1cv8 not found under Program Files")
    hint = (version_hint or "").strip()
    if hint and hint.count(".") >= 3:
        for v in versions:
            if v.name == hint:
                exe = v / "bin" / "1cv8.exe"
                if exe.is_file():
                    return exe
    if hint:
        for v in versions:
            if v.name.startswith(hint):
                exe = v / "bin" / "1cv8.exe"
                if exe.is_file():
                    return exe
    for v in versions:
        exe = v / "bin" / "1cv8.exe"
        if exe.is_file():
            return exe
    raise RuntimeError("1cv8.exe not found")


def _ref_token(connect: str) -> str:
    m = re.search(r'Ref="([^"]+)"', connect, re.I)
    if m:
        return m.group(1).lower()
    m = re.search(r'ws="([^"]+)"', connect, re.I)
    if m:
        return m.group(1).rstrip("/").split("/")[-1].lower()
    m = re.search(r'File="([^"]+)"', connect, re.I)
    if m:
        return Path(m.group(1)).name.lower()
    return ""


def pick_base(entries: list[dict], name: str, ref: str) -> dict:
    name_l = (name or "").lower()
    ref_l = (ref or "").lower()
    hits = []
    for e in entries:
        if not e.get("connect"):
            continue
        ok_name = (not name_l) or (name_l in e["name"].lower())
        token = _ref_token(e["connect"])
        if ref_l:
            ok_ref = token == ref_l or ref_l in e["connect"].lower()
        else:
            ok_ref = True
        if ok_name and ok_ref:
            hits.append(e)
    if not hits:
        raise RuntimeError(f"Base not found: name={name!r} ref={ref!r}")
    if ref_l:
        exact_ref = [h for h in hits if _ref_token(h["connect"]) == ref_l]
        if exact_ref:
            hits = exact_ref
    if name_l:
        exact_name = [h for h in hits if h["name"].lower() == name_l]
        if len(exact_name) == 1:
            return exact_name[0]
    # Prefer thick/server over published web client when ref collides
    if len(hits) > 1:
        ranked = sorted(
            hits,
            key=lambda h: {"server": 0, "file": 1, "ws": 2, "other": 3}[
                connect_kind(h["connect"])
            ],
        )
        if connect_kind(ranked[0]["connect"]) != connect_kind(ranked[1]["connect"]):
            return ranked[0]
        names = ", ".join(
            f"{h['name']} [{connect_kind(h['connect'])}:{_ref_token(h['connect'])}]"
            for h in hits[:8]
        )
        raise RuntimeError(f"Ambiguous ({len(hits)}): {names}. Pass fuller --name.")
    return hits[0]


def connect_kind(connect: str) -> str:
    c = connect.lower()
    if "srvr=" in c:
        return "server"
    if "file=" in c:
        return "file"
    if "ws=" in c:
        return "ws"
    return "other"


# DESIGNER only on copies / local / non-prod stands (fail closed).
_DESIGNER_DENY_MARKERS = (
    "прод",
    " prod",
    "prod ",
    "_prod",
    "prod_",
    "production",
    "рабочая база",
)
_DESIGNER_ALLOW_MARKERS = (
    "copy",
    "копи",
    "_stage",
    "stage",
    "стейдж",
    "_test",
    "test",
    "тест",
    "_dev",
    "dev_",
    "локал",
    "local",
)


def assert_designer_allowed(base: dict) -> None:
    """Raise if DESIGNER must not open this infobase (prod / unknown server)."""
    connect = base.get("connect") or ""
    name = base.get("name") or ""
    token = _ref_token(connect)
    blob = f"{name}\n{connect}\n{token}".lower()
    kind = connect_kind(connect)

    for marker in _DESIGNER_DENY_MARKERS:
        if marker.strip() in blob:
            # Allow explicit "копия прод" / "*_prod_copy" style only if copy marker wins
            if any(a in blob for a in ("copy", "копи")):
                break
            raise RuntimeError(
                "DESIGNER forbidden on production/working IB: "
                f"name={name!r} ref={token!r}. "
                "Only copies and local file bases are allowed."
            )

    if kind == "file":
        return

    if any(m in blob for m in _DESIGNER_ALLOW_MARKERS):
        return

    raise RuntimeError(
        "DESIGNER forbidden (fail closed): IB is not a recognized copy/local/stage. "
        f"name={name!r} ref={token!r} connect={connect!r}. "
        "Allowed: File= local, or name/Ref with copy/копия/stage/test/dev."
    )


def connect_to_args(connect: str) -> list[str]:
    """Map Connect= line to 1cv8 args (list form, no nested quoting)."""
    m = re.search(r'Srvr="([^"]+)";Ref="([^"]+)"', connect)
    if m:
        return [f"/S{m.group(1)}\\{m.group(2)}"]
    m = re.search(r'File="([^"]+)"', connect)
    if m:
        return [f"/F{m.group(1)}"]
    m = re.search(r'ws="([^"]+)"', connect, re.I)
    if m:
        return [f"/WS{m.group(1)}"]
    raise RuntimeError(f"Unsupported Connect=: {connect}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Launch 1C base without UI search")
    ap.add_argument("--name", default="", help="Substring of base display name (ibases.v8i)")
    ap.add_argument("--ref", default="", help="Substring of Connect (e.g. erp_cfg_copy)")
    ap.add_argument(
        "--app",
        choices=("enterprise", "designer", "thin"),
        default="thin",
        help=(
            "thin=1cv8c ENTERPRISE (default, required for UI work); "
            "designer=1cv8 DESIGNER; "
            "enterprise=legacy alias, remapped to thin (thick ENTERPRISE forbidden)"
        ),
    )
    ap.add_argument(
        "--no-wa",
        action="store_true",
        help="Pass /WA- (disable OS auth; show 1C login). Default is /WA+",
    )
    ap.add_argument("--user", default="", help="1C user name for /N (implies /WA-)")
    ap.add_argument("--password", default="", help="1C password for /P (optional with --user)")
    ap.add_argument(
        "--as-me",
        action="store_true",
        help="Use saved local credentials (~/.cursor/secrets/1c-ib.json) with /WA- /N /P",
    )
    ap.add_argument("--yes", action="store_true", help="Actually launch (default dry-run)")
    ap.add_argument("--list-erp", action="store_true", help="List ERP-related bases and exit")
    ap.add_argument(
        "--wait",
        action="store_true",
        default=True,
        help="After launch, wait for window via PID/titles (default with --yes)",
    )
    ap.add_argument("--no-wait", action="store_true", help="Do not wait for window")
    ap.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Wait timeout seconds (ERP cold start can be minutes)",
    )
    ap.add_argument("--poll", type=float, default=1.0, help="Wait poll seconds")
    ap.add_argument(
        "--expect-title",
        default="",
        help="Title substring for ready (default: base name)",
    )
    args = ap.parse_args()

    path = ibases_path()
    entries = parse_ibases(path)

    if args.list_erp:
        erp = [
            e
            for e in entries
            if e.get("connect")
            and ("erp" in e["connect"].lower() or "ерп" in e["name"].lower())
        ]
        print(json.dumps({"ok": True, "bases": erp}, ensure_ascii=False, indent=2))
        return 0

    if not args.name and not args.ref:
        print(json.dumps({"ok": False, "error": "pass --name and/or --ref (or --list-erp)"}, ensure_ascii=False))
        return 2

    try:
        base = pick_base(entries, args.name, args.ref)
        if args.app == "designer":
            assert_designer_allowed(base)
        connect_args = connect_to_args(base["connect"])
        platform = find_platform(base.get("version") or "")
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    requested_app = args.app
    remapped_from_enterprise = False
    if args.app == "enterprise":
        args.app = "thin"
        remapped_from_enterprise = True

    if args.app == "thin":
        exe = platform.with_name("1cv8c.exe")
        mode = "ENTERPRISE"
    elif args.app == "designer":
        exe = platform
        mode = "DESIGNER"
    else:
        exe = platform.with_name("1cv8c.exe")
        mode = "ENTERPRISE"

    user = args.user
    password = args.password
    cred_source = None
    if args.as_me:
        creds = load_credentials()
        if not creds:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "No saved credentials. Create %USERPROFILE%\\.cursor\\secrets\\1c-ib.json",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        if not user:
            user = creds["user"]
        if not password:
            password = creds.get("password") or ""
        cred_source = creds.get("source")

    cmd = [str(exe), mode, *connect_args]
    # 1C default without /WA is /WA+. To pick another IB user must pass /WA- explicitly.
    use_os_auth = not args.no_wa and not user and not args.as_me
    if use_os_auth:
        cmd.append("/WA+")
    else:
        cmd.append("/WA-")
    if user:
        cmd.append(f"/N{user}")
    if password:
        cmd.append(f"/P{password}")

    safe_cmd = redact_cmd(cmd)
    plan = {
        "ok": True,
        "dry_run": not args.yes,
        "base": base,
        "exe": str(exe),
        "app": args.app,
        "app_requested": requested_app,
        "user": user or None,
        "cred_source": cred_source,
        "cmd": safe_cmd,
        "cmd_line": subprocess.list2cmdline(safe_cmd),
        "auth": "os" if use_os_auth else "1c_login",
    }
    if remapped_from_enterprise:
        plan["thick_forbidden"] = True
        plan["note"] = (
            "--app enterprise remapped to thin (1cv8c). "
            "Thick 1cv8 ENTERPRISE is forbidden; use --app thin."
        )

    if not args.yes:
        plan["note"] = "Dry-run. Re-run with --yes to launch."
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    before_pids = set(list_1c_pids())
    creationflags = 0
    if sys.platform == "win32":
        # Keep PID trackable; do not DETACH (harder to verify).
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    proc = subprocess.Popen(
        cmd,
        cwd=str(exe.parent),
        env=os.environ.copy(),
        creationflags=creationflags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    plan["launched"] = True
    plan["pid"] = proc.pid

    do_wait = args.wait and not args.no_wait
    if not do_wait:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    # Give starter a moment to spawn/replace process
    time.sleep(1.0)
    after_pids = set(list_1c_pids())
    new_pids = sorted(after_pids - before_pids)
    track = [proc.pid] + [p for p in new_pids if p != proc.pid]
    if not track:
        track = sorted(after_pids) or [proc.pid]

    expect = args.expect_title or base.get("name") or ""
    wait_result = wait_for(
        pids=track,
        timeout_sec=args.timeout,
        poll_sec=args.poll,
        expect_title=expect,
        # Do not stop on generic "window": keep polling through splash/loading.
        ready_phases={"ready", "auth", "error"},
    )
    plan["wait"] = wait_result
    plan["tracked_pids"] = track
    plan["ok"] = bool(wait_result.get("ok"))
    if wait_result.get("phase") == "auth":
        plan["note"] = "Window needs authentication (handle via UIA/credentials, not VLM)."
    elif wait_result.get("phase") == "error":
        plan["note"] = "Error window detected in titles; inspect wait.windows."
    elif wait_result.get("phase") == "loading":
        plan["note"] = "Still loading when stopped."
    elif wait_result.get("phase") == "timeout":
        plan["note"] = "Timeout waiting for window; see history_tail."

    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if wait_result.get("phase") in ("ready", "window", "auth"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Act on a found UI element: Invoke preferred, else physical click. Dry-run default."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from find import Hit, find_ocr, find_uia, find_window, _match_text  # noqa: E402


def resolve_hit(
    window_ctrl: Any,
    name: str,
    role: str,
    contains: bool,
    ocr: bool,
    index: int,
) -> Hit:
    hits = find_uia(
        window_ctrl,
        name=name,
        role=role,
        contains=contains,
        limit=max(index + 1, 5),
    )
    if not hits and ocr:
        hits = find_ocr(
            window_ctrl,
            name=name,
            contains=contains or True,
            limit=max(index + 1, 5),
        )
    if not hits:
        raise RuntimeError(f"Element not found: name={name!r} role={role!r}")
    if index >= len(hits):
        raise RuntimeError(f"Hit index {index} out of range (found {len(hits)})")
    return hits[index]


def find_uia_control(window_ctrl: Any, hit: Hit, contains: bool) -> Any:
    from find import iter_uia, control_role_name

    for ctrl in iter_uia(window_ctrl):
        try:
            cname = ctrl.Name or ""
        except Exception:
            continue
        if not _match_text(cname, hit.name, contains):
            continue
        ctype = control_role_name(ctrl)
        if hit.role and ctype and hit.role != ctype and hit.source == "uia":
            if hit.role.lower() not in ctype.lower():
                continue
        try:
            rect = ctrl.BoundingRectangle
            left, top, right, bottom = (
                int(rect.left),
                int(rect.top),
                int(rect.right),
                int(rect.bottom),
            )
        except Exception:
            continue
        if (
            abs(left - hit.left) <= 2
            and abs(top - hit.top) <= 2
            and abs(right - hit.right) <= 2
            and abs(bottom - hit.bottom) <= 2
        ):
            return ctrl
        # name+role match without strict bounds as fallback
        if hit.source == "uia" and ctype == hit.role:
            return ctrl
    return None


def try_invoke(ctrl: Any) -> bool:
    try:
        pattern = ctrl.GetInvokePattern()
        if pattern:
            pattern.Invoke()
            return True
    except Exception:
        pass
    try:
        pattern = ctrl.GetPattern(10000)
        if pattern:
            pattern.Invoke()
            return True
    except Exception:
        pass
    return False


def try_set_value(ctrl: Any, text: str) -> bool:
    try:
        pattern = ctrl.GetValuePattern()
        if pattern:
            pattern.SetValue(text)
            return True
    except Exception:
        pass
    return False


def physical_click(x: int, y: int) -> None:
    try:
        from coords import ensure_dpi_aware, physical_click as _click

        ensure_dpi_aware()
        _click(x, y)
        return
    except Exception:
        pass
    import ctypes

    user32 = ctypes.windll.user32
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    # MOUSEEVENTF_LEFTDOWN = 0x0002, LEFTUP = 0x0004
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.02)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


def physical_type(text: str) -> None:
    """Type text without SendKeys modifiers (they often print literally in 1C).

    Prefer shared helper from 1c-desktop-session (ValuePattern/paste/unicode).
    Layout-aware: VkKeyScan alone is wrong under RU/EN mismatch.
    """
    import sys
    from pathlib import Path

    helper = (
        Path(__file__).resolve().parents[2]
        / "1c-desktop-session"
        / "scripts"
    )
    if helper.is_dir() and str(helper) not in sys.path:
        sys.path.insert(0, str(helper))
    try:
        from text_input import type_text  # type: ignore

        r = type_text(text, method="auto", clear_first=False)
        if r.get("ok"):
            return
    except Exception:
        pass

    # Last resort: UNICODE SendInput with correct Win64 INPUT size
    import ctypes
    from ctypes import wintypes

    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = (
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_ulonglong),
        )

    class INPUT_UNION(ctypes.Union):
        _fields_ = (("ki", KEYBDINPUT),)

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = (
            ("type", wintypes.DWORD),
            ("_padding", wintypes.DWORD),
            ("u", INPUT_UNION),
        )

    for ch in text:
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            inp = INPUT(type=1)
            inp.ki = KEYBDINPUT(0, ord(ch), flags, 0, 0)
            ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.01)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Invoke/click/type UI element (dry-run by default)")
    p.add_argument("--window", "-w", required=True)
    p.add_argument("--name", "-n", required=True)
    p.add_argument("--role", "-r", default="any")
    p.add_argument("--contains", action="store_true")
    p.add_argument("--ocr", action="store_true")
    p.add_argument("--index", type=int, default=0, help="Which hit to use (0-based)")
    p.add_argument(
        "--action",
        choices=("click", "invoke", "type", "focus"),
        default="click",
        help="click=Invoke then physical; invoke=Invoke only; type=set/paste text; focus=set focus",
    )
    p.add_argument("--text", default="", help="Text for --action type")
    p.add_argument(
        "--yes",
        action="store_true",
        help="Actually perform the action (default is dry-run)",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        import uiautomation as auto
    except ImportError:
        print("Missing uiautomation. pip install -r requirements.txt", file=sys.stderr)
        return 2

    try:
        window_ctrl = find_window(auto, args.window)
        hit = resolve_hit(
            window_ctrl,
            name=args.name,
            role=args.role,
            contains=args.contains,
            ocr=args.ocr,
            index=args.index,
        )
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1

    plan = {
        "ok": True,
        "dry_run": not args.yes,
        "window": window_ctrl.Name,
        "action": args.action,
        "hit": hit.to_dict(),
        "performed": None,
        "method": None,
    }

    if not args.yes:
        plan["note"] = "Dry-run. Re-run with --yes to execute."
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    ctrl = find_uia_control(window_ctrl, hit, contains=args.contains) if hit.source == "uia" else None
    method = None
    performed = False

    if args.action in ("invoke", "click") and ctrl is not None:
        if try_invoke(ctrl):
            method = "uia_invoke"
            performed = True

    if args.action == "type":
        if not args.text:
            plan["ok"] = False
            plan["error"] = "--text required for type"
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 2
        if ctrl is not None:
            try:
                ctrl.SetFocus()
            except Exception:
                pass
            if try_set_value(ctrl, args.text):
                method = "uia_set_value"
                performed = True
            else:
                physical_click(hit.cx, hit.cy)
                time.sleep(0.1)
                physical_type(args.text)
                method = "click_paste"
                performed = True
        else:
            physical_click(hit.cx, hit.cy)
            time.sleep(0.1)
            physical_type(args.text)
            method = "click_paste"
            performed = True

    elif args.action == "focus":
        if ctrl is not None:
            ctrl.SetFocus()
            method = "uia_focus"
            performed = True
        else:
            physical_click(hit.cx, hit.cy)
            method = "click_focus"
            performed = True

    elif args.action in ("click", "invoke") and not performed:
        if args.action == "invoke" and ctrl is None:
            plan["ok"] = False
            plan["error"] = "Invoke unavailable (no UIA control / no InvokePattern)"
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 1
        physical_click(hit.cx, hit.cy)
        method = "physical_click"
        performed = True

    plan["performed"] = performed
    plan["method"] = method
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0 if performed else 1


if __name__ == "__main__":
    raise SystemExit(main())

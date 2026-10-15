#!/usr/bin/env python3
"""Click/type into a labeled form field. Prefer UIA Edit/CheckBox by Name; OCR coords last."""

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

from coords import (  # noqa: E402
    bring_to_foreground,
    control_from_point,
    ensure_dpi_aware,
    physical_click,
    resolve_field_click_near_label,
)
from find import find_ocr, find_uia, find_window, iter_uia  # noqa: E402


def _desktop_session_text_input():
    helper = _SCRIPTS.parents[1] / "1c-desktop-session" / "scripts"
    if helper.is_dir() and str(helper) not in sys.path:
        sys.path.insert(0, str(helper))
    import text_input as ti  # type: ignore

    return ti


def _norm_label(name: str) -> str:
    return (name or "").strip().rstrip(":").lower()


def find_uia_named(window_ctrl: Any, name: str, role: str) -> Optional[Any]:
    """Exact-ish UIA control by Name + role. Prefer exact Name, then contains."""
    needle = (name or "").strip()
    if not needle:
        return None
    hits = find_uia(window_ctrl, name=needle, role=role, contains=False, limit=8)
    if not hits:
        hits = find_uia(window_ctrl, name=needle, role=role, contains=True, limit=8)
    if not hits and needle.endswith(":"):
        hits = find_uia(window_ctrl, name=needle.rstrip(":"), role=role, contains=True, limit=8)
    if not hits:
        return None
    # Prefer exact Name match among hits
    for h in hits:
        if (h.name or "").strip().lower() == needle.lower():
            return _ctrl_from_hit(window_ctrl, h, role)
    return _ctrl_from_hit(window_ctrl, hits[0], role)


def _ctrl_from_hit(window_ctrl: Any, hit: Any, role: str) -> Optional[Any]:
    role_key = role.lower()
    wanted = {
        "edit": "EditControl",
        "checkbox": "CheckBoxControl",
        "combo": "ComboBoxControl",
    }.get(role_key, "")
    for ctrl in iter_uia(window_ctrl):
        try:
            cname = (ctrl.Name or "").strip()
            ctype = ctrl.ControlTypeName or ""
        except Exception:
            continue
        if cname != (hit.name or "").strip():
            # allow contains when hit came from contains search
            if _norm_label(hit.name) not in _norm_label(cname) and _norm_label(cname) not in _norm_label(
                hit.name
            ):
                continue
        if wanted and ctype != wanted:
            continue
        try:
            rect = ctrl.BoundingRectangle
            if abs(int(rect.left) - hit.left) <= 3 and abs(int(rect.top) - hit.top) <= 3:
                return ctrl
        except Exception:
            return ctrl
    # name+role only
    for ctrl in iter_uia(window_ctrl):
        try:
            cname = (ctrl.Name or "").strip()
            ctype = ctrl.ControlTypeName or ""
        except Exception:
            continue
        if cname == (hit.name or "").strip() and (not wanted or ctype == wanted):
            return ctrl
    return None


def toggle_state(ctrl: Any) -> Optional[int]:
    try:
        pat = ctrl.GetTogglePattern()
        return int(pat.ToggleState) if pat else None
    except Exception:
        return None


def read_value_pattern(ctrl: Any) -> dict[str, Any]:
    """Read Edit value from the specific UIA control (not clipboard / not focus)."""
    out: dict[str, Any] = {"ok": False, "method": "value_pattern", "text": ""}
    if ctrl is None:
        out["error"] = "no control"
        return out
    try:
        pat = ctrl.GetValuePattern()
        if pat is None:
            out["error"] = "ValuePattern unavailable"
            return out
        text = pat.Value
        out["ok"] = True
        out["text"] = text if text is not None else ""
        return out
    except Exception as e:
        out["error"] = str(e)
        return out


def set_value_pattern(ctrl: Any, text: str) -> dict[str, Any]:
    """Write via ValuePattern on the specific control (does not need keyboard focus)."""
    out: dict[str, Any] = {"ok": False, "method": "value_pattern_set"}
    if ctrl is None:
        out["error"] = "no control"
        return out
    try:
        pat = ctrl.GetValuePattern()
        if pat is None:
            out["error"] = "ValuePattern unavailable"
            return out
        if getattr(pat, "IsReadOnly", False):
            out["error"] = "ValuePattern IsReadOnly"
            return out
        pat.SetValue(text)
        out["ok"] = True
        return out
    except Exception as e:
        out["error"] = str(e)
        return out


def _values_match(expected: str, actual: str) -> bool:
    exp = (expected or "").strip().lower().replace("/", "\\")
    got = (actual or "").strip().lower().replace("/", "\\")
    if not exp:
        return got == ""
    return exp == got or exp in got


def focus_proof_after_click(
    window_ctrl: Any,
    edit_ctrl: Any,
    after: dict[str, Any],
    *,
    expected_name: str,
) -> dict[str, Any]:
    """Refuse typing if click/focus left the target 1C Edit (e.g. landed in Cursor)."""
    out: dict[str, Any] = {"ok": True}
    try:
        import ctypes

        fg = int(ctypes.windll.user32.GetForegroundWindow())
        hwnd = int(getattr(window_ctrl, "NativeWindowHandle", 0) or 0)
        out["foreground_hwnd"] = fg
        out["window_hwnd"] = hwnd
        if hwnd and fg and fg != hwnd:
            # child of 1C is ok; foreign top-level is not
            cur = fg
            belongs = False
            for _ in range(8):
                if cur == hwnd:
                    belongs = True
                    break
                parent = int(ctypes.windll.user32.GetParent(cur) or 0)
                if not parent or parent == cur:
                    break
                cur = parent
            if not belongs:
                out["ok"] = False
                out["error"] = (
                    f"foreground HWND {fg} is not the 1C window {hwnd} "
                    "(click/type would hit Cursor or another app)"
                )
                return out
    except Exception as e:
        out["fg_check_error"] = str(e)

    ctype = (after.get("control_type") or "").lower()
    aname = (after.get("name") or "").strip()
    bounds = after.get("bounds") or {}
    if "window" in ctype:
        out["ok"] = False
        out["error"] = (
            f"click landed on WindowControl name={aname!r} - "
            "not the Edit field (likely missed 1C / hit another app)"
        )
        return out
    if bounds:
        w = int(bounds.get("right", 0)) - int(bounds.get("left", 0))
        h = int(bounds.get("bottom", 0)) - int(bounds.get("top", 0))
        if "pane" in ctype and (w > 500 or h > 100):
            out["ok"] = False
            out["error"] = f"click landed on large Pane ({w}x{h}) - refusing type"
            return out

    # Prefer proof via ValuePattern on the resolved Edit (identity of control)
    if edit_ctrl is not None:
        try:
            ename = (edit_ctrl.Name or "").strip()
            out["edit_name"] = ename
            if expected_name and _norm_label(ename) != _norm_label(expected_name):
                # still ok if Taxi names differ slightly; ValuePattern read is the real proof
                out["name_note"] = f"edit Name={ename!r} vs label={expected_name!r}"
        except Exception:
            pass
        # after click, ControlFromPoint should be Edit or at least not foreign Window
        if "edit" not in ctype and "document" not in ctype:
            # soft warn only if ValuePattern still readable on edit_ctrl
            vp = read_value_pattern(edit_ctrl)
            out["value_before_focus_check"] = {k: v for k, v in vp.items()}
            if not vp.get("ok"):
                out["ok"] = False
                out["error"] = (
                    f"after click control_type={ctype!r} and ValuePattern "
                    f"on target Edit failed: {vp.get('error')}"
                )
                return out
    return out


def ensure_checkbox(
    window_ctrl: Any,
    name: str,
    *,
    want_on: bool,
    yes: bool,
) -> dict[str, Any]:
    """Turn CheckBox on/off by UIA Name. Physical click first; TogglePattern fallback."""
    ctrl = find_uia_named(window_ctrl, name, "checkbox")
    out: dict[str, Any] = {"name": name, "want_on": want_on, "found": ctrl is not None}
    if ctrl is None:
        out["ok"] = False
        out["error"] = f"CheckBox not found by UIA Name: {name!r}"
        return out
    before = toggle_state(ctrl)
    out["toggle_before"] = before
    need = 1 if want_on else 0
    if before == need:
        out["ok"] = True
        out["toggle_after"] = before
        out["changed"] = False
        return out
    if not yes:
        out["ok"] = True
        out["dry_run"] = True
        out["would_change"] = True
        return out
    try:
        rect = ctrl.BoundingRectangle
        cx = (int(rect.left) + int(rect.right)) // 2
        cy = (int(rect.top) + int(rect.bottom)) // 2
        physical_click(cx, cy)
        time.sleep(0.35)
    except Exception as e:
        out["click_error"] = str(e)
    after = toggle_state(ctrl)
    if after != need:
        try:
            ctrl.GetTogglePattern().Toggle()
            time.sleep(0.35)
            after = toggle_state(ctrl)
            out["used_toggle_pattern"] = True
        except Exception as e:
            out["toggle_error"] = str(e)
    out["toggle_after"] = after
    out["ok"] = after == need
    out["changed"] = True
    if not out["ok"]:
        out["error"] = f"checkbox still {after}, want {need}"
    return out


def resolve_uia_edit(window_ctrl: Any, label: str) -> dict[str, Any]:
    """Preferred: EditControl whose Name equals the form label (Taxi often names Edit = label)."""
    ctrl = find_uia_named(window_ctrl, label, "edit")
    if ctrl is None and not label.endswith(":"):
        ctrl = find_uia_named(window_ctrl, label + ":", "edit")
    if ctrl is None:
        return {"ok": False, "error": f"EditControl not found for label {label!r}", "method": "uia_edit"}
    try:
        enabled = bool(ctrl.IsEnabled)
    except Exception:
        enabled = True
    try:
        rect = ctrl.BoundingRectangle
        cx = (int(rect.left) + int(rect.right)) // 2
        cy = (int(rect.top) + int(rect.bottom)) // 2
        bounds = {
            "left": int(rect.left),
            "top": int(rect.top),
            "right": int(rect.right),
            "bottom": int(rect.bottom),
        }
    except Exception as e:
        return {"ok": False, "error": f"no bounds: {e}", "method": "uia_edit", "enabled": enabled}
    return {
        "ok": True,
        "cx": cx,
        "cy": cy,
        "method": "uia_edit_by_name",
        "enabled": enabled,
        "control": {
            "name": getattr(ctrl, "Name", "") or "",
            "control_type": "EditControl",
            "bounds": bounds,
        },
        "verify_ok": True,
        "_ctrl": ctrl,
    }


def find_label(window_ctrl: Any, name: str, *, ocr: bool) -> Any:
    hits = find_uia(window_ctrl, name=name, role="text", contains=True, limit=8)
    if not hits:
        hits = find_uia(window_ctrl, name=name, role="any", contains=True, limit=8)
    if (not hits) and ocr:
        hits = find_ocr(window_ctrl, name=name, contains=True, limit=8)
    if not hits:
        raise RuntimeError(f"label not found: {name!r}")
    for h in hits:
        n = (h.name or "").strip()
        if n.endswith(":") or name.lower() in n.lower():
            return h
    return hits[0]


def main(argv: list[str] | None = None) -> int:
    ensure_dpi_aware()
    ap = argparse.ArgumentParser(
        description="Precise labeled-field click/type (UIA Edit/CheckBox first, OCR sweep last)"
    )
    ap.add_argument("--window", "-w", required=True)
    ap.add_argument("--label", "-l", default="", help="Label / Edit Name (e.g. Пользователь:)")
    ap.add_argument(
        "--ensure-checkbox",
        default="",
        help="UIA CheckBox Name to turn ON before typing (e.g. Аутентификация операционной системы)",
    )
    ap.add_argument(
        "--checkbox",
        default="",
        help="Only set this CheckBox (no text field). Use with --ensure-on/--ensure-off",
    )
    ap.add_argument("--ensure-on", action="store_true", help="Checkbox must be On (default for --ensure-checkbox)")
    ap.add_argument("--ensure-off", action="store_true", help="Checkbox must be Off")
    ap.add_argument("--ocr", action="store_true", default=True)
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--type", dest="text", default="", help="Text to paste after focus")
    ap.add_argument(
        "--type-method",
        choices=("auto", "paste", "value"),
        default="paste",
        help=(
            "How to write --type. paste (default): Ctrl+V after focus_proof - "
            "dirties 1C form so Записать persists. value: ValuePattern.SetValue only "
            "(UIA shows text but Taxi often does NOT dirty; Записать keeps old). "
            "auto: paste then verify ValuePattern."
        ),
    )
    ap.add_argument(
        "--expect-clip",
        default="",
        help="Substring that read-clip BEFORE type must match (focus proof)",
    )
    ap.add_argument(
        "--reject-clip",
        default="",
        help="If read-clip before type contains this - abort (wrong field)",
    )
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)

    import uiautomation as auto

    use_ocr = args.ocr and not args.no_ocr
    want_on = True
    if args.ensure_off:
        want_on = False
    elif args.ensure_on or args.ensure_checkbox:
        want_on = True

    try:
        window = find_window(auto, args.window)
        bring_to_foreground(window)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 1

    plan: dict[str, Any] = {
        "ok": True,
        "dry_run": not args.yes,
        "window": window.Name,
    }

    cb_name = args.checkbox or args.ensure_checkbox
    if cb_name:
        cb = ensure_checkbox(window, cb_name, want_on=want_on, yes=args.yes)
        plan["checkbox"] = {k: v for k, v in cb.items() if k != "_ctrl"}
        if not cb.get("ok"):
            plan["ok"] = False
            plan["error"] = cb.get("error")
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 1
        if args.checkbox and not args.label:
            # checkbox-only mode
            if not args.yes:
                plan["note"] = "Dry-run. Re-run with --yes to toggle checkbox."
            else:
                plan["performed"] = True
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        # after enabling dependent checkbox, small settle
        if args.yes and cb.get("changed"):
            time.sleep(0.4)

    if not args.label:
        plan["ok"] = False
        plan["error"] = "need --label for field type, or --checkbox alone"
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 1

    # 1) Preferred: UIA EditControl by Name == label
    target = resolve_uia_edit(window, args.label)
    label_hit = None
    if not target.get("ok"):
        # 2) Fallback: OCR/UIA label + ControlFromPoint sweep (hardened in coords)
        try:
            label_hit = find_label(window, args.label, ocr=use_ocr)
            plan["label"] = label_hit.to_dict()
            target = resolve_field_click_near_label(window, label_hit)
        except Exception as e:
            plan["ok"] = False
            plan["error"] = str(e)
            plan["uia_edit"] = {k: v for k, v in target.items() if k != "_ctrl"}
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 1
    else:
        plan["label"] = {"name": args.label, "source": "uia_edit_name"}

    plan["target"] = {k: v for k, v in target.items() if k != "_ctrl"}
    if not target.get("ok"):
        plan["ok"] = False
        plan["error"] = target.get("error") or "no field target"
        plan["hint"] = (
            "If field depends on a checkbox, pass --ensure-checkbox "
            '"Аутентификация операционной системы"'
        )
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 1

    if target.get("enabled") is False:
        plan["ok"] = False
        plan["error"] = "EditControl found but IsEnabled=false (enable checkbox first)"
        plan["hint"] = 'Use --ensure-checkbox "..." --yes'
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 1

    if not target.get("verify_ok", True):
        plan["warning"] = "verify ControlFromPoint at click center looks weak"

    if not args.yes:
        plan["note"] = "Dry-run. Re-run with --yes to click/type."
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    cx, cy = int(target["cx"]), int(target["cy"])
    edit_ctrl = target.get("_ctrl")

    # Prefer SetFocus on the resolved Edit so clicks are not required for ValuePattern
    if edit_ctrl is not None:
        try:
            edit_ctrl.SetFocus()
            time.sleep(0.1)
            plan["set_focus"] = True
        except Exception as e:
            plan["set_focus"] = False
            plan["set_focus_error"] = str(e)

    bring_to_foreground(window)
    physical_click(cx, cy)
    time.sleep(0.2)
    after = control_from_point(cx, cy)
    plan["after_click"] = after

    focus = focus_proof_after_click(
        window, edit_ctrl, after, expected_name=args.label
    )
    plan["focus_proof"] = {k: v for k, v in focus.items() if k != "_ctrl"}
    if not focus.get("ok"):
        plan["ok"] = False
        plan["error"] = focus.get("error") or "focus proof failed"
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 1

    # Source of truth: ValuePattern on the SAME Edit control object (not clipboard)
    before_vp = read_value_pattern(edit_ctrl)
    plan["value_before"] = before_vp
    field_text = (before_vp.get("text") or "") if before_vp.get("ok") else ""

    if not before_vp.get("ok"):
        plan["ok"] = False
        plan["error"] = (
            "cannot read ValuePattern of target Edit - refusing type "
            f"(clipboard alone is not proof): {before_vp.get('error')}"
        )
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 1

    if args.reject_clip and args.reject_clip.lower() in field_text.lower():
        plan["ok"] = False
        plan["error"] = f"reject matched wrong field ValuePattern content: {field_text!r}"
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 1
    if args.expect_clip and args.expect_clip.lower() not in field_text.lower():
        if field_text.strip() and args.expect_clip != "":
            plan["ok"] = False
            plan["error"] = f"expect not in field ValuePattern: got={field_text!r}"
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 1

    if args.text:
        # 2026-10-04: ValuePattern.SetValue on Taxi often updates UIA only and does
        # NOT dirty the form - Записать keeps the previous value. Preferred: paste
        # after focus_proof, then verify ValuePattern on the same Edit.
        method = args.type_method
        if method == "auto":
            method = "paste"
        plan["type_method"] = method

        def _paste_into_edit() -> dict[str, Any]:
            ti = _desktop_session_text_input()
            bring_to_foreground(window)
            try:
                edit_ctrl.SetFocus()
            except Exception:
                pass
            physical_click(cx, cy)
            time.sleep(0.1)
            after2 = control_from_point(cx, cy)
            focus2 = focus_proof_after_click(
                window, edit_ctrl, after2, expected_name=args.label
            )
            if not focus2.get("ok"):
                return {
                    "ok": False,
                    "method": "paste",
                    "error": focus2.get("error") or "focus proof before paste failed",
                    "focus_proof_before_paste": focus2,
                }
            ti.chord(ti.VK_CONTROL, ti.VK_A)
            time.sleep(0.05)
            try:
                # Clear selection so paste replaces (not appends)
                ti.key_tap(0x2E)  # VK_DELETE
            except Exception:
                pass
            time.sleep(0.05)
            pasted = ti.type_text(args.text, method="paste")
            return {
                "ok": bool(pasted.get("ok")),
                "method": "paste",
                "paste": pasted,
                "focus_proof_before_paste": focus2,
            }

        if method == "value":
            set_res = set_value_pattern(edit_ctrl, args.text)
            plan["typed"] = set_res
            if not set_res.get("ok"):
                paste_res = _paste_into_edit()
                plan["typed"] = {
                    **paste_res,
                    "method": "paste_fallback",
                    "set_value_error": set_res.get("error"),
                }
                if not paste_res.get("ok"):
                    plan["ok"] = False
                    plan["error"] = paste_res.get("error") or "paste fallback failed"
                    print(json.dumps(plan, ensure_ascii=False, indent=2))
                    return 1
        else:
            paste_res = _paste_into_edit()
            plan["typed"] = paste_res
            if not paste_res.get("ok"):
                plan["ok"] = False
                plan["error"] = paste_res.get("error") or "paste failed"
                print(json.dumps(plan, ensure_ascii=False, indent=2))
                return 1
        time.sleep(0.2)

        after_vp = read_value_pattern(edit_ctrl)
        plan["value_after"] = after_vp
        got = (after_vp.get("text") or "") if after_vp.get("ok") else ""
        if not after_vp.get("ok") or not _values_match(args.text, got):
            plan["ok"] = False
            plan["error"] = (
                "value not in target Edit ValuePattern "
                f"(clipboard alone is not proof): got={got!r} "
                f"expected={args.text!r} read_err={after_vp.get('error')}"
            )
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 1

    plan["performed"] = True
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

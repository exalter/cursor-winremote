#!/usr/bin/env python3
"""Precise screen coords for Windows UI: DPI, PrintWindow capture, field targeting.

Root causes of "click miss" this module addresses:
1) ImageGrab of window bounds captures overlapping topmost windows (Cursor) ->
   OCR hits belong to the wrong app, clicks go "мимо".
2) Clicking OCR label / right+N heuristic while Edit is disabled/elsewhere ->
   focus stays on previous field.
3) DPI-unaware process on scaled displays (future-proof; this machine often 96 DPI).
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from typing import Any, Optional

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

# PrintWindow flags
PW_CLIENTONLY = 0x00000001
PW_RENDERFULLCONTENT = 0x00000002

# DPI
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


def ensure_dpi_aware() -> dict[str, Any]:
    """Best-effort Per-Monitor V2 DPI awareness for this process."""
    out: dict[str, Any] = {"ok": False, "method": None}
    try:
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            ok = user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
            out["ok"] = bool(ok)
            out["method"] = "SetProcessDpiAwarenessContext_V2"
            return out
    except Exception as e:
        out["error"] = str(e)
    try:
        shcore = ctypes.windll.shcore
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        hr = shcore.SetProcessDpiAwareness(2)
        out["ok"] = hr == 0
        out["method"] = "SetProcessDpiAwareness"
        out["hr"] = int(hr)
    except Exception as e:
        out["error"] = str(e)
    return out


def hwnd_of(window_ctrl: Any) -> int:
    for attr in ("NativeWindowHandle", "Handle", "Element"):
        try:
            h = getattr(window_ctrl, attr, None)
            if callable(h):
                h = h()
            if h:
                return int(h)
        except Exception:
            continue
    raise RuntimeError("Cannot get HWND from window control")


def bring_to_foreground(window_ctrl: Any) -> bool:
    """Activate target window so physical clicks hit it (does not maximize)."""
    try:
        hwnd = hwnd_of(window_ctrl)
    except Exception:
        hwnd = 0
    try:
        window_ctrl.SetActive()
    except Exception:
        pass
    try:
        window_ctrl.SetFocus()
    except Exception:
        pass
    if hwnd:
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.05)
        return user32.GetForegroundWindow() == hwnd
    return False


def grab_window_image_printwindow(window_ctrl: Any):
    """Capture window bitmap via PrintWindow (ignores overlapping Cursor/other apps).

    Returns (PIL.Image, screen_box (l,t,r,b)) where OCR local coords map to
    screen as screen_x = l + local_x (same as old ImageGrab path).
    """
    from PIL import Image

    rect = window_ctrl.BoundingRectangle
    left, top, right, bottom = (
        int(rect.left),
        int(rect.top),
        int(rect.right),
        int(rect.bottom),
    )
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise RuntimeError("Invalid window bounds for PrintWindow capture")

    hwnd = hwnd_of(window_ctrl)
    bring_to_foreground(window_ctrl)

    hdc_window = user32.GetWindowDC(hwnd)
    if not hdc_window:
        raise RuntimeError("GetWindowDC failed")
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_window, width, height)
    old = gdi32.SelectObject(hdc_mem, hbmp)

    # Prefer full content (Chrome/DirectX-ish); fallback classic PrintWindow
    ok = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
    if not ok:
        ok = user32.PrintWindow(hwnd, hdc_mem, 0)

    # BITMAP copy to buffer
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = width
    bmi.biHeight = -height  # top-down
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0

    buf_len = width * height * 4
    buf = (ctypes.c_char * buf_len)()
    gdi32.GetDIBits(hdc_mem, hbmp, 0, height, buf, ctypes.byref(bmi), 0)

    gdi32.SelectObject(hdc_mem, old)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_window)

    img = Image.frombuffer("RGB", (width, height), bytes(buf), "raw", "BGRX", 0, 1)
    if not ok:
        # last resort: screen grab (may include overlays) - caller should know
        from PIL import ImageGrab

        img = ImageGrab.grab(bbox=(left, top, right, bottom))
    return img, (left, top, right, bottom)


def control_from_point(x: int, y: int) -> dict[str, Any]:
    """UIA element under screen point (what a click would hit)."""
    import uiautomation as auto

    ctrl = auto.ControlFromPoint(int(x), int(y))
    if ctrl is None:
        return {"ok": False, "x": x, "y": y, "error": "ControlFromPoint None"}
    try:
        rect = ctrl.BoundingRectangle
        bounds = {
            "left": int(rect.left),
            "top": int(rect.top),
            "right": int(rect.right),
            "bottom": int(rect.bottom),
        }
    except Exception:
        bounds = None
    try:
        ctype = ctrl.ControlTypeName
    except Exception:
        ctype = ""
    try:
        name = ctrl.Name or ""
    except Exception:
        name = ""
    try:
        auto_id = ctrl.AutomationId or ""
    except Exception:
        auto_id = ""
    return {
        "ok": True,
        "x": int(x),
        "y": int(y),
        "name": name,
        "control_type": ctype,
        "automation_id": auto_id,
        "bounds": bounds,
    }


def _is_value_target(info: dict[str, Any], label_name: str) -> bool:
    ctype = (info.get("control_type") or "").lower()
    name = (info.get("name") or "").strip()
    if not info.get("ok"):
        return False
    # reject pure text / label under cursor
    if "text" in ctype and "edit" not in ctype:
        return False
    if name and label_name and name.lower() == label_name.lower():
        return False
    if any(k in ctype for k in ("edit", "combo", "document", "custom", "dataitem", "spinner")):
        return True
    # 1C often: Pane/Custom with empty name over the value cell.
    # Reject form-sized panes (whole content area) - they steal focus checks.
    if "pane" in ctype or "custom" in ctype or ctype == "":
        b = info.get("bounds") or {}
        w = int(b.get("right", 0)) - int(b.get("left", 0))
        h = int(b.get("bottom", 0)) - int(b.get("top", 0))
        # field-like aspect only; max size avoids "click Pane = type into Full name"
        if w >= 40 and w <= 480 and 12 <= h <= 80:
            return True
    return False


def resolve_field_click_near_label(
    window_ctrl: Any,
    label_hit: Any,
    *,
    max_dx: int = 360,
    step: int = 16,
) -> dict[str, Any]:
    """Find a screen click point for the value field to the RIGHT of a label hit.

    Sweeps ElementFromPoint; prefers Edit/Combo/Custom field-sized controls.
    Does NOT guess a single right+N offset.
    """
    bring_to_foreground(window_ctrl)
    label_name = getattr(label_hit, "name", "") or ""
    y = int(getattr(label_hit, "cy", 0) or ((label_hit.top + label_hit.bottom) // 2))
    x0 = int(getattr(label_hit, "right", 0)) + 8
    probes: list[dict[str, Any]] = []
    best: Optional[dict[str, Any]] = None

    for dx in range(0, max_dx + 1, step):
        x = x0 + dx
        info = control_from_point(x, y)
        info["dx"] = dx
        probes.append(info)
        if _is_value_target(info, label_name):
            b = info.get("bounds") or {}
            # click center of control if bounds known, else probe point
            if b:
                cx = (int(b["left"]) + int(b["right"])) // 2
                cy = (int(b["top"]) + int(b["bottom"])) // 2
            else:
                cx, cy = x, y
            best = {
                "ok": True,
                "cx": cx,
                "cy": cy,
                "probe_x": x,
                "probe_y": y,
                "control": info,
                "method": "control_from_point_sweep",
            }
            break

    if best is None:
        return {
            "ok": False,
            "error": "no value control to the right of label",
            "label": label_name,
            "probes_sample": probes[:8] + probes[-4:],
        }
    # verify click point still resolves to value-like control
    verify = control_from_point(best["cx"], best["cy"])
    best["verify"] = verify
    best["verify_ok"] = _is_value_target(verify, label_name)
    return best


def physical_click(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.02)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import json

    ensure_dpi_aware()
    p = argparse.ArgumentParser(description="Coord diagnostics / field targeting")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("point", help="ControlFromPoint at x,y")
    pp.add_argument("--x", type=int, required=True)
    pp.add_argument("--y", type=int, required=True)

    pd = sub.add_parser("dpi", help="Show DPI awareness")

    args = p.parse_args(argv)
    if args.cmd == "dpi":
        awareness = ctypes.c_int(-1)
        try:
            ctypes.windll.shcore.GetProcessDpiAwareness(0, ctypes.byref(awareness))
        except Exception:
            pass
        print(
            json.dumps(
                {
                    "ensure": ensure_dpi_aware(),
                    "GetProcessDpiAwareness": awareness.value,
                    "GetDpiForSystem": user32.GetDpiForSystem(),
                    "SM_CXSCREEN": user32.GetSystemMetrics(0),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.cmd == "point":
        print(json.dumps(control_from_point(args.x, args.y), ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

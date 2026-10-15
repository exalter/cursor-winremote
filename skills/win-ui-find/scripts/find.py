#!/usr/bin/env python3
"""Find a UI element on Windows: UIA first, OCR fallback. No VLM."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Optional


ROLE_MAP = {
    "button": "ButtonControl",
    "edit": "EditControl",
    "text": "TextControl",
    "menu": "MenuItemControl",
    "menuitem": "MenuItemControl",
    "checkbox": "CheckBoxControl",
    "radio": "RadioButtonControl",
    "list": "ListControl",
    "listitem": "ListItemControl",
    "tree": "TreeControl",
    "treeitem": "TreeItemControl",
    "tab": "TabItemControl",
    "tabitem": "TabItemControl",
    "combo": "ComboBoxControl",
    "combobox": "ComboBoxControl",
    "link": "HyperlinkControl",
    "hyperlink": "HyperlinkControl",
    "window": "WindowControl",
    "pane": "PaneControl",
    "document": "DocumentControl",
    "any": None,
}


@dataclass
class Hit:
    source: str  # uia | ocr
    name: str
    role: str
    left: int
    top: int
    right: int
    bottom: int
    confidence: float
    invoke: bool = False
    automation_id: str = ""
    class_name: str = ""

    @property
    def cx(self) -> int:
        return (self.left + self.right) // 2

    @property
    def cy(self) -> int:
        return (self.top + self.bottom) // 2

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["cx"] = self.cx
        d["cy"] = self.cy
        return d


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _match_text(candidate: str, needle: str, contains: bool) -> bool:
    a = _norm(candidate)
    b = _norm(needle)
    if not b:
        return False
    return b in a if contains else a == b


def find_window(auto: Any, window: str) -> Any:
    root = auto.GetRootControl()
    needle = _norm(window)
    exact = None
    partial = None
    for child in root.GetChildren():
        name = child.Name or ""
        n = _norm(name)
        if not n:
            continue
        if n == needle:
            exact = child
            break
        if needle in n and partial is None:
            partial = child
    found = exact or partial
    if found is None:
        raise RuntimeError(f"Window not found: {window!r}")
    return found


def iter_uia(
    control: Any,
    max_depth: int = 18,
    max_nodes: int = 8000,
) -> Iterable[Any]:
    stack = [(control, 0)]
    seen = 0
    while stack:
        node, depth = stack.pop()
        yield node
        seen += 1
        if seen >= max_nodes or depth >= max_depth:
            continue
        try:
            children = node.GetChildren()
        except Exception:
            continue
        for child in reversed(children):
            stack.append((child, depth + 1))


def control_role_name(control: Any) -> str:
    try:
        return control.ControlTypeName or ""
    except Exception:
        return ""


def has_invoke(control: Any) -> bool:
    try:
        return bool(control.GetPattern(10000))  # InvokePattern
    except Exception:
        try:
            return control.GetInvokePattern() is not None
        except Exception:
            return False


def find_uia(
    window_ctrl: Any,
    name: str,
    role: Optional[str],
    contains: bool,
    limit: int,
) -> list[Hit]:
    hits: list[Hit] = []
    role_key = (role or "any").lower()
    wanted = ROLE_MAP.get(role_key, None)
    if role_key not in ROLE_MAP and role_key != "any":
        # allow raw ControlTypeName substring, e.g. Button
        wanted = role

    for ctrl in iter_uia(window_ctrl):
        try:
            cname = ctrl.Name or ""
        except Exception:
            continue
        if not _match_text(cname, name, contains):
            continue
        ctype = control_role_name(ctrl)
        if wanted:
            if wanted.endswith("Control"):
                if ctype != wanted and not ctype.startswith(wanted.replace("Control", "")):
                    # Compare both "ButtonControl" and short names
                    short = wanted.replace("Control", "")
                    if short.lower() not in ctype.lower():
                        continue
            elif wanted.lower() not in ctype.lower():
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
        if right <= left or bottom <= top:
            continue
        try:
            auto_id = ctrl.AutomationId or ""
        except Exception:
            auto_id = ""
        try:
            class_name = ctrl.ClassName or ""
        except Exception:
            class_name = ""
        hits.append(
            Hit(
                source="uia",
                name=cname,
                role=ctype,
                left=left,
                top=top,
                right=right,
                bottom=bottom,
                confidence=1.0,
                invoke=has_invoke(ctrl),
                automation_id=auto_id,
                class_name=class_name,
            )
        )
        if len(hits) >= limit:
            break
    return hits


def grab_window_image(window_ctrl: Any):
    """Capture window for OCR. Prefer PrintWindow (no Cursor overlay), else ImageGrab."""
    try:
        from coords import ensure_dpi_aware, grab_window_image_printwindow

        ensure_dpi_aware()
        return grab_window_image_printwindow(window_ctrl)
    except Exception:
        from PIL import ImageGrab

        rect = window_ctrl.BoundingRectangle
        box = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
        if box[2] <= box[0] or box[3] <= box[1]:
            raise RuntimeError("Invalid window bounds for OCR capture")
        img = ImageGrab.grab(bbox=box)
        return img, box


def _word_box(word: Any) -> tuple[int, int, int, int] | None:
    """Extract (x, y, x2, y2) from winocr word (dict or object)."""
    if isinstance(word, dict):
        b = word.get("bounding_rect") or word
        x = int(b.get("x", 0))
        y = int(b.get("y", 0))
        w = int(b.get("width", 0))
        h = int(b.get("height", 0))
        return (x, y, x + w, y + h)
    b = getattr(word, "bounding_rect", None) or getattr(word, "rect", None)
    if b is None:
        return None
    x = int(getattr(b, "x", 0))
    y = int(getattr(b, "y", 0))
    w = int(getattr(b, "width", 0))
    h = int(getattr(b, "height", 0))
    return (x, y, x + w, y + h)


def _lines_from_winocr_result(result: Any) -> list[Any]:
    if isinstance(result, dict):
        return list(result.get("lines") or result.get("text_lines") or [])
    lines = getattr(result, "lines", None) or getattr(result, "text_lines", None)
    return list(lines or [])


def _line_text_and_words(line: Any) -> tuple[str, list[Any]]:
    if isinstance(line, dict):
        return str(line.get("text") or ""), list(line.get("words") or [])
    text = getattr(line, "text", None) or str(line)
    words = getattr(line, "words", None) or []
    return str(text), list(words)


def ocr_boxes(img) -> list[tuple[str, tuple[int, int, int, int], float]]:
    """Return list of (text, (l,t,r,b) in image coords, confidence)."""
    # winocr: prefer recognize_pil_sync (dict lines/words). Async recognize_pil
    # on Taxi often yields empty/unusable hits for toolbar buttons.
    try:
        import winocr

        result = None
        sync = getattr(winocr, "recognize_pil_sync", None)
        if callable(sync):
            try:
                result = sync(img, lang="ru")
            except TypeError:
                result = sync(img)
            except Exception:
                result = None
        if result is None:
            import asyncio

            async def _run():
                return await winocr.recognize_pil(img)

            result = asyncio.run(_run())

        out: list[tuple[str, tuple[int, int, int, int], float]] = []
        lines = _lines_from_winocr_result(result)
        if lines:
            for line in lines:
                text, words = _line_text_and_words(line)
                if not text.strip():
                    continue
                xs: list[int] = []
                ys: list[int] = []
                for w in words:
                    wb = _word_box(w)
                    if wb is None:
                        continue
                    xs += [wb[0], wb[2]]
                    ys += [wb[1], wb[3]]
                if xs and ys:
                    box = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
                    out.append((text, box, 0.9))
                else:
                    out.append((text, (0, 0, img.width, img.height), 0.5))
            if out:
                return out
        if isinstance(result, dict):
            text = str(result.get("text") or "")
        else:
            text = getattr(result, "text", "") or str(result)
        if text.strip():
            return [(text, (0, 0, img.width, img.height), 0.4)]
    except Exception:
        pass

    # rapidocr
    try:
        from rapidocr_onnxruntime import RapidOCR

        engine = RapidOCR()
        result, _ = engine(img)
        out = []
        if result:
            for item in result:
                # item: [box_points, text, score]
                box_pts, text, score = item[0], item[1], float(item[2])
                xs = [p[0] for p in box_pts]
                ys = [p[1] for p in box_pts]
                box = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
                out.append((text, box, score))
        return out
    except Exception as exc:
        raise RuntimeError(
            "OCR backend unavailable. Install winocr or rapidocr-onnxruntime. "
            f"Last error: {exc}"
        ) from exc


def find_ocr(
    window_ctrl: Any,
    name: str,
    contains: bool,
    limit: int,
) -> list[Hit]:
    img, origin = grab_window_image(window_ctrl)
    ox, oy = origin[0], origin[1]
    hits: list[Hit] = []
    for text, (l, t, r, b), conf in ocr_boxes(img):
        if not _match_text(text, name, contains):
            continue
        hits.append(
            Hit(
                source="ocr",
                name=text,
                role="OCRText",
                left=ox + l,
                top=oy + t,
                right=ox + r,
                bottom=oy + b,
                confidence=float(conf),
                invoke=False,
            )
        )
        if len(hits) >= limit:
            break
    return hits


def list_actionable(window_ctrl: Any, limit: int = 80) -> list[Hit]:
    actionable = {
        "ButtonControl",
        "EditControl",
        "HyperlinkControl",
        "MenuItemControl",
        "CheckBoxControl",
        "RadioButtonControl",
        "TabItemControl",
        "ListItemControl",
        "TreeItemControl",
        "ComboBoxControl",
    }
    hits: list[Hit] = []
    for ctrl in iter_uia(window_ctrl):
        ctype = control_role_name(ctrl)
        if ctype not in actionable:
            continue
        try:
            cname = (ctrl.Name or "").strip()
        except Exception:
            continue
        if not cname:
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
        if right <= left or bottom <= top:
            continue
        hits.append(
            Hit(
                source="uia",
                name=cname,
                role=ctype,
                left=left,
                top=top,
                right=right,
                bottom=bottom,
                confidence=1.0,
                invoke=has_invoke(ctrl),
            )
        )
        if len(hits) >= limit:
            break
    return hits


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Find UI element via UIA then OCR (no VLM)",
    )
    p.add_argument("--window", "-w", required=True, help="Window title (exact or substring)")
    p.add_argument("--name", "-n", default="", help="Element name / visible text")
    p.add_argument(
        "--role",
        "-r",
        default="any",
        help="button|edit|text|menuitem|checkbox|tab|link|any|...",
    )
    p.add_argument(
        "--contains",
        action="store_true",
        help="Substring match for --name (default: exact, case-insensitive)",
    )
    p.add_argument(
        "--ocr",
        action="store_true",
        help="Enable OCR fallback when UIA finds nothing",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="List actionable UIA elements in the window",
    )
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true", help="Print JSON only")
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
    except Exception as exc:  # noqa: BLE001
        err = {"ok": False, "error": str(exc)}
        print(json.dumps(err, ensure_ascii=False, indent=2))
        return 1

    if args.list:
        hits = list_actionable(window_ctrl, limit=args.limit)
        payload = {
            "ok": True,
            "window": window_ctrl.Name,
            "count": len(hits),
            "hits": [h.to_dict() for h in hits],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if hits else 1

    if not args.name:
        print(json.dumps({"ok": False, "error": "--name required unless --list"}, ensure_ascii=False))
        return 2

    hits = find_uia(
        window_ctrl,
        name=args.name,
        role=args.role,
        contains=args.contains,
        limit=args.limit,
    )
    if not hits and args.ocr:
        try:
            hits = find_ocr(
                window_ctrl,
                name=args.name,
                contains=args.contains or True,
                limit=args.limit,
            )
        except Exception as exc:  # noqa: BLE001
            payload = {
                "ok": False,
                "window": window_ctrl.Name,
                "error": f"UIA empty; OCR failed: {exc}",
                "hits": [],
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 1

    payload = {
        "ok": bool(hits),
        "window": window_ctrl.Name,
        "query": {
            "name": args.name,
            "role": args.role,
            "contains": args.contains,
            "ocr": args.ocr,
        },
        "count": len(hits),
        "hits": [h.to_dict() for h in hits],
        "best": hits[0].to_dict() if hits else None,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if hits else 1


if __name__ == "__main__":
    raise SystemExit(main())

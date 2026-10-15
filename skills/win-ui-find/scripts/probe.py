#!/usr/bin/env python3
"""Check Windows UI backends: UIA, OCR, DPI."""

from __future__ import annotations

import json
import sys


def check_uia() -> dict:
    try:
        import uiautomation as auto  # noqa: F401

        return {"ok": True, "package": "uiautomation"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def check_ocr() -> dict:
    backends = []
    try:
        import winocr  # noqa: F401

        backends.append("winocr")
    except Exception:
        pass
    try:
        import rapidocr_onnxruntime  # noqa: F401

        backends.append("rapidocr")
    except Exception:
        pass
    return {"ok": bool(backends), "backends": backends}


def check_pillow() -> dict:
    try:
        from PIL import ImageGrab  # noqa: F401

        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def list_top_windows(limit: int = 20) -> list[dict]:
    try:
        import uiautomation as auto
    except Exception:
        return []

    root = auto.GetRootControl()
    items: list[dict] = []
    for child in root.GetChildren():
        try:
            name = child.Name or ""
            if not name.strip():
                continue
            rect = child.BoundingRectangle
            items.append(
                {
                    "name": name,
                    "class": getattr(child, "ClassName", "") or "",
                    "left": int(rect.left),
                    "top": int(rect.top),
                    "right": int(rect.right),
                    "bottom": int(rect.bottom),
                }
            )
        except Exception:
            continue
        if len(items) >= limit:
            break
    return items


def main() -> int:
    report = {
        "platform": sys.platform,
        "uia": check_uia(),
        "pillow": check_pillow(),
        "ocr": check_ocr(),
        "top_windows": list_top_windows(),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["uia"]["ok"]:
        print(
            "Install: pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

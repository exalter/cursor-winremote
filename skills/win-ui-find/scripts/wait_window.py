#!/usr/bin/env python3
"""Wait for a Windows UI window without VLM: process + Win32 titles + UIA."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from ctypes import wintypes
import ctypes
from dataclasses import asdict, dataclass
from typing import Any, Optional


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


@dataclass
class WinInfo:
    hwnd: int
    pid: int
    title: str
    visible: bool
    class_name: str = ""
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    exit_code = wintypes.DWORD()
    ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
    kernel32.CloseHandle(handle)
    if not ok:
        return False
    # STILL_ACTIVE = 259
    return exit_code.value == 259


def process_hung(pid: int, hwnd: int = 0) -> bool:
    """True if main window is hung (IsHungAppWindow). No VLM."""
    if hwnd:
        try:
            return bool(user32.IsHungAppWindow(wintypes.HWND(hwnd)))
        except Exception:
            return False
    wins = [w for w in list_windows_for_pids({pid}) if w.visible and w.title.strip()]
    if not wins:
        return False
    try:
        return bool(user32.IsHungAppWindow(wintypes.HWND(wins[0].hwnd)))
    except Exception:
        return False


def list_windows_for_pids(pids: set[int]) -> list[WinInfo]:
    found: list[WinInfo] = []

    @WNDENUMPROC
    def _enum(hwnd, _lparam):
        proc_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
        if proc_id.value not in pids:
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or ""
        visible = bool(user32.IsWindowVisible(hwnd))
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        found.append(
            WinInfo(
                hwnd=int(hwnd),
                pid=int(proc_id.value),
                title=title,
                visible=visible,
                class_name=cls_buf.value or "",
                left=int(rect.left),
                top=int(rect.top),
                right=int(rect.right),
                bottom=int(rect.bottom),
            )
        )
        return True

    user32.EnumWindows(_enum, 0)
    return found


def list_1c_pids() -> list[int]:
    # lightweight: tasklist via CreateToolhelp32Snapshot
    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    pids: list[int] = []
    if kernel32.Process32FirstW(snap, ctypes.byref(entry)):
        while True:
            name = (entry.szExeFile or "").lower()
            if name in ("1cv8.exe", "1cv8c.exe", "1cestart.exe"):
                pids.append(int(entry.th32ProcessID))
            if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                break
    kernel32.CloseHandle(snap)
    return pids


LOADING_RE = re.compile(
    r"загрузк|loading|конфигурацион|инициализ|подключен|соединен|обновлен",
    re.I,
)
AUTH_RE = re.compile(
    r"аутентификац|парол|пользовател|login|authentication|доступ запрещ",
    re.I,
)
ERROR_RE = re.compile(
    r"ошибк|error|не удалось|не найден|отказано|exception|аварийн",
    re.I,
)
READY_HINT_RE = re.compile(
    r"1[cс]:\s*предприят|управляемое приложение|erp|ерп",
    re.I,
)


def classify_titles(titles: list[str], expect_substr: str = "") -> str:
    """Return phase: none|loading|auth|error|ready|window."""
    if not titles:
        return "none"
    joined = " | ".join(titles)
    if ERROR_RE.search(joined):
        return "error"
    if AUTH_RE.search(joined):
        return "auth"
    if LOADING_RE.search(joined):
        return "loading"
    if expect_substr:
        exp = expect_substr.lower()
        if any(exp in t.lower() for t in titles):
            return "ready"
    if any(READY_HINT_RE.search(t) for t in titles):
        return "ready"
    # visible titled window that is not splash-only
    non_empty = [t for t in titles if t.strip()]
    if non_empty:
        return "window"
    return "none"


def windows_as_dicts(windows: list[WinInfo]) -> list[dict[str, Any]]:
    """Win32 facts only - no UIA (UIA can hang on not-responding 1C splash)."""
    return [asdict(w) for w in windows]


def wait_for(
    pids: list[int],
    timeout_sec: float = 180.0,
    poll_sec: float = 1.0,
    expect_title: str = "",
    ready_phases: Optional[set[str]] = None,
) -> dict[str, Any]:
    ready_phases = ready_phases or {"ready", "auth", "error", "window"}
    deadline = time.time() + timeout_sec
    history: list[dict[str, Any]] = []
    last_phase = "none"
    tracked = set(int(p) for p in pids if p)
    last_heartbeat = 0.0

    while time.time() < deadline:
        alive = {p for p in tracked if process_alive(p)}
        if not alive and tracked:
            # process died before ready
            snap = {
                "ts": time.time(),
                "phase": "exited",
                "alive_pids": [],
                "titles": [],
            }
            history.append(snap)
            return {
                "ok": False,
                "phase": "exited",
                "alive_pids": [],
                "windows": [],
                "history_tail": history[-8:],
                "error": "Process exited before a stable window appeared",
            }

        # also discover child 1cv8 if starter handed off
        for p in list_1c_pids():
            if p not in tracked:
                tracked.add(p)
                alive.add(p)

        wins = [w for w in list_windows_for_pids(alive) if w.visible and w.title.strip()]
        titles = [w.title for w in wins]
        phase = classify_titles(titles, expect_title)
        snap = {
            "ts": time.time(),
            "phase": phase,
            "alive_pids": sorted(alive),
            "titles": titles,
        }
        changed = (
            not history
            or history[-1].get("phase") != phase
            or history[-1].get("titles") != titles
        )
        if changed:
            history.append(snap)
            print(
                f"[wait_window] phase={phase} pids={sorted(alive)} titles={titles[:3]}",
                file=sys.stderr,
                flush=True,
            )
            last_heartbeat = time.time()
        elif time.time() - last_heartbeat >= 30.0:
            left = max(0.0, deadline - time.time())
            print(
                f"[wait_window] heartbeat phase={phase} left={left:.0f}s titles={titles[:2]}",
                file=sys.stderr,
                flush=True,
            )
            last_heartbeat = time.time()
        last_phase = phase

        if phase in ready_phases and phase != "none":
            return {
                "ok": phase not in ("error", "exited"),
                "phase": phase,
                "alive_pids": sorted(alive),
                "windows": windows_as_dicts(wins),
                "history_tail": history[-12:],
            }

        time.sleep(poll_sec)

    wins = [
        w
        for w in list_windows_for_pids({p for p in tracked if process_alive(p)})
        if w.visible and w.title.strip()
    ]
    return {
        "ok": False,
        "phase": "timeout",
        "last_phase": last_phase,
        "alive_pids": sorted(p for p in tracked if process_alive(p)),
        "windows": windows_as_dicts(wins),
        "history_tail": history[-12:],
        "error": f"Timeout {timeout_sec}s waiting for window (last_phase={last_phase})",
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Wait for window by PID / 1cv8 (no VLM)")
    p.add_argument("--pid", type=int, action="append", default=[], help="PID to track (repeatable)")
    p.add_argument("--any-1c", action="store_true", help="Track all 1cv8/1cv8c/1cestart PIDs")
    p.add_argument("--timeout", type=float, default=180.0)
    p.add_argument("--poll", type=float, default=1.0)
    p.add_argument("--expect-title", default="", help="Substring that means ready")
    p.add_argument(
        "--accept",
        default="ready,auth,error,window",
        help="Comma phases that stop waiting",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    pids = list(args.pid or [])
    if args.any_1c or not pids:
        pids = list(set(pids) | set(list_1c_pids()))
    if not pids:
        print(json.dumps({"ok": False, "error": "No PIDs / no 1cv8 running"}, ensure_ascii=False, indent=2))
        return 1
    accept = {x.strip() for x in args.accept.split(",") if x.strip()}
    result = wait_for(
        pids=pids,
        timeout_sec=args.timeout,
        poll_sec=args.poll,
        expect_title=args.expect_title,
        ready_phases=accept,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

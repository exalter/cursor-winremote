"""Start winremote-mcp outside the parent Job Object (fixes intermittent WinError 5)."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200

PORT = 8090
LOG = Path.home() / ".config" / "winremote" / "winremote-start.log"
CONFIG = Path.home() / ".config" / "winremote" / "winremote.toml"
PYTHON = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python312" / "python.exe"


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = time.strftime("%Y-%m-%dT%H:%M:%S%z") + " " + msg
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def pids_on_port(port: int) -> list[int]:
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],
            text=True,
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as e:
        log(f"netstat failed: {e}")
        return []
    pids: list[int] = []
    needle = f":{port}"
    for line in out.splitlines():
        if "LISTENING" not in line or needle not in line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        # local address contains :port
        if parts[1].endswith(needle) or parts[1].endswith(f"]:{port}"):
            try:
                pids.append(int(parts[-1]))
            except ValueError:
                pass
    return sorted(set(pids))


def cmdline(pid: int) -> str:
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
            ],
            text=True,
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        return (out or "").strip()
    except Exception:
        return ""


def main() -> int:
    if not PYTHON.is_file():
        log(f"ERROR python missing: {PYTHON}")
        return 1
    if not CONFIG.is_file():
        log(f"ERROR config missing: {CONFIG}")
        return 1

    for pid in pids_on_port(PORT):
        cl = cmdline(pid)
        if "winremote" in cl.lower():
            log(f"Stopping stale winremote PID={pid}")
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    creationflags=CREATE_NO_WINDOW,
                )
            except Exception as e:
                log(f"taskkill failed: {e}")
            time.sleep(1)

    args = [str(PYTHON), "-m", "winremote", "--config", str(CONFIG), "--enable-tier3"]
    flags = CREATE_BREAKAWAY_FROM_JOB | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        proc = subprocess.Popen(
            args,
            cwd=str(Path.home()),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
    except OSError as e:
        # Breakaway may be denied by job; retry without breakaway but still detached
        log(f"breakaway failed ({e}); retry without CREATE_BREAKAWAY_FROM_JOB")
        proc = subprocess.Popen(
            args,
            cwd=str(Path.home()),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    log(f"Started winremote PID={proc.pid} flags=0x{flags:x}")

    for _ in range(40):
        time.sleep(0.25)
        listening = pids_on_port(PORT)
        if listening:
            log(f"Listening :{PORT} pids={listening}")
            # Report job membership of listener
            try:
                import ctypes
                from ctypes import wintypes

                pid = listening[0]
                h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                injob = wintypes.BOOL()
                ctypes.windll.kernel32.IsProcessInJob(h, None, ctypes.byref(injob))
                ctypes.windll.kernel32.CloseHandle(h)
                log(f"IsProcessInJob={bool(injob.value)}")
            except Exception as e:
                log(f"IsProcessInJob check failed: {e}")
            return 0
    log("ERROR: port not listening after start")
    return 2


if __name__ == "__main__":
    sys.exit(main())

"""Verify this project's runtime and write an evidence record without secrets."""
from __future__ import annotations

import importlib.metadata
import json
import platform
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    with sqlite3.connect(":memory:") as database:
        sqlite_ok = database.execute("select 1 + 1").fetchone()[0] == 2
    packages = {name: importlib.metadata.version(name) for name in ("pandas", "openpyxl", "pytest", "ruff")}
    tools = {}
    for name, command in {"git": "git", "node": "node", "npm": "npm.cmd", "codex": "codex"}.items():
        executable = shutil.which(command)
        if executable:
            try:
                result = subprocess.run([executable, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False)
                tools[name] = {"path": executable, "returncode": result.returncode, "version": result.stdout.strip(), "diagnostic": result.stderr.strip()}
            except (OSError, subprocess.TimeoutExpired) as exc:
                tools[name] = {"path": executable, "error": str(exc)}
        else:
            tools[name] = {"path": None}
    result = {
        "checked_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "platform": platform.platform(), "python": platform.python_version(),
        "python_executable": sys.executable, "virtual_environment": sys.prefix != sys.base_prefix,
        "packages": packages, "sqlite_version": sqlite3.sqlite_version, "sqlite_smoke_check": sqlite_ok,
        "tools": tools,
        "scope": "Development environment only. No application, business tests, or live Codex Hook integration verified.",
    }
    out = ROOT / "evidence/environment/environment.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["virtual_environment"] or not sqlite_ok or any(tools[name].get("returncode") != 0 for name in ("git", "codex")):
        raise SystemExit("Required development environment checks failed.")


if __name__ == "__main__":
    main()

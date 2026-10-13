"""Offline protocol and launcher checks; these do not prove live Hook loading."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid


def payload(command: str) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}


def check_result(completed: subprocess.CompletedProcess, denied: bool) -> str | None:
    if completed.returncode != 0:
        return f"unexpected_exit_code:{completed.returncode}; stderr={completed.stderr.strip()}"
    if completed.stderr.strip():
        return f"unexpected_stderr:{completed.stderr.strip()}"
    if not denied:
        return None if not completed.stdout.strip() else "allowed_case_produced_output"
    try:
        output = json.loads(completed.stdout)
        data = output["hookSpecificOutput"]
        assert data["hookEventName"] == "PreToolUse"
        assert data["permissionDecision"] == "deny"
        assert isinstance(data["permissionDecisionReason"], str) and data["permissionDecisionReason"]
    except (json.JSONDecodeError, KeyError, AssertionError, TypeError):
        return "invalid_deny_protocol"
    return None


def main() -> int:
    script = Path(__file__).with_name("guard_commands.py").resolve()
    cases = [
        ("safe_git_status", "git status --short", False),
        ("safe_output_mentions_command", "Write-Output 'Remove-Item -Recurse'", False),
        ("safe_output_mentions_force", 'Write-Output "git push --force"', False),
        ("ordinary_push", "git push origin main", False),
        ("ordinary_file_delete", "Remove-Item -LiteralPath './scratch.txt' -WhatIf", False),
        ("explicit_nonrecursive_delete", "Remove-Item './scratch.txt' -Recurse:$false -WhatIf", False),
        ("ps_recursive_delete", "Remove-Item -LiteralPath './scratch' -Recurse -Force", True),
        ("ps_recursive_whatif", "Remove-Item -LiteralPath './scratch' -Recurse -WhatIf", True),
        ("ps_recursive_mixedcase", "rEmOvE-iTeM './scratch' -rEcUrSe:$true", True),
        ("ps_recursive_abbreviation", "ri './scratch' -rec -WhatIf", True),
        ("ps_pipeline_delete", "Get-Item './scratch' | Remove-Item -Recurse -WhatIf", True),
        ("ps_second_statement", "Get-Location; Remove-Item './scratch' -Recurse -WhatIf", True),
        ("ps_backtick_command", "Remove-`Item './scratch' -Recurse -WhatIf", True),
        ("ps_qualified_command", "Microsoft.PowerShell.Management\\Remove-Item './scratch' -Recurse -WhatIf", True),
        ("ps_quoted_executable", "& 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe' -NoProfile -Command \"Remove-Item './scratch' -Recurse -WhatIf\"", True),
        ("ps_comment", "Write-Output 'safe' # Remove-Item './scratch' -Recurse", False),
        ("ps_comment_next_line", "Write-Output 'safe' # comment\nRemove-Item './scratch' -Recurse -WhatIf", True),
        ("git_force_long", "git push origin main --force", True),
        ("git_force_short", "git push -f origin main", True),
        ("git_force_cluster", "git push -vf origin main", True),
        ("git_force_lease", "git -C 'E:\\中文 空格目录' push --force-with-lease=main:abc origin main", True),
        ("git_force_refspec", "git push origin +main:main", True),
        ("cmd_recursive", 'cmd /c "rmdir /s /q scratch"', True),
        ("unix_recursive", "rm -rf ./scratch", True),
        ("safe_probe", "Write-Output 'VC_R03_GUARD_PROBE'", True),
    ]
    results = []
    for name, command, denied in cases:
        completed = subprocess.run([sys.executable, "-X", "utf8", str(script)],
            input=json.dumps(payload(command), ensure_ascii=False), capture_output=True,
            text=True, encoding="utf-8", timeout=10)
        error = check_result(completed, denied)
        results.append({"name": name, "expected": "deny" if denied else "no_objection", "passed": error is None, "error": error})
    for name, raw in [
        ("invalid_json", "{"),
        ("invalid_object", "[]"),
        ("wrong_event", json.dumps({"hook_event_name": "PostToolUse"})),
        ("missing_command", json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"cmd": "git status"}})),
    ]:
        completed = subprocess.run([sys.executable, "-X", "utf8", str(script)], input=raw,
            capture_output=True, text=True, encoding="utf-8", timeout=10)
        error = check_result(completed, True)
        results.append({"name": name, "expected": "deny", "passed": error is None, "error": error})

    # Invoke only the guard, passing dangerous-looking text as JSON stdin.
    # The guard never executes the contents of tool_input.command.
    if sys.platform == "win32":
        temporary = (script.parent / f"VC R03 Hook 中文路径 {uuid.uuid4().hex}").resolve()
        if temporary.parent != script.parent:
            raise RuntimeError("Launcher probe directory escaped the scripts directory")
        temporary.mkdir()
        copied = temporary / "guard commands.py"
        try:
            shutil.copyfile(script, copied)
            command = f'{sys.executable} -X utf8 "{copied}"'
            if " " in sys.executable:
                # This alternative covers a Python installation containing spaces.
                command = f'py -3.12 -X utf8 "{copied}"'
            for name, launcher in [
                ("windows_cmd_quoted_unicode_path", f'cmd.exe /d /s /c "{command}"'),
                ("windows_powershell_quoted_unicode_path", ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command]),
            ]:
                completed = subprocess.run(launcher, input=json.dumps(payload("Write-Output 'VC_R03_GUARD_PROBE'")),
                    capture_output=True, text=True, encoding="utf-8", timeout=15)
                error = check_result(completed, True)
                results.append({"name": name, "expected": "deny", "passed": error is None, "error": error})
        finally:
            copied.unlink(missing_ok=True)
            temporary.rmdir()

    failed = sum(not row["passed"] for row in results)
    report = {
        "checked_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
        "verification_type": "offline_script_protocol_and_windows_launcher",
        "live_codex_hook_integration": "NOT_VERIFIED_REQUIRES_USER_TRUST_AND_REAL_TOOL_CALL",
        "python_version": sys.version.split()[0],
        "guard_path": str(script),
        "total": len(results), "passed": len(results) - failed, "failed": failed,
        "cases": results,
        "coverage_limit": "Literal command lexer only; no general alias, variable, encoding, external script or arbitrary program analysis.",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

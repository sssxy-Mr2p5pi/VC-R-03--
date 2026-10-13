"""Codex PreToolUse guard for a small, documented set of shell command forms.

This lexer is not a PowerShell AST or a complete shell security boundary. It
does not resolve variables, aliases defined by the user, encoded commands,
external scripts, Python file operations, or every form of dynamic execution.
Keep Codex sandbox and approval controls enabled.
"""

from __future__ import annotations

import json
import re
import sys

PROBE = "VC_R03_GUARD_PROBE"
MAX_INPUT_BYTES = 1_048_576


def command_segments(command: str) -> list[list[str]]:
    """Tokenize common literal command forms, including quoted arguments.

    Preserve Windows backslashes. Recognize PowerShell backtick escapes,
    doubled single quotes, comments, pipelines, and statement separators.
    Unsupported dynamic syntax remains outside this guard's coverage.
    """
    segments: list[list[str]] = []
    tokens: list[str] = []
    token: list[str] = []
    quote: str | None = None
    i = 0

    def finish_token() -> None:
        if token:
            tokens.append("".join(token))
            token.clear()

    def finish_segment() -> None:
        finish_token()
        if tokens:
            segments.append(tokens.copy())
            tokens.clear()

    while i < len(command):
        char = command[i]
        if char == "`" and quote != "'" and i + 1 < len(command):
            i += 1
            if command[i] == "\r" and i + 1 < len(command) and command[i + 1] == "\n":
                i += 1
            elif command[i] != "\n":
                token.append(command[i])
        elif quote:
            if char == quote:
                if quote == "'" and i + 1 < len(command) and command[i + 1] == "'":
                    token.append("'")
                    i += 1
                else:
                    quote = None
            else:
                token.append(char)
        elif char in ("'", '"'):
            quote = char
        elif char == "#" and not token:
            while i < len(command) and command[i] not in "\r\n":
                i += 1
            finish_segment()
        elif char in ";|&\r\n{}":
            finish_segment()
        elif char.isspace():
            finish_token()
        else:
            token.append(char)
        i += 1
    finish_segment()
    return segments


def executable_name(token: str) -> str:
    name = re.split(r"[\\/]", token)[-1].casefold()
    return re.sub(r"\.(?:exe|cmd|bat)$", "", name)


def is_recursive_flag(token: str, executable: str) -> bool:
    flag = token.casefold()
    prefix, _, value = flag.partition(":")
    if prefix in {"-r", "-re", "-rec", "-recu", "-recur", "-recurs", "-recurse"}:
        return value not in {"$false", "false", "0"}
    if executable in {"rm", "rmdir", "rd"}:
        return flag == "--recursive" or flag == "/s" or bool(re.fullmatch(r"-[a-z]*r[a-z]*", flag))
    return False


def git_push_reason(arguments: list[str]) -> str | None:
    # Skip common Git global options before finding the actual subcommand.
    with_value = {"-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
    i = 0
    while i < len(arguments):
        token = arguments[i]
        if token == "-C" or token in with_value:
            i += 2
        elif token.startswith("-"):
            i += 1
        else:
            break
    if i >= len(arguments) or arguments[i] != "push":
        return None
    for token in arguments[i + 1:]:
        if token.split("=", 1)[0] in {"--force", "--force-with-lease", "--force-if-includes"}:
            return "项目 Hook 拒绝 Git 强制推送；请先审阅提交与远端状态。"
        if re.fullmatch(r"-[A-Za-z]*f[A-Za-z]*", token) or token.startswith("+"):
            return "项目 Hook 拒绝 Git 强制推送参数或强制 refspec。"
    return None


def inspect_command(command: str, depth: int = 0) -> str | None:
    if depth > 4:
        return "项目 Hook 拒绝超过检查深度的嵌套 shell 命令；请拆成单独命令。"
    deletion_commands = {"remove-item", "ri", "rm", "rmdir", "rd", "del", "erase"}
    for tokens in command_segments(command):
        if PROBE in tokens:
            return f"项目 Hook 集成探针 {PROBE} 已拒绝；该探针仅输出文字，没有删除操作。"
        executable = executable_name(tokens[0])
        arguments = tokens[1:]
        if executable in deletion_commands and any(is_recursive_flag(a, executable) for a in arguments):
            return "项目 Hook 拒绝递归删除命令（含 -WhatIf 预演）；请先核对目标并改用具体文件操作。"
        if executable == "git":
            reason = git_push_reason(arguments)
            if reason:
                return reason
        wrapper_flags = {
            "powershell": {"-command", "-c"},
            "pwsh": {"-command", "-c"},
            "cmd": {"/c", "/k"},
            "bash": {"-c", "-lc"},
            "sh": {"-c"},
        }.get(executable, set())
        for index, argument in enumerate(arguments):
            if argument.casefold() in wrapper_flags:
                reason = inspect_command(" ".join(arguments[index + 1:]), depth + 1)
                if reason:
                    return reason
                break
    return None


def evaluate_payload(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return "项目 Hook 收到无效 JSON 对象，拒绝继续；请检查 Hook 配置。"
    if payload.get("hook_event_name") != "PreToolUse":
        return "项目 Hook 收到错误事件名，拒绝继续；仅支持 PreToolUse。"
    if payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
        return "项目 Hook 缺少 tool_input.command，拒绝继续；请检查事件格式。"
    return inspect_command(tool_input["command"])


def main() -> None:
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            reason = "项目 Hook 输入超过大小限制，拒绝继续。"
        else:
            reason = evaluate_payload(json.loads(raw.decode("utf-8-sig")))
    except (UnicodeDecodeError, json.JSONDecodeError):
        reason = "项目 Hook 输入无法按 UTF-8 JSON 读取，拒绝继续。"
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}, ensure_ascii=False))
    # Exit 0 with no stdout means no objection, not a permission override.


if __name__ == "__main__":
    main()

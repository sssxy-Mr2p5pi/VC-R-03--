"""Verify frozen file hashes and acceptance reference IDs; do not infer semantic coverage."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZONE = timezone(timedelta(hours=8))
MANIFEST = ROOT / "baseline.json"

BASELINE_FILES = (
    "要做什么.md",
    "怎么做.md",
    "怎么算做对.md",
    "决定记录.md",
    "待确认事项.md",
    "选题自查表.md",
    "AGENTS.md",
    "CLAUDE.md",
)
ENTRY_FILES = ("AGENTS.md", "CLAUDE.md")
SPEC = "要做什么.md"
ACCEPT = "怎么算做对.md"
DECISIONS = "决定记录.md"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def load_manifest() -> dict:
    if not MANIFEST.exists():
        raise SystemExit("缺少 baseline.json；先运行 --init 建立基线。")
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def save_manifest(data: dict) -> None:
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def decision_numbers(text: str) -> list[int]:
    return sorted(int(m) for m in re.findall(r"^##\s*D-(\d+)", text, re.MULTILINE))


def current_decision_range() -> str:
    """Range of decisions under the last "现行" heading;早期条目按历史处理。"""
    text = read_text(DECISIONS)
    markers = list(re.finditer(r"^##.*现行.*$", text, re.MULTILINE))
    ids = decision_numbers(text[markers[-1].start() :]) if markers else decision_numbers(text)
    if not ids:
        ids = decision_numbers(text)
    return f"D-{ids[0]:02d}..D-{ids[-1]:02d}" if ids else "无"


def next_version(previous: str) -> str:
    today = datetime.now(ZONE).date().isoformat()
    if not previous.startswith(today):
        return today
    suffix = previous[len(today) :].lstrip(".")
    return f"{today}.{int(suffix) + 1}" if suffix.isdigit() else f"{today}.2"


def report_drift(data: dict) -> list[str]:
    drifted = []
    for name in BASELINE_FILES:
        path = ROOT / name
        tag = "入口" if name in ENTRY_FILES else "规格"
        if not path.exists():
            print(f"  [{tag}] {name:<14} 缺失")
            drifted.append(name)
            continue
        if data["files"].get(name) == digest(path):
            print(f"  [{tag}] {name:<14} 一致")
        else:
            print(f"  [{tag}] {name:<14} 已变更  <-- 需走变更流程")
            drifted.append(name)
    return drifted


def expand_refs(text: str, prefix: str) -> set[str]:
    """展开 FR-17/18、DP-03/06 这类简写，避免漏检。"""
    found: set[str] = set()
    for match in re.finditer(rf"{prefix}-(\d+)((?:/\d+)*)", text):
        for number in (match.group(1), *re.findall(r"\d+", match.group(2))):
            found.add(f"{prefix}-{int(number):02d}")
    return found


def acceptance_refs() -> set[str]:
    text = read_text(ACCEPT)
    found = expand_refs(text, "FR") | expand_refs(text, "DP")
    for line in text.splitlines():
        if "验收覆盖" in line:
            for token in re.findall(r"(\d{1,2}(?:/\d{1,2})*)\s*→", line):
                found |= {f"DP-{int(n):02d}" for n in token.split("/")}
    return found


def report_coverage() -> list[str]:
    problems = []
    spec = read_text(SPEC)
    declared = expand_refs(spec, "FR") | expand_refs(spec, "DP")
    referenced = acceptance_refs()
    for missing in sorted(declared - referenced, key=lambda s: (s[:2], int(s.split("-")[1]))):
        problems.append(f"{missing} 在《{SPEC}》中已定义，但《{ACCEPT}》未引用")
    for extra in sorted(referenced - declared, key=lambda s: (s[:2], int(s.split("-")[1]))):
        problems.append(f"{extra} 在《{ACCEPT}》中被引用，但《{SPEC}》中没有定义")
    return problems


def require_acceptance_refs() -> None:
    """Reject a baseline write before touching the manifest when references are invalid."""
    problems = report_coverage()
    if problems:
        print("拒绝写入基线：FR／DP 编号引用校验失败。")
        for item in problems:
            print(f"  {item}")
        raise SystemExit(1)


def cmd_init(args: argparse.Namespace) -> int:
    if MANIFEST.exists() and not args.force:
        raise SystemExit("baseline.json 已存在；重建请加 --force。")
    require_acceptance_refs()
    now = datetime.now(ZONE).isoformat(timespec="seconds")
    data = {
        "baseline_version": next_version(""),
        "updated_at": now,
        "current_decisions": current_decision_range(),
        "files": {name: digest(ROOT / name) for name in BASELINE_FILES},
        "history": [{"version": next_version(""), "date": now, "note": args.note or "建立基线"}],
    }
    save_manifest(data)
    print(f"已建立基线 {data['baseline_version']}，{len(BASELINE_FILES)} 个文件。")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    data = load_manifest()
    if not args.note:
        raise SystemExit("--update 必须带 --note 说明本次改动原因。")
    recorded = max(decision_numbers(read_text(DECISIONS))[-1:] or [0])
    baseline = max(int(x) for x in re.findall(r"\d+", data["current_decisions"])) if data.get("current_decisions") else 0
    if recorded <= baseline and not args.force:
        raise SystemExit(
            f"《{DECISIONS}》最新决定为 D-{recorded:02d}，未超过基线记录的 {data['current_decisions']}。\n"
            "按变更协议，应先新增一条 D-xx（日期/决定/备选/理由），再更新基线。\n"
            "若本次只是错字或格式修正，可加 --force。"
        )
    require_acceptance_refs()
    now = datetime.now(ZONE).isoformat(timespec="seconds")
    version = next_version(data["baseline_version"])
    drifted = [n for n in BASELINE_FILES if data["files"].get(n) != digest(ROOT / n)]
    data["baseline_version"] = version
    data["updated_at"] = now
    data["current_decisions"] = current_decision_range()
    data["files"] = {name: digest(ROOT / name) for name in BASELINE_FILES}
    data["history"].append({"version": version, "date": now, "note": args.note, "changed": drifted})
    save_manifest(data)
    print(f"基线已更新为 {version}；本次变更文件：{'、'.join(drifted) if drifted else '无'}")
    print("别忘了同步《AGENTS.md》《CLAUDE.md》以及项目外的报告素材。")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init", action="store_true", help="建立基线清单（首次使用）")
    parser.add_argument("--update", action="store_true", help="记录本次规格改动并推进版本")
    parser.add_argument("--list", action="store_true", help="只显示基线信息，不做校验")
    parser.add_argument("--note", default="", help="本次改动原因，--update 时必填")
    parser.add_argument("--force", action="store_true", help="豁免新决定/重建限制；仍检查编号引用")
    args = parser.parse_args()

    if args.init:
        raise SystemExit(cmd_init(args))
    if args.update:
        raise SystemExit(cmd_update(args))

    data = load_manifest()
    print(f"基线版本：{data['baseline_version']}    更新于：{data['updated_at']}")
    print(f"现行决定：{data['current_decisions']}")
    if args.list:
        for name, value in data["files"].items():
            print(f"  {name:<14} {value[:16]}…")
        return

    print("\n[1] 规格文件校验")
    drifted = report_drift(data)

    print("\n[2] 验收编号引用校验")
    problems = report_coverage()
    if problems:
        for item in problems:
            print(f"  {item}")
    else:
        print("  FR／DP 编号均有验收文字引用；语义与执行结果需另查")

    if drifted or problems:
        print("\n结果：基线或编号引用存在问题。")
        if drifted:
            print("若是有意的规格修订，按变更协议：先在《决定记录》新增 D-xx，再执行")
            print("  .\\.venv\\Scripts\\python.exe -X utf8 .\\scripts\\check_baseline.py --update --note \"原因\"")
        raise SystemExit(1)
    print("\n结果：基线文件一致，验收编号引用完整（不代表语义覆盖或测试通过）。")


if __name__ == "__main__":
    main()

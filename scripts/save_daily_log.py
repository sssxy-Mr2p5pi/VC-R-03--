"""Create today's real record and export actual commits; never backdate evidence."""
from __future__ import annotations

import argparse
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZONE = timezone(timedelta(hours=8))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--note", action="append", default=[], help="A truthful action note; can be repeated.")
    args = parser.parse_args()
    now = datetime.now(ZONE)
    day = ROOT / "每日工作记录" / now.date().isoformat()
    screenshots = day / "截图"
    screenshots.mkdir(parents=True, exist_ok=True)
    journal = day / "今天做了什么.md"
    if not journal.exists():
        journal.write_text(f"# {now.date().isoformat()} 工作记录\n\n记录由辅助脚本建立；事实由本人核对，截图由本人补充。\n", encoding="utf-8")
    if args.note:
        with journal.open("a", encoding="utf-8") as out:
            out.write(f"\n## {now.strftime('%H:%M:%S')} 记录\n\n")
            out.writelines(f"- {note}\n" for note in args.note)
    readme = screenshots / "截图说明.md"
    if not readme.exists():
        readme.write_text("# 当天截图\n\n请保存本人实际操作的终端、界面或纠偏截图。此说明文件不能替代截图；当前是否齐全由提交核对检查。禁止生成或照抄虚构截图。\n", encoding="utf-8")
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", "HEAD"], capture_output=True, text=True, check=False)
    lines = []
    if head.returncode == 0:
        history = subprocess.run(["git", "-C", str(ROOT), "log", "--all", "--date=iso-strict", "--format=%h%x09%cI%x09%s"], capture_output=True, text=True, encoding="utf-8", check=True)
        for row in history.stdout.splitlines():
            commit_id, timestamp, message = row.split("\t", 2)
            when = datetime.fromisoformat(timestamp).astimezone(ZONE)
            if when.date() == now.date():
                lines.append(f"{commit_id} {when.isoformat(timespec='seconds')} {message}")
    text = "\n".join(lines) if lines else "当天尚无 Git 提交；本记录如实保留，不能代替提交。"
    (day / "提交记录.txt").write_text(f"导出时间：{now.isoformat(timespec='seconds')}\n时区：Asia/Shanghai；按提交时间筛选当日，包含各分支真实历史。\n\n{text}\n", encoding="utf-8")
    print(f"Daily record: {day}")
    print(f"Actual commits exported: {len(lines)}")


if __name__ == "__main__":
    main()

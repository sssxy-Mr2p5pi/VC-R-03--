"""Read-only preflight; file existence does not certify coursework completion."""
from __future__ import annotations

import json
import re
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    required = ["大作业报告.docx", "中期汇报.pptx", "要做什么.md", "怎么做.md", "怎么算做对.md", "选题自查表.md", "决定记录.md", "CLAUDE.md", "每日工作记录"]
    missing = [name for name in required if not (ROOT / name).exists()]
    daily = ROOT / "每日工作记录"
    days = []
    invalid_dates = []
    today = datetime.now(timezone(timedelta(hours=8))).date()
    for folder in sorted(daily.iterdir()) if daily.exists() else []:
        if folder.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", folder.name):
            try:
                record_date = date.fromisoformat(folder.name)
            except ValueError:
                invalid_dates.append({"name": folder.name, "reason": "无效日历日期"})
                continue
            if record_date > today:
                invalid_dates.append({"name": folder.name, "reason": "未来日期不能计为已完成记录"})
                continue
            absent = [name for name in ("今天做了什么.md", "提交记录.txt") if not (folder / name).is_file() or (folder / name).stat().st_size == 0]
            pictures = [p.name for p in (folder / "截图").glob("*") if p.is_file() and p.stat().st_size > 0 and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}]
            if not pictures:
                absent.append("实际截图")
            days.append({"date": folder.name, "missing": absent, "screenshot_files": pictures})
    status = subprocess.run(["git", "-C", str(ROOT), "status", "--short"], capture_output=True, text=True, encoding="utf-8", check=False)
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", "HEAD"], capture_output=True, text=True, check=False)
    report = {"missing_files": missing, "daily_records": days, "date_folder_count": len(days), "invalid_or_future_dates": invalid_dates, "has_git_commit": head.returncode == 0, "working_tree_changes": status.stdout.splitlines(), "limits": ["仅检查文件和实际图片是否存在，不能证明内容真实、质量合格或达到评分标准。", "说明书要求不少于8天，样例首页写10天；需课程组澄清。", "业务测试、本人复跑、现场演示和工具等效认定需要独立证据。"]}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if missing or invalid_dates or len(days) < 8 or any(day["missing"] for day in days) or head.returncode:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

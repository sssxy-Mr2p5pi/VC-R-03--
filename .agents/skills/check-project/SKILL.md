---
name: check-project
description: 核验VC-R-03项目的Python环境、官方源文件完整性与准备脚本，在开工或准备工作结束时使用。业务验收与本人操作另行记录。
---

在本项目根目录读取 CLAUDE.md 和待确认事项.md，使用 `.venv\Scripts\python.exe`。

1. 运行 `scripts/check_environment.py`，查看真实版本与失败项。
2. 原始数据已有下载记录时运行 `scripts/download_data.py`，只核对原文件校验值。若数据缺失，说明将从UCI官方重新获取，不构造替代文件。
3. 运行 `scripts/check_guard.py` 与 `python -m ruff check scripts`，区分Hook离线检查和真实集成。
4. 仅在原始文件或统计逻辑发生变化，或用户要求重新核验时运行 `scripts/profile_data.py`；不要每次开工重复扫描全量工作簿。
5. 回报已检查项、失败项及证据路径。业务测试未写、本人复跑未做、Hook未在实际会话触发时，明确标为待完成。

遇到数据两边、清洗、切片或配对口径问题先问用户，不在本技能中自行决定业务规则。

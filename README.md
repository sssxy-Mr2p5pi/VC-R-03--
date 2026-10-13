# VC-R-03 退货对账与差异处理系统

当前是项目准备阶段：已获取 UCI 官方原始数据，建立独立 Python 环境和过程记录框架。业务页面与对账流程尚未实现。

GitHub 远端由用户指定为 https://github.com/sssxy-Mr2p5pi/VC-R-03-- 。
本地准备工作保存在 `setup/data-environment` 分支。远端主分支历史尚未拉取成功；联网后先取得并检查远端历史，再合并准备工作。本次没有推送。

## 先读哪里

- `待确认事项.md`：两边数据、配对、切片、清洗和课程工具等效认定的未定项。
- `要做什么.md` / `怎么做.md` / `怎么算做对.md`：规格初稿，继续开发前逐条确认。
- `CLAUDE.md`：完整项目规矩；Codex 入口为 `AGENTS.md`。
- 学习指南在项目外 `../VC-R-03-学习指南/`，按本人要求不入版本记录和课程提交包。

## 已验证的准备命令

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 .\scripts\check_environment.py
.\.venv\Scripts\python.exe -X utf8 .\scripts\download_data.py
.\.venv\Scripts\python.exe -X utf8 .\scripts\profile_data.py
.\.venv\Scripts\python.exe -X utf8 .\scripts\check_guard.py
.\.venv\Scripts\python.exe -m ruff check .\scripts
```

环境丢失或换机后先安装 Python 3.12 与 Git，再运行 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1`。脚本仅为本项目建 `.venv` 并按锁定依赖安装。
首次获取原始文件需要联网。原始大文件不进 Git；来源、哈希与可重复获取脚本进入 Git。换机后须重新下载并核对来源。

## 今天收工

实际完成一件事后进行真实 Git 提交，再执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 .\scripts\save_daily_log.py
```

脚本创建当天说明和截图目录、导出真实当日 Git 历史，不编写“已完成”叙述，不生成截图。本人补充三五行事实及实际截图。
`提交记录.txt` 是从 Git 导出的文件，不再进入 Git，避免为了记录自身提交而产生循环；课程打包脚本会直接收录每日目录中的它。

## 当前证据和缺项

环境：`evidence/environment/`；数据：`evidence/data/download_manifest.json`、`raw_profile.json`、`raw_preview.csv`。
数据全量 1,067,371 行。实际字段名、两个工作表范围及异常统计以 raw_profile.json 为准；尚未清洗、切片、配对或派单。
Hook 离线自检通过不能证明 Hook 在新项目会话中已被本人信任并实际拦截。
准备工具检查保存于 `evidence/tests/preparation_checks.json`，Hook 离线检查保存于 `evidence/tests/guard_checks.json`。两者都不计入课程要求的业务测试数量。
业务测试、本人复跑截图、完整报告、PPT、跨日证据等仍需开发过程中完成。

提交前运行 `scripts/check_submission.py` 查看可机械检查的缺项；当前阶段预期会报告缺项。
报告使用 `templates/大作业报告-模板.docx`，形成本人内容后保存到根目录固定名称；中期 3—5 页，最终 8—12 页。
官方打包脚本最后才运行，输出位于项目旁，学习指南不在其收集范围内。

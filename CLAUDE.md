# 项目规矩

实际主要代理：Codex。此文件保留课程打包脚本要求的固定名称；Codex 从 AGENTS.md 进入并读取本文件。

## 当前阶段

仅完成工作区、官方数据获取、只读数据核验和开发环境准备。业务系统、页面、配对算法和业务验收尚未完成。
两边输入与配对口径保留待确认，见 `待确认事项.md`。

## 运行方法

在项目根目录使用 PowerShell。环境首次重建：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

直接使用项目解释器，无需激活虚拟环境，也无需更改全局执行策略：

```powershell
.\.venv\Scripts\python.exe -X utf8 .\scripts\check_environment.py
.\.venv\Scripts\python.exe -X utf8 .\scripts\download_data.py
.\.venv\Scripts\python.exe -X utf8 .\scripts\profile_data.py
.\.venv\Scripts\python.exe -X utf8 .\scripts\check_guard.py
.\.venv\Scripts\python.exe -m ruff check .\scripts
.\.venv\Scripts\python.exe -X utf8 .\scripts\save_daily_log.py
.\.venv\Scripts\python.exe -X utf8 .\scripts\check_submission.py
```

目前没有应用启动命令。`check_submission.py` 在初始阶段报告缺项并返回失败是正常事实，不能为使它通过而创建虚假材料。
业务测试写入 tests 后使用 `.\.venv\Scripts\python.exe -m pytest -q`；测试目录当前没有业务测试，不能记为“全绿”。

## 目录职责

根目录三份规格定义做什么、怎样做和怎样验收；README 提供已验证入口；src 放业务代码，tests 放业务测试，scripts 放可重复执行的辅助程序。
data/raw 保留官方下载文件；data/processed 只放有明确规则、可重新生成的派生数据。
evidence 保存带来源的环境、数据、测试和审查记录；每日工作记录按实际日期收录说明、Git 记录和本人截图。
templates 存课程报告空白模板；正式报告完成后用根目录 `大作业报告.docx`。PPT 同理，固定名为 `中期汇报.pptx`。

## 禁动范围与本人要求

- 用户明确要求：有任何作业不明确之处必须先问；两份学习指南独立存放，不进入 Git 与提交包。
- 不修改 data/raw 中的原始文件；发生校验值不一致时先调查，不自动覆盖。
- 不用虚构财务账、仓库账或人为差异冒充公开源数据；教学例子标为假设，测试夹具与真实数据分开说明。
- 不伪造提交时间、截图、耗时、费用、失败经历、测试通过或本人操作。AI 可根据本轮实际动作起草当天记录，由本人核对。
- 每做完一件事及时提交，提交说明以对应 FR 编号开头；不倒填时间，不为了凑数量拆分虚假工作。
- 尚未确定的匹配规则不做成默认代码。无法确认与已发现差异的含义需先在规格中约定。
- 课程原始脚本保持原样；打包会重建项目旁同名材料目录和 ZIP，运行前核对输出位置。
- 本次仅连接用户指定 GitHub 远端；远端发布需按用户实际指示进行，不默认推送。

## Codex 工程手段

项目技能：`.agents/skills/check-project/SKILL.md`，用于已实现的环境与数据核验。应用实现后再建立真实 run-app 技能。
项目 Hook：`.codex/hooks.json` 与 `scripts/guard_commands.py`。脚本自检和真实 Codex 工具调用拦截分别验证；本人审阅并信任 Hook 后再做安全探针。
Hook 覆盖有限，不能替代沙箱；阻断返回 deny，不能把不支持的 ask 当成可靠保护。
全量业务测试子代理、核心审查、本人复跑及截图在业务实现后按课程要求补齐。课程对工具替代的评分认定仍待确认。

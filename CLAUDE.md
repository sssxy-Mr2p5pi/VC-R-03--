# 项目规矩

实际主要代理：Codex。此文件保留课程打包脚本要求的固定名称；Codex 从 AGENTS.md 进入并读取本文件。

## 当前阶段

已完成工作区、官方数据获取、只读数据核验、开发环境准备及基线约束与校验工具。业务系统、页面、配对算法和业务验收尚未完成。
2026-10-14 已完成本轮规格修订与全量规则只读验证，现行决定见 D-18～D-32。Flask＋SQLite及三模块已定；Flask尚未安装、应用和业务测试尚未实现。权限回避、四种动作和幂等边界以《怎么做》为准；外部待定项见《待确认事项》。本轮不提交或推送。

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
.\.venv\Scripts\python.exe -X utf8 .\scripts\check_baseline.py
```

开工先跑 `check_baseline.py`：它校验 `baseline.json` 记录的8个文件（6份规格＋2份入口）哈希，并检查FR／DP编号是否在《怎么算做对》中引用；语义覆盖和测试执行须另查。报告"已变更"时按 `AGENTS.md` 的改动同步清单处理，**不直接改规格、也不改哈希了事**。

目前没有应用启动命令。`check_submission.py` 在初始阶段报告缺项并返回失败是正常事实，不能为使它通过而创建虚假材料。
业务测试写入 tests 后使用 `.\.venv\Scripts\python.exe -m pytest -q`；测试目录当前没有业务测试，不能记为“全绿”。

## 目录职责

根目录三份规格定义做什么、怎样做和怎样验收；README 提供已验证入口；src 放业务代码，tests 放业务测试，scripts 放可重复执行的辅助程序。
data/raw 保留官方下载文件；data/processed 只放有明确规则、可重新生成的派生数据。
evidence 保存带来源的环境、数据、测试和审查记录；每日工作记录按实际日期收录说明、Git 记录和本人截图。
templates 存课程报告空白模板；正式报告完成后用根目录 `大作业报告.docx`。PPT 同理，固定名为 `中期汇报.pptx`。

## 禁动范围与本人要求

- 用户明确要求：有任何作业不明确之处必须先问；学习指南、`纠偏记录.md` 与 `中期报告素材（不提交）\` 独立存放于 `../VC-R-03-学习指南/`，三者均不进入 Git 与提交包。
- 不修改 data/raw 中的原始文件；发生校验值不一致时先调查，不自动覆盖。
- 不用虚构财务账、仓库账或人为差异冒充公开源数据；教学例子标为假设，测试夹具与真实数据分开说明。
- 不伪造提交时间、截图、耗时、费用、失败经历、测试通过或本人操作。AI 可根据本轮实际动作起草当天记录，由本人核对。
- 每做完一件事及时提交，提交说明以对应 FR 编号开头；不倒填时间，不为了凑数量拆分虚假工作。
- 尚未确定的匹配规则不做成默认代码。无法确认与已发现差异的含义需先在规格中约定。
- 课程原始脚本保持原样；打包会重建项目旁同名材料目录和 ZIP，运行前核对输出位置。
- 本次仅连接用户指定 GitHub 远端；远端发布需按用户实际指示进行，不默认推送。

## Codex 工程手段

项目技能：`.agents/skills/check-project/SKILL.md`，用于已实现的环境与数据核验。应用实现后再建立真实 run-app 技能。
基线控制：`baseline.json` ＋ `scripts/check_baseline.py`。基线固定8个文件（6份规格＋2份入口）的SHA-256与现行决定区间，并校验FR／DP编号引用；改规格的唯一路径是先在《决定记录》新增 D-xx，再执行 `--update --note "原因"`。同步范围与顺序见 `AGENTS.md` 的改动同步清单。
每日收工技能：`.agents/skills/daily-summary/SKILL.md`，用于按课程《大作业说明书》动作十整理当天记录：把当天记录压到 5～8 条、导出当天提交记录、回填决定记录与项目外的《纠偏记录.md》、登记 token 用量与费用、提醒本人补截图。只整理已发生的事实，不编造、不倒填。
项目 Hook：`.codex/hooks.json` 与 `scripts/guard_commands.py`。脚本自检和真实 Codex 工具调用拦截分别验证；本人审阅并信任 Hook 后再做安全探针。
Hook 覆盖有限，不能替代沙箱；阻断返回 deny，不能把不支持的 ask 当成可靠保护。
全量业务测试子代理、核心审查、本人复跑及截图在业务实现后按课程要求补齐。课程对工具替代的评分认定仍待确认。

2026-10-14 补充审查已完成：输入类型/数值读取、数量符号、字符与分级碰撞、版本索引和质量归置以《要做什么》DP-03/05/10为准。S1—S11及后续意见的范围判断见 `evidence/reviews/2026-10-14-S1-S11/审查结论.md`。新增合同仍待应用实现和业务验收，本轮只修改文档及保存只读审查证据。

2026-10-14 已补差异单类型及固定来源/工单身份（D-30/D-31）。读《决定记录》时先看状态索引；原待定或旧规则保留为历史，解决登记日期和现行位置写在条目旁。P-01～P-08仍有外部/后续待办，设计解决不等于应用完成。

2026-10-14 基线工具复核（D-32）：初始化和更新均先检查验收编号引用，失败时清单保持不变，--force也不跳过该检查。编号出现不证明用例充分、修改顺序正确或业务测试通过。已实现范围为准备/分析辅助脚本与基线工具；业务应用、事务台账、页面和业务自动化测试仍未实现。工具反例验证单独留证，不计入课程业务测试数量。

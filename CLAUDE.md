# 项目规矩

当前更新（2026-10-16 D-39）：现行决定D-18～D-39。A已交付，B与初始建单已有实现；A/B绑定和原因类别筛选两处缺口获准修复。先保存Git中间检查点和验收计划，再冻结TAKT批次（两个gpt-6.1-sol/high、并发2）。具体任务仅修改批准文件，规格/基线由协调会话维护；收工记录由协调会话汇总，worker不得越过文件范围补改日志。修复验收、完整NFR、C人工流程与最终接收按实际结果分别登记。以下旧阶段文字保留，当前以本段和D-39为准。

当前更新（2026-10-15 D-38）：本人批准B＋初始建单＋额度服务及候选质量补充。计划入口scripts/reconcile_data.py，CLI/页面共用reconcile_workbook；通过后再登记。现行决定D-18～D-38；人工C流程、P-04仍后续。A历史提交149440d已包含在云端1c623d4；旧“未推送”属于历史阶段。

实际主要代理：Codex。此文件保留课程打包脚本要求的固定名称；Codex 从 AGENTS.md 进入并读取本文件。

## 当前阶段

模块A已实现并经AI自动测试、独立复验与官方全量核验；包含共享导入服务、SQLite来源/分析快照、R1和质量核验页面。Flask 3.1.3已安装在本项目.venv；B/C、配对/额度台账与工单流程仍待实现。现行决定D-18～D-37，未完成项见《待确认事项》。本轮按实际工作本地提交，未推送；本人复跑与视觉截图待补。

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

应用入口见下面模块A命令；`check_submission.py` 在初始阶段报告缺项并返回失败是正常事实，不能为使它通过而创建虚假材料。
业务测试使用 `.\.venv\Scripts\python.exe -m pytest -q`；当前42项A测试通过，B/C尚未实现，完整课程验收仍待完成。

模块A已验证命令：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/import_data.py --source full
.\.venv\Scripts\python.exe -X utf8 scripts/import_data.py --source performance
.\.venv\Scripts\python.exe -X utf8 scripts/run_app.py
.\.venv\Scripts\python.exe -m pytest -q
```

浏览器打开http://127.0.0.1:5000。首次导入全量需等待，后续同源同规则重导复用；样本与全量库隔离。重制样本用scripts/build_performance_sample.py，该脚本保留已有样本并拒绝无确认覆盖；完整NFR计时器待B/C实现，scripts/measure_module_a.py仅记录A阶段且保留已有输出目录。

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


## 历史阶段说明

下方按日期保留准备与审查过程，其中“业务尚未开始”“只修文档”等只描述当时阶段。当前状态以2026-10-15 D-36为准，不将旧表述用于覆盖已完成的A或宣称B/C完成。

2026-10-14 补充审查已完成：输入类型/数值读取、数量符号、字符与分级碰撞、版本索引和质量归置以《要做什么》DP-03/05/10为准。S1—S11及后续意见的范围判断见 `evidence/reviews/2026-10-14-S1-S11/审查结论.md`。新增合同仍待应用实现和业务验收，本轮只修改文档及保存只读审查证据。

2026-10-14 已补差异单类型及固定来源/工单身份（D-30/D-31）。读《决定记录》时先看状态索引；原待定或旧规则保留为历史，解决登记日期和现行位置写在条目旁。P-01～P-08仍有外部/后续待办，设计解决不等于应用完成。

2026-10-14 基线工具复核（D-32）：初始化和更新均先检查验收编号引用，失败时清单保持不变，--force也不跳过该检查。编号出现不证明用例充分、修改顺序正确或业务测试通过。已实现范围为准备/分析辅助脚本与基线工具；业务应用、事务台账、页面和业务自动化测试仍未实现。工具反例验证单独留证，不计入课程业务测试数量。

2026-10-14 D-33/D-34：用户批准固定样本起点及完整计时协议，开始模块A、对应测试和CLI/页面共享服务。此前“本轮不实现应用/不提交”仅指历史审查阶段；本轮进入实现，按实际完成工作本地存档，远端推送须按用户指示。P-04仍在真实审批演示前确认。

2026-10-15 D-35：用户确认三键预检、类型明确的七列比较及客户/商品规范键例外；文本字段公式/错误新增末级原因。先更新合同再修代码，旧来源与分析保留，规则刷新有差异先报告。

2026-10-15 D-36：P-03样本、A代码/42项测试、独立审查及本机HTTP已交付。全量新旧输出0行变化，来源和旧分析保留；完整NFR、B/C、P-04实际配置及本人复跑/截图未完成。

2026-10-15 D-37：用户批准固定样本受限重建，核对既有哈希再发布；原样本/清单不覆盖，不更换范围。封装时间仅用于复现字节，真实重建回执另记。实际工具反例结果独立保存，不与A业务测试或NFR混算。

# VC-R-03 退货对账与差异处理系统

当前模块A已实现并核验；模块B/C尚未实现。实际运行环境为Python 3.12.10、Flask 3.1.3、SQLite 3.49.1，依赖仅安装在项目.venv。规则合同见《要做什么》《怎么做》《怎么算做对》，现行决定D-18～D-37；开工先执行基线检查。

## 本机运行

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/check_baseline.py
.\.venv\Scripts\python.exe -X utf8 scripts/import_data.py --source full
.\.venv\Scripts\python.exe -X utf8 scripts/import_data.py --source performance
.\.venv\Scripts\python.exe -X utf8 scripts/run_app.py
```

浏览器打开http://127.0.0.1:5000。固定文件按钮与CLI共用ingest_workbook；可查看R1、非商品统计、来源/排除/质量分页和原始明细定位。服务仅绑定本机，关闭调试。首次全量导入需要等待，同源同规则再次导入复用来源和分析；全量与样本使用独立库。

## 重建环境与数据

先安装Python 3.12与Git，在项目根目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
.\.venv\Scripts\python.exe -X utf8 scripts/download_data.py
.\.venv\Scripts\python.exe -X utf8 scripts/build_performance_sample.py
```

原始ZIP/XLSX、派生样本、SQLite库不进入Git；换机重新获取原数据并核对哈希，样本来源映射保存在evidence/performance/sample-v1/。制样采用固定起点和整单贪心，两表各5000行。已有样本及计时目录拒绝覆盖；Git中清单存在而样本缺失时，制样脚本按既有映射受限重建，核验全部原值/类型及原样本字节哈希后发布。封装原时间仅复现字节，真实回执另记；不匹配时停止报告，不重新选样。

## 已完成验证与限制

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -X utf8 scripts/measure_module_a.py
```

模块A当前42项自动测试及独立复验通过；真实全量1067371＝1041455＋25916，非商品保留5830，质量清单229795。D-35新旧全部逐行输出变化0，来源及旧分析保留。9个本机HTTP页面/API核验通过；本人视觉核验与截图待补。

固定样本10000＝9939＋61；五次A阶段最大1.244932秒。A阶段不包含B候选配对及C初始建单，完整NFR-01未执行。全部阶段记录和曾失败制样、独立True/1反例都保留在evidence/，合成测试数据不冒充真实交易。

待完成：模块B/C、R2/R3、额度与派单流程、正式审批及端到端性能；P-04实际人员/权限/管理链在真实审批演示前确认。报告、PPT、本人复跑、跨日记录、真实技能/Hook证据及课程工具等效认定按原计划完成，详见《待确认事项》。

## 证据与收工

- 应用验证：evidence/tests/module-a/、evidence/data/module-a-2026-10-15/。
- 样本及计时：evidence/performance/sample-v1/、module-a-phase/；探索/旧版本/并行资源竞争时的阶段结果单独保留。
- 历史只读分析：evidence/data/review-2026-10-14/；只用于设计核验，不能写成B/C应用结果。
- 按.agents/skills/daily-summary/SKILL.md整理真实日志，并执行scripts/save_daily_log.py导出真实Git提交。提交记录.txt不再进入Git，课程打包直接收录，避免自记录循环。
- 学习指南、纠偏和中期报告素材位于项目外../VC-R-03-学习指南/，不入Git或课程包。本人截图缺失时如实留缺，不生成替代图片。

项目分支setup/data-environment，用户指定远端https://github.com/sssxy-Mr2p5pi/VC-R-03--；本轮仅本地存档，未推送。课程报告固定名大作业报告.docx，中期PPT名中期汇报.pptx；提交前用scripts/check_submission.py检查机械缺项。当前缺项不会因A完成而自动消失。


2026-10-15 D-37实际补充：隔离新检出工作区受限重建成功，10000行原值/类型及原字节SHA-256相同，清单不变、原样本未动；再次执行拒绝覆盖。新增7项制样工具反例单列，pytest合计49项（A业务/输入42＋制样工具7）、独立复验49项均通过；NFR-01仍未执行。证据见evidence/performance/sample-rebuild/与evidence/tests/module-a/pytest-with-rebuild.xml、independent-with-rebuild.xml。

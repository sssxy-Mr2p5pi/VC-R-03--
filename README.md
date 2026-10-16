# VC-R-03 退货对账与差异处理系统 · 使用说明

对账、差异工单与人工处理系统的本机运行说明。规格合同见《要做什么》《怎么做》《怎么算做对》，现行决定 D-18～D-39；开工先执行基线检查。

实现状态：A（导入核验）、B（配对与初始异常建单）、两处已批准修复（A/B 绑定保护、原因类别筛选）、C（人工流程：派单/行结论/关闭/复核/释放/冲销/重开/指标）均已实现；自动测试 106 项通过，真实 HTTP 全链路走查（56 项断言）通过。完整性能验收（NFR-01 固定样本五跑）结果见第五节。

## 一、启动

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\run_app.py
```

浏览器打开 http://127.0.0.1:5000 。服务仅绑定本机、无登录页；处理页顶部用“教学账号”下拉切换身份。

## 二、数据与库

- `data/raw/online_retail_II.xlsx`：官方原始数据（不入 Git；换机用 `scripts/download_data.py` 重取）。
- `data/processed/app-full.db`：全量库（真实全量结果；首次导入需较长时间）。
- `data/processed/app-performance.db`：固定 10,000 行性能样本库（演示推荐）。
- 两个来源相互独立；顶部导航在“官方全量 / 性能样本”之间切换。
- 建库/重建：打开对应来源 → 点“导入并核验固定文件” → 点“运行对账并生成初始异常单”；重复执行幂等，不会重复占量或重复建单。
- 重置样本演示：关闭应用后备份并删除 `app-performance.db`，回到首页重新导入+对账。

## 三、页面一览

| 页面 | 地址 | 用途 |
|---|---|---|
| 首页 / 导入核验 | `/` | 导入、对账、R1 验算、非商品统计、来源行/排除/质量清单 |
| 配对结果 | `/matches` | 三分类、主因、原因类别筛选、分页、导出结果/候选 CSV |
| 异常工单 | `/tickets` | 工单类型筛选、导出、点“处理”进入处理台 |
| 处理详情 | `/workflow/ticket/<工单ID>` | 派单、行结论、关闭、复核、释放、冲销、待批审批、事件与归属历史 |
| 处理指标 | `/metrics` | 自动配对被冲销、复核通过/否决、未复核数量、等待时长 |

## 四、人工处理怎么用（教学账号）

**责任人或权限不符时系统会拒绝操作（HTTP 409）——这是权限与回避规则的现场演示。** 复核人≠提交人；同业务角色仅管理链上级可复核；批准人≠当前责任人或配对产生者；产生者不能自批。

教学账号（演示夹具、非真实人员；真实账号与管理链配置不在本项目范围）：

| 账号 | 名称 | 业务角色 | 权限 | 上级 |
|---|---|---|---|---|
| manager_sun | 孙经理（教学） | MANAGER | 派单 / 复核 / 批准释放 / 批准冲销 | — |
| supervisor_zhao | 赵主管（教学） | SUPERVISOR | 全部 | manager_sun |
| finance_li | 李财务（教学） | FINANCE | 无 | supervisor_zhao |
| finance_chen | 陈财务（教学） | FINANCE | 复核 | supervisor_zhao |
| warehouse_wang | 王仓库（教学） | WAREHOUSE | 无 | supervisor_zhao |
| warehouse_lead | 仓库组林组长（教学） | WAREHOUSE | 复核 | manager_sun |

推荐演示路径（以样本库为例）：

1. 切到 `supervisor_zhao`，把工单派给 `warehouse_wang`；
2. 切到 `warehouse_wang`，逐行处理：选“配对（选一条候选）”或“不配对（需说明）”，可先“存草稿”再“提交”；配对提交即占原单额度；
3. 全部待办行有结论后“关闭工单”（仍有未结待办会被拒绝）；
4. 切到 `finance_chen` 逐行复核：“通过”只追加意见；“否决”触发冲销并自动开新轮次（已关闭单回到待处理）；
5. 释放演示：把工单派给 `supervisor_zhao` 本人 → 申请释放 → 本人批准被拒（回避）→ `manager_sun` 批准 → 工单回到待派；
6. 冲销演示：有配对的行“提交冲销申请” → 产生者自批被拒 → 有审批权者批准 → 追加反向流水、额度恢复。

## 五、验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m ruff check --no-cache src tests scripts
.\.venv\Scripts\python.exe -X utf8 scripts\check_baseline.py
```

NFR-01 固定样本五跑（A→B→初始建单，2026-10-16）：**通过**——连续五次最大值 1.330402 秒、中位数 1.319027 秒，五次输出一致；全部运行记录与结果见 `evidence/performance/nfr-01/`。

## 六、已知限制

- 无登录认证，账号切换仅用于教学演示；真实账号/管理链配置不在本项目范围。
- 性能样本库保存一次完整走查后的状态（部分工单已处理/关闭/重开，属正常演示数据）。
- 源移除检测、批量复核/冲销等首版明确暂缓，见《待确认事项》。

## 七、重建环境（换机）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
.\.venv\Scripts\python.exe -X utf8 scripts\download_data.py
.\.venv\Scripts\python.exe -X utf8 scripts\build_performance_sample.py
```

原始 ZIP/XLSX、派生样本、SQLite 库不进入 Git；样本来源映射与哈希见 `evidence/performance/sample-v1/`；Git 中清单存在而样本缺失时，制样脚本按既有映射受限重建并核验字节哈希，不重新选样。当前代码提交在 `DP_Preview` 分支（未推送）；验收证据与收工记录见 `evidence/`。

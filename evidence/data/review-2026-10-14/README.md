# 2026-10-14 规则设计验证

这里保存真实只读分析；没有建立业务数据库、正式配对台账或工单。

- rule_input_audit.json：两表原始输入、七列整单重复、排除并集、编码及价格精度。
- batch_validation.json：全量规则干跑、分类守恒、价差和额度需求、32条候选交叉检查。
- batch_line_results.csv：17,973条商品取消行的分类、主原因/标签、来源位置及唯一候选引用。多候选在此证据中保留数量，完整候选页面仍待应用实现。
- document_checks.json：FR/TC/DP编号、用例覆盖、讲义本地链接、CSV汇总及脚本哈希检查。
- verification.json：最终只读脚本规范检查与本轮记录生成结果。

复现方法（在项目根目录，输出改用新的证据目录，避免覆盖本次记录）：

```powershell
.\.venv\Scripts\python.exe -X utf8 .\scripts\audit_rule_inputs.py .\data\raw\online_retail_II.xlsx <新目录>\rule_input_audit.json
.\.venv\Scripts\python.exe -X utf8 .\scripts\batch_validate_rules.py .\data\raw\online_retail_II.xlsx <新目录>
```

新目录占位符需替换成实际路径。首次探索reconciliation_profile.json保留原样；其旧口径与本次结果分别标明，不能混用。
价格差统计没有真实归属标签，不证明非零价差合法。全量分析耗时不代替10,000行端到端性能验收；这里的检查不计入课程业务自动化测试。

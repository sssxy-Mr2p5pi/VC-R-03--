# 只读补充审查证据

先读[审查结论](审查结论.md)。JSON为源特征和旧结果的只读交叉核验，Python文件为本轮检查工具，均不属于业务应用实现。
重跑时在项目根目录指定源文件与新的输出目录，避免覆盖这份记录：

```powershell
.\.venv\Scripts\python.exe -X utf8 .\evidence\reviews\2026-10-14-S1-S11\inspect_numeric_source.py .\data\raw\online_retail_II.xlsx <新目录>\numeric_source_inspection.json
.\.venv\Scripts\python.exe -X utf8 .\evidence\reviews\2026-10-14-S1-S11\inspect_key_collisions.py .\data\raw\online_retail_II.xlsx <新目录>\key_collision_inspection.json
```

数值检查同时读取既有`evidence/data/review-2026-10-14/batch_line_results.csv`，只核对其中唯一候选的价格相等判定；未重跑完整业务匹配。必须显式传入源文件及输出路径，且保留原项目目录结构。
文档编号、证据哈希及未改动文件清单见document_verification.json；写回检查与当天实际提交导出见publication_verification.json。应用业务测试数为0，不将这些检查计入课程业务测试。

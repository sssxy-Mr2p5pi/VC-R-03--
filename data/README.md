# 数据说明与当前状态

来源：UCI Online Retail II，Chen, D. (2012)，DOI https://doi.org/10.24432/C5CG6D，CC BY 4.0。
官方页：https://archive.ics.uci.edu/dataset/502/online+retail+ii 。

原始文件位于 raw/，不改写且不进入Git。来源、首次下载时间、字节数、校验值记录在 `../evidence/data/download_manifest.json`。
完整只读统计在 `../evidence/data/raw_profile.json`；各表前五行带工作表/Excel行号的真实预览在 raw_preview.csv。

2026-10-13实测：Year 2009-2010有525,461行；Year 2010-2011有541,910行；合计1,067,371行。
实际表头为 Invoice、StockCode、Description、Quantity、InvoiceDate、Price、Customer ID、Country。
课程所列InvoiceNo、UnitPrice、CustomerID需要按实际文件映射。原始文件首行是表头，不算数据行。

两表日期范围在2010年12月有交集；此事实只表示时间范围重叠，不证明具体行重复。当前尚未实施去重、清洗、切片、配对或构造第二套账。
processed/暂空；两边数据、样本和处理口径确认后再生成派生文件。

原始文件丢失或新克隆后，运行 `scripts/download_data.py` 可按记录恢复：缺失文件从官方重新获取或从已核实ZIP提取，校验通过后补齐；已有但校验不符的文件拒绝覆盖。
首次来源清单保留，恢复另写 source_recovery_history.json。不要把恢复时间当作首次取得时间。

# 原始财报评测

此目录将真实 PDF、问题、候选答案、模型推理和独立评分分开。模型只收到题目 id、question、category、split；gold.jsonl 和 PDF 原页标注只供评分及人工审核。

本目录保留历史实验的独立评分、PDF来源核验和记录重放；旧实时消融执行入口已停用。现行生成统一使用云端，真实问答入口见[当前评测说明](../README.md)。BGE和既有索引不变，数据准备仍检查隔离数据库与索引位置。

## 数据文件与边界

默认目录为 `data/runtime/real_validation`：

- `report_manifest.json`：预选 PDF 清单，含 `documents`；每条保留公司、报告期、原件路径、SHA-256、开发/保留分组。
- `questions.jsonl`：每条只允许 `id/question/category/split`，不混入答案、摘录、标注或模型提示。
- `gold.jsonl`：同一组题号的独立候选标注。数值和来源必须带 `stock_code/report_year/report_period/source_path/source_sha256/page/excerpt`；数值额外需要公司名、指标字段、值和单位。页码从 1 开始，指 PDF 物理页。

支持 `numeric/chart/citation/security/out_of_scope`，报告期支持 FY、HY、Q1、Q3。金额支持元、万元、亿元；EPS 支持元/股和明确 EPS 语境的元。独立评分不调用项目 verifier，不查已入库数值来生成真值，也不调用另一个模型裁判。

`label_status=machine_candidate` 的数字是**与机器候选的一致性**，尚不能称为人工确认的准确率。提取失败标 `annotation_missing`：照常运行并保留在预选分母中，依赖候选真值的分数为 N/A。可加 `--require-human-labels`，只有全部选中标签具备人工 reviewer/reviewed_at 时才运行。

`prospective_holdout` 仅表示此次冻结后的保留组。历史调试曝光未知，不能声称过去从未使用过。请在开发组完成通用修复后冻结代码，再运行保留组；不得按保留组答案调规则。

## 历史记录重放

以下旧实时执行步骤仅描述历史方法，不作为当前云端启动说明。`run_eval.py`只接受`--rescore`重放，不能用它新建问答实验；新问答使用`eval/cloud_focused.py`。问题、gold和原件证据的分离规则继续有效。

历史实验的模型身份、数据、参数和逐题结果只从对应快照解释，不改写为当前DeepSeek版本。本目录不再提供旧实时启动命令；保留下面的只读复评分入口。现行28题的启动、续跑与真实Java/SSE检查见[云端定向测试](../../docs/CLOUD_FOCUSED_TEST.md)。

## 复评分与材料

每次运行保存完整 `report.jsonl`，包括嵌套回答、引用、SQL 结果、图表、事件和不含鉴权头的 HTTP 诊断。另存问题/标注/清单快照、SQL 快照、完整索引内容与向量哈希、源码哈希、模型摘要、服务版本、参数和硬件信息。`run_status.json` 标明计划覆盖、运行状态及磁盘复评分校验。

```powershell
& .venv/Scripts/python.exe eval/real/run_eval.py --rescore eval/runs/real-development-agent-v1/report.jsonl --output eval/runs/real-development-agent-v1-replay
```

复评分只读已存记录与原 PDF，不启动模型、不查数据库。它核对题目与标注指纹、完整计划覆盖及 PDF 原页；完整运行如果缺失 JSONL 记录会直接拒绝。引用评分要求同一原件路径、公司、报告期、PDF 哈希、物理页和该页原文，生产 `document_id/document_version` 的组合哈希不会被误当作 PDF 哈希。OCR 与 PDF 文字层不能对应时保守判未通过，留人工检查，不放宽为“有一个链接就通过”。

每个运行目录还会输出：

- `manual_review.jsonl`：解释审核队列；reviewer、supported、细项保持空值，答案以 SHA-256 绑定。
- `user_study_tasks.jsonl`：不带答案的参与者任务。
- `user_study_results.template.jsonl`：空白匿名用户研究记录，没有自动填入参与者、耗时或结论。
- `REVIEW_INSTRUCTIONS.md`：候选真值审核、解释审核及交错分配研究条件的操作说明。

人工完成审核后另存文件，再将其导入复评分：

```powershell
& .venv/Scripts/python.exe eval/real/run_eval.py --rescore eval/runs/real-development-agent-v1/report.jsonl --manual-reviews data/runtime/real_validation/reviews.completed.jsonl --output eval/runs/real-development-agent-v1-reviewed
```

单次小样本与自动候选一致性不能证明充分创新、完整泛化或真实用户收益。解释支持率和用户研究结论只有在相应人工活动实际完成后才可填写。

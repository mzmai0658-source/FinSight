# 原始财报评测

此目录将真实 PDF、问题、候选答案、模型推理和独立评分分开。模型只收到题目 id、question、category、split；gold.jsonl 和 PDF 原页标注只供评分及人工审核。

默认使用本机 Ollama 的 `qwen3.5:9b-q4_K_M`，`reasoning_effort=low`，BGE 使用本地缓存并在 CPU 运行。运行器会拒绝远程模型端点、远程数据库、原项目数据库和合成演示库；只接受 `finsight_real_eval` 与 `data/real_eval_chroma_db`。

## 数据文件与边界

默认目录为 `data/runtime/real_validation`：

- `report_manifest.json`：预选 PDF 清单，含 `documents`；每条保留公司、报告期、原件路径、SHA-256、开发/保留分组。
- `questions.jsonl`：每条只允许 `id/question/category/split`，不混入答案、摘录、标注或模型提示。
- `gold.jsonl`：同一组题号的独立候选标注。数值和来源必须带 `stock_code/report_year/report_period/source_path/source_sha256/page/excerpt`；数值额外需要公司名、指标字段、值和单位。页码从 1 开始，指 PDF 物理页。

支持 `numeric/chart/citation/security/out_of_scope`，报告期支持 FY、HY、Q1、Q3。金额支持元、万元、亿元；EPS 支持元/股和明确 EPS 语境的元。独立评分不调用项目 verifier，不查已入库数值来生成真值，也不调用另一个模型裁判。

`label_status=machine_candidate` 的数字是**与机器候选的一致性**，尚不能称为人工确认的准确率。提取失败标 `annotation_missing`：照常运行并保留在预选分母中，依赖候选真值的分数为 N/A。可加 `--require-human-labels`，只有全部选中标签具备人工 reviewer/reviewed_at 时才运行。

`prospective_holdout` 仅表示此次冻结后的保留组。历史调试曝光未知，不能声称过去从未使用过。请在开发组完成通用修复后冻结代码，再运行保留组；不得按保留组答案调规则。

## 运行

先完成对应报告的真实 ETL 和索引导入，配置 `.env.real-eval` 的独立只读 SQL 账号。运行器在推理前检查所选分组的每一份 PDF 都有 SQL 报告身份及携带原件哈希的索引块；库中允许存在清单内的其他分组。缺失报告会写入预检失败记录，不能静默略过。

```powershell
$env:PYTHONIOENCODING='utf-8'
& .venv/Scripts/python.exe eval/real/run_eval.py --split development --modes agent --output eval/runs/real-development-agent-v1
```

五模式使用相同本地模型、数据和 0 温度；只变动运行链路。模式在相邻题之间交替顺序，避免始终让同一模式先跑。默认输出预算、上下文和重试次数来自 `.env.real-eval` 并进入运行快照；模型和索引预热不计入逐题耗时。

```powershell
& .venv/Scripts/python.exe eval/real/run_eval.py --split development --modes bare tool_baseline agent no_verifier no_rerank --output eval/runs/real-development-five-modes-v1
& .venv/Scripts/python.exe eval/real/run_eval.py --split prospective_holdout --modes agent --output eval/runs/real-prospective-holdout-agent-v1
```

使用新输出目录，不覆盖旧记录。传输断连、超时或流式连接中断会立即保存已完成题、故障题和已经发出的工具证据，然后终止整个批次；恢复服务后另开运行目录。HTTP 500 等服务器响应另保留状态和错误体，避免规则兜底把模型接口故障掩盖成正常模型表现。

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

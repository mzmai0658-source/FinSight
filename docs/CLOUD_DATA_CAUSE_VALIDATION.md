> 历史版本资料：当前安装、模型和数据以 [INSTALL_CURRENT.md](INSTALL_CURRENT.md) 为准；本文旧期间/旧成绩不代表本次交付。

# 数据与经营原因修复后的200轮验证

本轮在隔离候选中继续第2、3、5项。现用主目录的数据库、服务与旧117份资料不切换，不更新GitHub。生成仍为DeepSeek Flash，向量为DashScope text-embedding-v4 / 1024，新OCR采用PaddleOCR-VL-1.6；没有更换问答提示词来调题。

## 本轮处理

针对OCR合并标题、集团与财务子公司表格、年度列及附注列、表内期间切换修复提取程序。经营原因支持“指标、数值、原因”表格，并按真实PDF同一行、列头期间与局部范围核对；分部原因不能充当集团原因，母公司与合并不能互借。文字或表格边界不能确认的内容仍保留缺项。

三一重工2025三季报的17页新OCR已完成，保留远端作业号和缓存摘要。一季报此前提交结果不确定，仍不重新付费提交；该报告作为显式缺口保留。

完整重提取、索引与发布的实际数量以候选的`data/runtime/submissions/ocr_trial/current.json`及对应版本目录的manifest/index/publication为准；发布后仍是experimental / relaxed_ocr，不代表所有数字人工审完。

2026-10-23已发布隔离候选：119份报告、7607条事实（5554条程序核验通过、1400条待核验、653条派生）、21437个片段。用户已确认不追求100%指标覆盖，少量缺项如实保留，不再追加提取修复。4轮真实Java/SSE入口检查已完成、保存并对照原件复核；不能据此宣称200轮通过。

## 运行固定200轮

在Windows PowerShell执行，不需要安装`pwsh`。私有配置和密钥已保存在隔离候选中，不要写到命令参数。

```powershell
Set-Location 'D:\data_discovery\.local_runtime\upload_cloud120_20261021\candidate'
$py = 'D:\data_discovery\.venv\Scripts\python.exe'

# 启动或复用本轮隔离应用，不处理8000/8080日常服务。
& $py -X utf8 -u scripts/financial_upload_upgrade.py start

# 同一目录续跑尚未完成的题目，原有首次结果保留。
& $py -X utf8 -u scripts/financial_upload_upgrade.py evaluate --output 'D:\data_discovery\.local_runtime\rollout_235_20261022\evaluation\final200'
```

本轮发布与小型入口检查通过后再执行上述正式测试。若服务刚启动，等待Python8120、Java18280就绪再运行问答。若提示当前源码、版本、索引或配置与冻结记录不同，应停止检查差异，不能删冻结记录来强行续跑。

首次原件卡准备会重新读取PDF及坐标，可能有较长时间没有题目进度；此时尚未开始模型答题，不是在重新OCR或补齐全部指标。已有原件卡将按摘要检查后复用。不要同时在同一输出目录启动两份准备或评测进程。

只准备原件卡而不登录或调用生成、向量接口：

```powershell
& $py -X utf8 -u scripts/financial_upload_upgrade.py evaluate --prepare-only --output 'D:\data_discovery\.local_runtime\rollout_235_20261022\evaluation\final200'
```

## 怎样看结果

输出目录为上面`final200`：

- `answers.html`：每道问题、完整回答、预期及检查结果。
- `records.jsonl`、`answers/`：原始首次结果，不用后续成功覆盖。
- `preparation.json`：原件卡覆盖，区分未提取、审核未完成与独立核验失败。
- `oracle.json`、`freeze.json`：原件预期及固定的代码、模型、资料、索引身份。
- `scores/`、`summary.json`：自动检查及统计。原因和概念的正文仍需本助手独立逐项复核，待复核不能算通过。

题库保持原200轮：120个单问、20组四轮对话，开发120轮、项目内保留80轮；覆盖10家公司。现有原件和数据修复不使这些公司成为完全未见样本，不宣称第三方盲测。不设及格线、不删失败题、不用Agent自报“通过”替代评分。

缺数据、未核验与答错分开记录；200题总分母保留。程序测试、原件离线回放和模型端到端问答是不同证据，不能拼成一个准确率。

本轮本机证据：`D:/data_discovery/.local_runtime/rollout_235_20261022/`。主目录与旧候选备份、首次失败、修复反例和新版本均保留。尚未完成正式200轮及正文复核前，没有本轮最终准确率。

> 历史版本资料：当前安装、模型和数据以 [INSTALL_CURRENT.md](INSTALL_CURRENT.md) 为准；本文旧期间/旧成绩不代表本次交付。

# 真实财报运行前提

> 2026-10-22主目录已采用新118份云端试验问答库，旧117份/BGE保留。主目录使用`.env`，本文`.env.upload120`命令属于独立重建工具；主目录启动及切换见[主项目运行说明](CLOUD118_MAIN_RUN.md)，实际数据与网页结果见[本轮记录](ROLLOUT_146_20261022.md)。

当前生成统一经DeepSeek `deepseek-flash`云端执行，temperature=0、thinking disabled、max_tokens=3072、单次timeout=60秒、传输默认额外重试=0。新PDF经远端PaddleOCR-VL-1.6识别，叙述使用百炼`text-embedding-v4`1024维向量；MySQL保存财务事实与业务状态，Chroma保存向量，Redis承担令牌与配额，RabbitMQ调度异步任务，Java/Vue提供用户入口。没有Ollama或Qwen运行依赖，也不以BGE作为云端失败回退。

## 数据与配置前提

本轮处理10家新公司的120份真实原件，其中118份已有本轮新OCR。原件按SHA-256、公司、期间和物理页登记；候选数值区分`verified`、`unverified`和`derived`，派生关系不能消除输入的未核验状态。具体版本由`data/runtime/submissions/ocr_trial/current.json`指定，其事实和向量验收分别留存。当前OCR试验不代表全部单元格完成原件核验。

默认选择已确定为新118份云端试验库，私有配置使用`FINSIGHT_OCR_TRIAL=true`，状态保留`experimental / relaxed_ocr`和`source_audit_complete=false`。旧117份/BGE作为独立历史库保存，不与新默认混查。主目录迁移、实际启动配置和真实页面验收是否完成，按本轮最终记录核对。

首次环境与依赖见[安装说明](REAL_DATA_INSTALL.md)，云端变量与独立SELECT账号见[部署说明](DEPLOYMENT.md)。密钥仅在私有`.env.upload120`或部署方自己的环境文件中；模板留空。账号端点按实际地区配置，服务失败保留明确错误，不切换其他模型。只下载代码不能取得原件、OCR、来源资产或完整数据包。

## 本轮启动和续跑

在已配置的本轮目录执行，`$py`替换为实际Python解释器的绝对路径：

```powershell
$py = 'D:/data_discovery/.venv/Scripts/python.exe'
& $py -X utf8 scripts/financial_upload_upgrade.py start
& $py -X utf8 scripts/financial_upload_upgrade.py check
& $py -X utf8 scripts/financial_upload_upgrade.py status
```

当前Python编排可在Windows PowerShell 5.1执行，启动与恢复仅处理本轮隔离应用；详细端口、前端启动和首次配置见[升级运行说明](FINANCIAL_UPLOAD_UPGRADE.md)。已配置后修改私有值，用`financial_upload_upgrade.py restart`加载；有执行中上传时脚本会阻止重启。`check --probe-cloud`会调用一次生成和一次向量服务，普通预检及配置就绪不能替代完整问答验收。

OCR作业号和页数预留持久保存，恢复时续查已有作业，避免重复付费提交。新OCR生成结果可复用；仍需核对PDF/OCR摘要、模型身份、期间、单位、报表范围与来源。云端向量只按相同指纹、文本和元数据复用。完整命令见[OCR试验说明](UPGRADE120_OCR_TRIAL.md)。

## 严格发布与试验指针

普通上传经管理员确认身份、审核和严格发布后进入`serving`。本轮默认使用明确放宽的`ocr-trial-v1`配置：`FINSIGHT_OCR_TRIAL=true`读取独立`ocr_trial`指针，保留`experimental / relaxed_ocr`、`source_audit_complete=false`和未完整核验提示；上传页原有失败与待审核记录继续保留。试验中的可读数值、图表和原页接通不能写成管理员已经逐项审核完成。

主动退出试验时在私有配置中设`FINSIGHT_OCR_TRIAL=false`，再执行`financial_upload_upgrade.py restart`读取同一数据库的严格`serving`指针；它不会自动恢复旧117份/BGE。使用旧库需同时选择旧数据库、资料目录、BGE模型和旧索引。新版本先准备再切换，失败保留旧指针。刷新和SSE断开继续后台任务，用户主动停止由取消接口处理。任务预算与保存恢复见[v3接口](FINSIGHT_V3_API.md)。

## 历史资料与验收边界

原117份财报及BGE空间继续本地保留，使用时显式选择历史模型、索引目录和对应发布版本。其资料包重建、28轮、200轮、264轮和324轮记录仅支持各自数据、模型及代码范围，不能计入本轮成绩；见[历史材料说明](HISTORICAL_MATERIALS.md)。旧OCR缓存说明在[历史缓存章节](OCR_CACHE_USAGE.md)。

更广泛的新口语题评测暂缓，本轮不预填新准确率。软件测试、OCR覆盖、索引验收、真实Java/SSE接通、浏览器交互和独立答案评分分开报告。正式问答需要真实登录、SSE与实际保存的上一轮状态；原因和概念正文未经独立复核不能直接记通过。技术报告与PDF以最终核对后的本轮版本为准。

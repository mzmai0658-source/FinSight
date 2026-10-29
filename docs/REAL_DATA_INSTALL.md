> 历史版本资料：当前安装、模型和数据以 [INSTALL_CURRENT.md](INSTALL_CURRENT.md) 为准；本文旧期间/旧成绩不代表本次交付。

# 真实资料安装与重建

> 2026-10-22主目录已采用新118份云端试验问答库，旧117份/BGE保留。主目录使用`.env`，本文`.env.upload120`命令属于独立重建工具；主目录启动及切换见[主项目运行说明](CLOUD118_MAIN_RUN.md)，实际数据与网页结果见[本轮记录](ROLLOUT_146_20261022.md)。

当前入口采用DeepSeek `deepseek-flash`云端生成、远端PaddleOCR-VL-1.6识别和百炼`text-embedding-v4`1024维向量，本机运行Python/LangGraph、Java、Vue、MySQL、Chroma、Redis与RabbitMQ。旧117份/BGE安装路线保留在本文后半部分，并明确属于历史兼容模式。

本轮默认已选择新118份云端试验库，旧117份/BGE单独保留。新默认使用`FINSIGHT_OCR_TRIAL=true`、`ocr-trial-v1`和独立`ocr_trial`指针，仍为`experimental / relaxed_ocr`；安装和默认切换不代表118份全部严格核验完成。主目录资料迁移、实际配置与页面验收按最终执行记录确认。

## 当前云端上传路线

先准备Windows、Python 3.11、Node.js、JDK/Maven和可用的MySQL、Redis、RabbitMQ。安装代码锁定的依赖，不下载Ollama、Qwen或BGE权重：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
Push-Location frontend
npm ci
npm run build
Pop-Location
Push-Location backend-java
mvn package
Pop-Location
```

云端服务需要自己的账号、网络与私有密钥。当前配置为`LLM_PROVIDER=openai_compat`、`LLM_MODEL=deepseek-flash`，以及`EMBEDDING_PROVIDER=dashscope`、`EMBEDDING_MODEL=text-embedding-v4`、`EMBEDDING_DIMENSION=1024`。OCR配置为`OCR_API_PROTOCOL=paddle_async`和`OCR_MODEL=PaddleOCR-VL-1.6`；默认试验库配置为`FINSIGHT_OCR_TRIAL=true`。实际账号端点与密钥由配置器保存到被Git忽略的环境文件，不放进命令、报告或前端。完整变量见[部署说明](DEPLOYMENT.md)。

本轮隔离目录通过Python编排配置和启动，可在Windows PowerShell 5.1执行，不要求`pwsh`。以下使用本机已有解释器；独立安装时将`$py`替换为本目录`.venv`的绝对路径，提供自己的配置源文件：

```powershell
$py = 'D:/data_discovery/.venv/Scripts/python.exe'
& $py -X utf8 scripts/financial_upload_upgrade.py configure --source-env 'D:/data_discovery/.env'
& $py -X utf8 scripts/financial_upload_upgrade.py init
& $py -X utf8 scripts/financial_upload_upgrade.py start
& $py -X utf8 scripts/financial_upload_upgrade.py check
```

已有私有`.env.upload120`时保留原文件，不反复运行配置覆盖账号。`init`建立隔离数据库与独立SELECT账号；需要相应建库、建表或授权权限。首次Java迁移后可再执行`init`补资料表只读权限。各副本使用独立任务库、端口和向量目录，本轮端口与路径见[升级运行说明](FINANCIAL_UPLOAD_UPGRADE.md)。

原件先经真实用户上传入口，随后生成新OCR、身份提案、数值候选和向量。管理员确认身份并审核后，再发布严格通过的子集；未核实、冲突与缺失分别保留，不能补零。新120份原件及118份新OCR的现状由版本清单记录，不能把源码安装、OCR完成或索引接通当作全部财务值已核实。完整流程与批量命令见[升级运行说明](FINANCIAL_UPLOAD_UPGRADE.md)，明确放宽的试验配置见[OCR试验说明](UPGRADE120_OCR_TRIAL.md)。

只下载代码不能获得真实原件、OCR、已发布事实或现成向量。新资料包独立分发与无缓存全新环境安装不在本轮已完成证据中；现有本机缓存复用应如实记录。历史`demo/`合成数据不作为当前安装或验收入口。

## 历史117份/BGE兼容模式

以下说明仅对应旧10家公司、117份真实财报及匹配的本地BGE空间。需显式配置`EMBEDDING_PROVIDER=bge_local`、`EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5`、原`CHROMA_DB_PATH`与对应数据版本，不能用于新云端向量集合。只迁移生成模型时可保留这套资料，不需要重新初始化。

首次安装需要候选源码、三个真实资料附件及 `PACKAGE_SET.json`。这些资料此前在本地准备；GitHub 更新、云盘公开链接、资料分享范围和下载核对不能从本地打包推断已完成。只下载源码不能取得整批真实原件。历史 `demo/` 合成数据不作为本轮安装入口。

### 历史路线的环境与依赖

准备 Windows、Python 3.11、PowerShell 7、Node.js、JDK/Maven，以及实际 MySQL、Redis、RabbitMQ。另需可访问 DeepSeek 的网络、用户自己的服务账号与 API 密钥；不需要 Ollama 或本地生成模型。BGE 首次使用需下载权重，已有完整模型缓存可以复用，但不能称为无缓存下载验证。

在项目根目录使用 PowerShell 7：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
Push-Location frontend
npm ci
npm run build
Pop-Location
Push-Location backend-java
mvn package
Pop-Location
Copy-Item .env.example .env
```

已有私有 `.env` 时保留原文件并逐项更新，不重复覆盖。网络镜像、模型与构建缓存的复用按实际情况记录。填写[云端配置](DEPLOYMENT.md)：`LLM_PROVIDER=openai_compat`、`LLM_BASE_URL=https://api.deepseek.com`、`LLM_MODEL=deepseek-flash`、自己的 `LLM_API_KEY`、`LLM_TIMEOUT_SECONDS=60`、`LLM_MAX_RETRIES=0`、`LLM_MAX_TOKENS=3072`。temperature=0 和 thinking disabled 由代码固定。旧 `DEEPSEEK_*` 仅在对应 `LLM_*` 缺失时兼容，不存在本地生成回退。

填写自己的 MySQL 导入账号及不同的 `SQL_DB_USER / SQL_DB_PASSWORD`。导入账号需要创建表和写入候选的权限，问答账号只允许当前数据库 SELECT。`--create-database` 和 `--create-reader` 需要相应管理权限，不能使用问答账号初始化。数据库、Redis、RabbitMQ 与服务密钥均不随附件分发。

### 历史资料包的两条安装路线

需要的 ZIP 与 `PACKAGE_SET.json` 放在同一目录。路线 A 需要原件包和配套包，OCR 包可不下载；路线 B 需要三个包。目录可带空格，不修改包清单或摘要来绕过校验。这些命令用于新的安装空间，不用于仅切换云端生成模型。

快速初始化A导入此前通过原件程序检查的事实，重新核验来源并建立匹配的本机BGE向量；不表示成员人工逐格复核：

```powershell
.\.venv\Scripts\python.exe scripts\prepare_real_data.py --env .env --packages "D:\资料目录\PACKAGE_SET.json" --mode quick --create-database --create-reader
```

完整重建 B 重新从原件与 OCR 缓存抽取数字、读取 OCR 正文并生成本机向量：

```powershell
.\.venv\Scripts\python.exe scripts\prepare_real_data.py --env .env --packages "D:\资料目录\PACKAGE_SET.json" --mode rebuild --create-database --create-reader
```

两条路线先校验 ZIP、每个文件摘要、目录安全及版本，再登记报告。B 不把配套事实当作重新抽取结果，不复用旧正文或旧向量。定位标注提供抽取与核验依据，值仍读取真实单元格。索引由 OCR 正文分块生成，记录物理页、文件版本和 OCR 摘要；PDF 用于核对身份、数字和最终摘录。

事实、原件、指标、单位、期间、派生公式、索引覆盖和兼容投影通过检查后，才在同一事务中切换发布指针。失败保留上一版本；未核实指标排除、不补零。同版本重复运行不重复事实，已有向量按身份去重；首次运行与幂等运行的向量数分别记录。成功后检查 `data/runtime/real-rebuild/INSTALL_RECEIPT.json`、`acceptance.json` 及 `index_coverage.json`。旧安装记录不能代替本次安装成功的证据。

### 历史路线的启动、停止和上传

```powershell
.\.venv\Scripts\python.exe scripts\runtime_env.py --env-file .env
.\.venv\Scripts\python.exe scripts\preflight.py --env .env
pwsh -NoProfile -File scripts\start-finsight.ps1 -EnvFile .env
pwsh -NoProfile -File scripts\stop-finsight.ps1
```

先准备 RabbitMQ vhost/权限，确认 Redis 数据库号、云端服务配置及 MySQL 只读权限。预检实际检查协议；云端预检需要服务请求，不表示整批问答已通过。统一启动在 Java 迁移后补齐财报正文表只读权限；单独启动 Java 后需运行 `python scripts/ensure_real_reader.py --env .env`。停止仅处理项目启动记录中的应用进程，不关闭共享数据库或消息服务。

管理员上传财报进入 v3 候选链：同内容原件去重并检查现用索引，不同文件版本需重新抽取、索引、核验及发布。定位资料不适用于新版本时阻断发布并报告缺项，不能把旧宽表写入成功当作问答已能使用新报告；当前不自动扩展公司或报告期范围。

无缓存 OCR 路线 C 需自行配置兼容识别服务。此前仅对一份 10 页真实季报验证远端识别，恢复 28/46 条事实且值一致、缺 18 条；这是历史导入验证，不能据此宣称 117 份无缓存全量重建或云端问答通过。优先复用已核验缓存；新识别结果仍需核验，不补零或复制旧值。见[OCR 缓存说明](OCR_CACHE_USAGE.md)。

多副本分别使用自己的`frontend/node_modules`、Vite缓存、任务库与运行目录。以上安装记录及28轮等历史问答分别保留版本，不属于本轮新120份语料的正式成绩。当前报告按本轮真实证据更新，旧材料边界见[历史材料说明](HISTORICAL_MATERIALS.md)。

> 历史版本资料：当前安装、模型和数据以 [INSTALL_CURRENT.md](INSTALL_CURRENT.md) 为准；本文旧期间/旧成绩不代表本次交付。

# 主项目运行：118份云端试验问答库

2026-10-22本机默认选择已经合入主项目。工作目录为`D:/data_discovery`，正常启动读取私有`.env`。生成模型为DeepSeek `deepseek-flash`，OCR为PaddleOCR-VL-1.6，向量为百炼`text-embedding-v4`、1024维；不需要Qwen、Fin-R1或Ollama。

## 数据与发布范围

- 默认问答指向`ocr_trial`：118份真实报告、10家公司、7298条事实、21418个向量片段。`FINSIGHT_DATA_PROFILE=ocr-trial-v1`与`FINSIGHT_OCR_TRIAL=true`必须同时明确选择；数据仍为experimental / relaxed_ocr，待核验提示保留。
- 严格`serving`是另一份发布清单：当前3份报告、106条事实。网页公开公司与财报目录继续按严格审核发布管理，试验问答库的118份不能冒充118份管理员已审核的公开资料。
- 关闭试验开关只读取同一数据库的严格`serving`，不会恢复旧117份/BGE。旧库恢复需要选择整套旧数据库、原件、任务存储和BGE模型/索引配置，不改名混用向量。
- 本机新主库为`finsight_cloud118_20261022_default`，原数据库保留；新状态位于`data/runtime/cloud118`，新向量位于`data/chroma_cloud_v4_1024`。原件按内容摘要保留在`data_root`。不要删除这些运行资料后仍把代码能导入当作数据已就绪。

## 启动

新上传链路须保持`ETL_INGEST_RAG=true`。旧版只做抽取时可能关闭过该开关；关闭时程序会明确阻止候选进入可批准状态，不能用“数字提取成功”代替索引完成。本机已将此项对齐为true。

在项目根目录先运行预检。它验证所选资料版本、只读权限、向量指纹、云端型号列表以及Redis/RabbitMQ协议，不发送问答生成请求。

```powershell
& '.venv\Scripts\python.exe' -X utf8 scripts/preflight.py --env .env
```

MySQL、Redis、RabbitMQ未启动时，按[部署说明](DEPLOYMENT.md)准备依赖。当前本机保留的Redis6391、RabbitMQ5673可以继续复用，账号仅在私有配置中；不要另外启动同端口服务。

Windows自带PowerShell 5.1可分别在三个终端执行以下三条命令，不要求`pwsh`在PATH中。终端都先切换到项目根目录。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-agent.ps1 -EnvFile .env -OverrideEnv
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-java.ps1 -EnvFile .env -OverrideEnv
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-frontend.ps1 -EnvFile .env -OverrideEnv
```

前端默认`http://127.0.0.1:5173`，Java为8080，Python为8000。依赖或原件缺失时查看预检的具体失败项，不通过更换模型名或跳过核验来消除错误。单独首次启动Java迁移后，必要时执行`python scripts/ensure_real_reader.py --env .env`补齐财报正文只读权限。

安装了PowerShell 7的环境可使用`start-finsight.ps1`统一启动及`stop-finsight.ps1`停止；手动三个终端启动时分别按Ctrl+C停止应用。停止应用不关闭共享MySQL、Redis或RabbitMQ。本轮没有自动重启日常服务。

## 上传与核验

普通用户进入“财报提交”，上传有效、未加密的PDF。处理状态和审核状态分开；处理成功后进入管理员队列。管理员打开原始PDF、检查候选与缺项、确认身份并批准，再在列表选中发布。新上传仍执行严格发布检查，不因默认试验问答开关而跳过管理员审核。

OCR已有匹配远端作业及缓存时续用，断网后查看原作业，不能盲目重新付费提交。重复上传允许产生不同提交回执，但共享同一PDF摘要的OCR和候选事实。未提取、无法识别、冲突、待核验与原件确实未披露分别标记，缺失不补零。

正式发布后可把试验开关关闭、重启应用，验证新增报告进入严格问答。试验模式依旧读取固定的118份清单，新上传不会自动混入这份冻结试验版本；需要显式准备、验收和发布新版本。

## 隔离重建与恢复

`financial_upload_upgrade.py`和`ocr_trial.py`仍是隔离重建工具，读取`.env.upload120`，并限制试验数据库与8120/18280端口。它们不用于重启已切换的主目录新数据库。继续这类实验时进入原候选目录、使用原私有配置，保留其独立运行记录。

主目录源码、原私有环境和旧数据均有本机备份。恢复时先停止自己启动的应用，核对[本轮记录](ROLLOUT_146_20261022.md)中的备份位置，恢复对应代码及完整配置；不要只切换一个向量型号。不要覆盖原数据版本摘要或删除旧原件来修复历史引用。

本机已有数据与缓存复用经过记录；这不等于全新机器、无缓存下载或公开云盘下载链路已经验证。新资料包、云盘分发、更多问法测试与薄报告处理仍按后续清单开展。

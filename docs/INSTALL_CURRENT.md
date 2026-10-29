# 当前版本安装、运行与恢复

版本：FinSight-AIC-2026-96123955-r3。已验证平台为Windows，Python3.11；前端使用Node/npm，后端使用JDK（目标17）和Maven。MySQL、Redis、RabbitMQ由使用者安装。云端生成、OCR和向量需各自有效账号，可能计费。不能只下载源码就获得真实财报库。首次复核可先使用[最小复现](MINIMAL_REPRODUCTION.md)的3份严格资料；完整119份试验库另行恢复。

## 1. 安装源码依赖

将源码ZIP解压到新目录，可以包含空格。以下命令均在解压后的项目根执行。Windows PowerShell5.1可以使用这些命令；首次创建环境后再激活并非必要。

```powershell
py -3.11 -m venv .venv
& '.venv/Scripts/python.exe' -m pip install --upgrade pip
& '.venv/Scripts/python.exe' -m pip install -r requirements.lock.txt
Push-Location frontend
npm ci
npm run build
Pop-Location
Push-Location backend-java
mvn verify
Pop-Location
Copy-Item -LiteralPath '.env.example' -Destination '.env'
```

填写私有.env：DB_HOST/PORT/NAME/USER/PASSWORD、MYSQL_*（Java使用）、独立SQL_DB_USER/PASSWORD/NAME、Redis、RabbitMQ、INTERNAL_API_TOKEN、JWT_SECRET和管理员账号。Python和Java必须共用内部令牌。数据库名建议新建finsight_*，不覆盖已有库。密钥不进入前端或公开文件。

云端配置使用LLM_PROVIDER=openai_compat、LLM_BASE_URL=https://api.deepseek.com、LLM_MODEL=deepseek-flash及LLM_API_KEY；请求固定temperature=0、关闭思考、3072输出上限、60秒单次等待。向量使用EMBEDDING_PROVIDER=dashscope、EMBEDDING_MODEL=text-embedding-v4、EMBEDDING_DIMENSION=1024和实际百炼端点/密钥。OCR使用OCR_API_PROTOCOL=paddle_async、OCR_MODEL=PaddleOCR-VL-1.6与实际端点、令牌；当前禁用的功能不要因安装而开启。

## 2. 准备当前真实资料

当前119份试验库与历史117份/BGE附件格式不同。prepare_real_data.py只服务于明确的旧117份附件，不能拿它导入当前119份快照，也不能把旧向量改名。

本地外部资料目录由restore_release.py export生成，包含财务数据、对应PDF与既有向量、逐文件摘要；不含用户账号、会话、私有配置或密钥。该目录不进入公开源码ZIP，评委须另行获得经团队确认范围的资料。验证和恢复示例：

```powershell
$py = '.venv/Scripts/python.exe'
& $py -B -X utf8 scripts/restore_release.py verify --folder 'D:/资料包位置'
& $py -B -X utf8 scripts/restore_release.py restore --folder 'D:/资料包位置' --env .env
```

restore要求数据库不存在、目标Chroma目录为空；不会删除已有库或替换既有资料。失败后保留诊断，并可能留下部分导入或已写入的指针；只有完整成功回执才表示安装完成。应选择新的空库和新目录重新安装，不盲删数据。SQL_DB_USER必须是本次专用只读账户，不能沿用已授权其他库的账户。导入保持原数据版本、verified/unverified/derived及experimental状态。它恢复已处理的冻结资料，不重新调用OCR或重新生成全部向量，不宣称逐单元格重新审核。

使用当前试验库须显式设置FINSIGHT_DATA_PROFILE=ocr-trial-v1及FINSIGHT_OCR_TRIAL=true，并保留未完整核验提示。关闭试验开关时读取同库严格serving；这与历史117库切换无关。首次使用原件上传重建而非快照恢复时，走网页上传、PaddleOCR、候选审核和管理员发布；OCR结果不确定先对账，避免重复付费提交。

公司页、资料库、原件下载、关注列表和示例问题均遵循Python的同一资料选择。完整试验库正常显示10家公司、119份报告和2023—2025年度指标；关闭试验开关后的严格最小库只有3份季度报告，年报图应显示缺项说明。目录不可用时明确报错，不回退旧表掩盖范围差异。资料库每页12份，搜索和分页不会改变完整目录统计。

## 3. 启动服务

先启动并配置MySQL、Redis、RabbitMQ。可在三个PowerShell5.1终端分别运行；路径带空格时保留引号。各窗口先进入相同项目目录。

```powershell
# 终端1：Python
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-agent.ps1 -EnvFile .env -OverrideEnv -Port 8000
# 终端2：Java
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-java.ps1 -EnvFile .env -OverrideEnv -Port 8080 -AgentBaseUrl http://127.0.0.1:8000
# 终端3：Vue
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-frontend.ps1 -EnvFile .env -OverrideEnv -Port 5173 -ApiBaseUrl http://127.0.0.1:8080
```

端口及CORS配置必须对应。若使用已构建的Java包，也可在加载同一私有配置后执行java -jar backend-java/target/finsight-api-0.1.0-SNAPSHOT.jar。首启Java执行Flyway迁移后，补齐金融只读授权并运行预检：

```powershell
& '.venv/Scripts/python.exe' -B -X utf8 scripts/ensure_real_reader.py --env .env
& '.venv/Scripts/python.exe' -B -X utf8 scripts/preflight.py --env .env
```

浏览器打开http://127.0.0.1:5173，注册普通用户或使用私有管理员配置。管理员登录入口与普通用户分别鉴权。基础验证按照[最小复现说明](MINIMAL_REPRODUCTION.md)操作。PowerShell7用户可选start-finsight.ps1统一入口；没有pwsh时直接使用上面三个终端，不需要为了看页面安装本地问答模型。

## 4. 停止、恢复与排查

分终端启动时，在对应应用终端Ctrl+C停止；不停止电脑上的无关服务。统一启动时使用同版本stop-finsight.ps1，仅处理记录的本项目进程。不要用任务名批量结束Java、Python或数据库。

刷新和临时断网不取消任务，重新打开同一会话按任务ID恢复。点击停止会请求取消，不将取消后的迟到结果登记为新事实。云端超时、认证/余额、限流、数据库不可达、索引模型指纹错误分别检查；只看HTTP流结束不能确认已经保存。失败后仅说“照旧”可能需要重新写出公司、指标、期间，属于当前限制。

API密钥只放后端私有.env；不要把.env、应用日志、用户会话或真实原文提交GitHub。检查日志时只提交脱敏任务ID、错误类型和问题，避免贴令牌与连接串。当前源码包内的历史脚本不都适用于这个数据版本，带日期的历史安装文档仅供溯源。

## 5. 验证范围

交付回执记录最终ZIP摘要、含空格目录、独立数据库与索引、依赖缓存复用、程序测试和界面实测。下载缓存可复用，不声称无缓存下载；使用本地真实资料，不声称已验证网盘下载；快照恢复不等同从头重复OCR/抽取。问答成绩仍是64/88，录屏精选题和安装冒烟不并入准确率。

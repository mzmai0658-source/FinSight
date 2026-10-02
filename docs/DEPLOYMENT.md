# 部署与运行配置

公开合成演示按 [DEMO.md](DEMO.md) 执行。支持参考环境为 Python 3.11、Node.js 24.12.0、JDK 23.0.2（源码目标17）、MySQL 9.3.0、Redis、RabbitMQ、Ollama Qwen 和本地 BGE；Python 使用 `requirements.lock.txt`，前端使用 `npm ci`。本次实际安装与验证记录见 [DELIVERY_ACCEPTANCE.md](DELIVERY_ACCEPTANCE.md)。

## 服务关系

浏览器 → Java（默认 8080，鉴权、配额、会话保存）→ Python（默认 8000，Agent、资源登记与 ETL）。前端默认 5173，通过开发代理访问 Java。Python 内部端口仅供 Java 或受控内网访问。

在仓库根目录运行三个启动脚本，均传入同一 `-EnvFile .env.demo -OverrideEnv`。启动脚本准备并加载私有 `INTERNAL_API_TOKEN`；Java 与 Python 必须使用相同值。直接启动 Java 时也必须加载该环境变量。`/internal/*` 请求需 `X-Internal-Token`，缺失服务配置返回 503，凭据不匹配返回 401。

## 数据与权限

| 配置 | 用途 |
| --- | --- |
| DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD | Python 导入和业务管理账户 |
| MYSQL_HOST / MYSQL_PORT / MYSQL_DATABASE / MYSQL_USER / MYSQL_PASSWORD | Java 业务数据库；PowerShell 加载脚本同步 DB_* |
| SQL_DB_USER / SQL_DB_PASSWORD | 独立的 Agent SELECT 账户；不能复用 root 或 DB_USER |
| SQL_DB_HOST / SQL_DB_PORT / SQL_DB_NAME | 可选的查询连接覆盖，默认使用 DB_* 目标 |
| SQL_QUERY_TIMEOUT_MS | 查询时限，默认 5000；同时有连接和网络读取超时 |
| FINANCIAL_FACTS_MANIFEST | 已核对的字段来源清单；公开演示使用 data/runtime/demo-v3/facts.json，生产事实以数据库验收指针为准 |
| CHROMA_DB_PATH | demo 必须为 data/demo_chroma_db；与正式索引隔离 |

`demo-seed` 仅允许本机 `finsight_demo` 或 `finsight_demo_` 隔离库。生成新的v3虚构PDF、事实与索引，验收后原子发布，创建专用只读账号；同版本重复初始化不产生重复事实或片段。失败候选不切指针，已有业务会话保留。Agent只加载当前登记公司。

正式语料先确认来源，再通过自己的 ETL 账户导入。Agent 账户仅授予指定兼容财务表、v3发布/事实/报告表及来源表的 SELECT，禁止文件、管理和写入权限。缺少字段来源映射时界面显示“原文位置未登记”，不会从同名文件推测页码。不要将未经确认来源的正式数据复制进公开演示包。

## 模型与导入

| 配置 | 用途 |
| --- | --- |
| LLM_PROVIDER=ollama | 本地对话模型 |
| OLLAMA_BASE_URL / OLLAMA_MODEL | 默认本机 11434/v1 与 qwen3.5:9b-q4_K_M |
| OLLAMA_REASONING_EFFORT=none | 生产启动默认关闭可配置思考；实验需显式记录所用设置 |
| OLLAMA_CONTEXT_LENGTH=16384 | v3每次原生调用固定16K，仍应避免超过上下文预算；兼容旧入口的提示不能替代实际响应检查 |
| EMBEDDING_PROVIDER=bge_local / EMBEDDING_MODEL | 默认 BAAI/bge-small-zh-v1.5，显式 CLS 池化 |
| OCR_API_URL | 新财报没有匹配 OCR 缓存时所需的 OCR 服务 |
| OCR_API_PROTOCOL=paddle_async / OCR_MODEL=PaddleOCR-VL-1.6 | PaddleOCR 异步提交、轮询、下载 JSON；凭据仅配置在私有环境文件 |
| ETL_INGEST_RAG | 是否将新财报叙述内容写入向量库 |

BGE 首次使用下载权重；完整缓存存在时直接从本地加载，避免离线环境反复校验远端。模型指纹或维度不匹配时需在独立目录重建索引。切换模型不能继续使用旧向量。

管理页在上传前显示 OCR 配置状态。财报导入需要匹配的本地缓存或 OCR 服务，研报支持文字层读取。复用其他目录的同名 OCR 缓存前会核对 PDF 内容哈希。ETL 返回 success / partial / failed，并记录失败阶段；重试保留任务记录，已在运行的任务不能重复入队。向量更新失败会保留已发布的旧版本。

## 页面与运行状态

`/api/meta/health` 聚合 Agent 查询账户、实际集合/向量指纹和目标模型状态；目录存在或填写了配置不等于就绪。目标模型可用但上下文低于建议值时标为可访问并附带警告，工作台显示黄色状态点，悬停可见实际长度；这不等于长输入已通过验收。OCR 字段表示导入配置能力，不宣称远端 OCR 已完成实测。

正文在核验后发布，工具进度仍实时显示。生成或保存失败会保留错误状态。资源使用登记 ID，经 Java 校验当前用户持有的已保存证据后转发；旧的任意路径接口和公共 results 静态挂载已移除。默认禁用未经过统一核验的 advisor 报告接口。

## 外部依赖与观测

Redis 用于令牌、配额与缓存；RabbitMQ 用于异步审计、通知和 ETL。聊天证据直接写入 MySQL，不能以异步日志代替会话保存。RabbitMQ 不可用时应保留 outbox 待重试记录；实际异步导入验收需要可用的队列服务。

Java 日志和 Python 日志传递 X-Request-Id，方便定位请求。Python `/internal/metrics` 也需要服务凭据；若使用 Prometheus，须由部署方在私有抓取配置中设置 X-Internal-Token，不将密钥提交到仓库。Java 指标与外部观测组件应限制访问。

常见问题优先查看对应状态和请求 ID：无 SQL 只读配置则查询失败；向量为空则重新构建隔离索引；模型未安装则先下载；OCR 缓存缺失则提供匹配缓存或服务。Flyway 校验失败应核对迁移历史并修复具体原因，不通过清空数据来绕过校验。

## 维护与问题反馈

保留任务 ID、应用版本、数据版本、问题文本、预期与实际结果；提交前去除令牌、环境文件和私人原件。成果渠道尚未选择，反馈入口待填。先复现问题并新增相应回归，再修改；数据或指标目录变化必须构建候选索引并重新验收，不能手改旧摘要。每次发布保留上一验收版本和 ZIP 摘要；失败候选不切指针。定期核查依赖、源文件版权和锁版本，模型与数据变更重新跑固定验收。维护负责人及频率待真实团队确定。

共存部署可配置 `REDIS_DATABASE` 与 `RABBITMQ_VHOST`，使用新空间并先授予访问权限。设置 `LLM_BASE_URL`、`LLM_MODEL` 时它们优先于OLLAMA同类字段；预检与实际模型使用同一解析器。公开默认保持Qwen，不通过预检时按具体hint排查，不关闭来源或权限门槛。

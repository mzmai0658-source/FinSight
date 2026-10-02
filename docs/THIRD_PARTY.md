# 开源及第三方资源使用清单

清单更新：2026-10-02。版本以本仓库 `requirements.lock.txt`、`frontend/package-lock.json` 和 `backend-java/pom.xml` 的可复现基线为准；本轮新增包的许可证从发行包元数据及上游说明核对。传递依赖由锁文件或构建工具解析结果确定。

FinSight 自研部分包括：统一请求与条件修改、LangGraph 编排与事件流、版本化事实审计及发布、确定性 Decimal 执行、原文定位与索引、分项核验、持久任务与恢复、前端证据交互和评测程序。项目没有训练或微调下列模型，也不把第三方模型权重提交进仓库。

## 模型与外部运行时

| 名称 | 使用版本 | 许可证 | 使用方式 | 关键义务与边界 |
| --- | --- | --- | --- | --- |
| [Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B) | Ollama `qwen3.5:9b-q4_K_M` | Apache-2.0 | 由 Ollama 在本机运行，承担意图理解、工具选择和答案组织 | 权重独立下载；保留许可证与归属；项目未训练或微调该模型 |
| [Fin-R1](https://huggingface.co/SUFE-AIFLM-Lab/Fin-R1) | 本机社区GGUF别名 `fin-r1:7b-q4`，摘要 `b75ab38fe963` | 历史来源/量化许可待核验；本轮不使用 | 仅用于前期本地对照，本轮交付不使用 | FinSight未训练该模型，不随仓库分发权重；社区量化成绩不能推广到原始权重 |
| [Ollama](https://github.com/ollama/ollama) | 0.33.3（本轮实测） | MIT | v3 使用原生异步 Chat API 和结构化输出；实验保留兼容端点 | 不随本仓库再分发；遵守其 MIT 声明 |
| [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5) | v1.5 | MIT | 由 sentence-transformers 首次运行时下载，用于构建与查询向量 | 权重不入仓库；保留模型卡与 MIT 归属 |
| [PaddleOCR / PaddleOCR-VL](https://github.com/PaddlePaddle/PaddleOCR) | 配置值 `PaddleOCR-VL-1.6`，本轮未调用 | Apache-2.0；托管服务条款另行适用 | 可选真实导入能力；公开合成数据不使用远端OCR | 凭据仅保存在私有环境文件；仓库不分发模型、真实 OCR 输出或受限原件 |
| MySQL Server | 9.3.0（本轮实测） | GPL-2.0 | 外部财务数据与业务数据库 | 用户自行安装；本项目通过标准网络协议访问 |
| Redis | 本机验收使用 5.0.14.1 | BSD-3-Clause | 外部缓存、配额、热点与 UV | 用户自行安装；不随仓库再分发 |
| RabbitMQ | 本机验收使用 4.3.6 | MPL-2.0 | 外部异步任务、发布确认与死信队列 | 用户自行安装；修改其 MPL 文件时需遵循文件级义务 |
| Prometheus | 用户本机安装版本 | Apache-2.0 | 可选指标采集 | Docker 镜像独立分发，保留上游声明 |
| Grafana | 用户本机安装版本 | AGPL-3.0 | 可选本地观测面板 | 仅作为外部服务使用；若修改并提供网络服务需核查 AGPL 义务 |

## Python 直接依赖

| 组件 | 验证版本 | 许可证 | 本项目用途 |
| --- | ---: | --- | --- |
| LangGraph | 1.1.4 | MIT | v3 生产 Agent 状态图 |
| langgraph-checkpoint | 4.0.1 | MIT | LangGraph 运行依赖 |
| langchain-ollama | 1.1.0 | MIT | 原生异步本地模型调用与取消 |
| langchain-core | 1.2.23 | MIT | 消息及运行时协议 |
| Ollama Python SDK | 0.6.2 | MIT | 本地模型 HTTP 传输 |
| pandas | 3.0.2 | BSD-3-Clause | 表格数据处理 |
| NumPy | 2.4.4 | BSD-3-Clause 等组合许可 | 数值计算 |
| openpyxl | 3.1.5 | MIT | Excel 读取 |
| SQLAlchemy | 2.0.48 | MIT | 数据库访问与模型 |
| PyMySQL | 1.1.2 | MIT | MySQL 驱动 |
| MySQL Connector/Python | 9.6.0 | GPL-2.0 with FOSS License Exception | 遗留导入链路驱动 |
| sqlglot | 30.17.0 | MIT | SQL AST 只读校验 |
| FastAPI | 0.136.0 | MIT | Python 内部 API |
| Uvicorn | 0.42.0 | BSD-3-Clause | ASGI 服务 |
| Pydantic | 2.12.5 | MIT | API 数据模型 |
| jsonschema | 4.26.0 | MIT | 对本地模型原生JSON合同做独立客户端验证 |
| Requests | 2.33.1 | Apache-2.0 | HTTP 客户端 |
| sentence-transformers | 5.3.0 | Apache-2.0 | 本地 embedding 推理 |
| DashScope SDK | 1.25.15 | Apache-2.0 | 显式启用时的可选远端回退 |
| ChromaDB | 1.5.5 | Apache-2.0 | 本地向量存储 |
| Beautiful Soup | 4.14.3 | MIT | HTML 解析 |
| pypdf | 6.9.2 | BSD-3-Clause | PDF 文本处理 |
| pdfplumber | 0.11.9 | MIT | PDF 页面与表格辅助解析 |
| pdf2image | 1.17.0 | MIT | PDF 页面渲染适配 |
| Pillow | 12.1.1 | MIT-CMU | 图像处理 |
| Matplotlib | 3.10.8 | Matplotlib License（PSF-compatible） | PNG 图表回退 |
| python-dotenv | 1.2.2 | BSD-3-Clause | 本地环境变量加载 |
| tqdm | 4.67.3 | MPL-2.0 / MIT | 批处理进度显示 |
| Loguru | 0.7.3 | MIT | Python 日志 |
| prometheus-client | 0.25.0 | Apache-2.0 / BSD-2-Clause | Python 指标 |
| pytest | 9.0.3 | MIT | 自动化测试 |
| [ReportLab](https://docs.reportlab.com/developerfaqs/) | 4.5.1 | BSD | 合成报告及技术报告 PDF |
| [PyMuPDF](https://pymupdf.io/licensing) | 1.27.2.3 | AGPL-3.0 或商业许可 | 既有 PDF 解析/验收辅助，权利义务按实际使用场景核对 |
| agentevals | 0.0.9 | MIT | 历史离线评测工具 |

Python 包均通过包管理器动态安装，未复制其源代码到本仓库。再分发二进制环境时，应一并保留各发行包自带的许可证与 NOTICE。

## Java 直接依赖与框架

| 组件 | 验证版本 | 许可证 | 本项目用途 |
| --- | ---: | --- | --- |
| Spring Boot / Spring Framework | 3.5.6 / 6.2.11 | Apache-2.0 | Web、Security、Validation、Redis、AMQP、Actuator |
| MyBatis-Plus | 3.5.12 | Apache-2.0 | 数据访问层 |
| MySQL Connector/J | 9.4.0 | GPL-2.0 with FOSS License Exception | Java MySQL 驱动 |
| Flyway Community | 11.7.2 | Apache-2.0 | 数据库迁移 |
| JJWT | 0.12.6 | Apache-2.0 | JWT 生成与校验 |
| springdoc-openapi | 2.8.9 | Apache-2.0 | OpenAPI / Swagger UI |
| Resilience4j | 2.3.0 | Apache-2.0 | 重试、熔断与限流 |
| Lombok | 1.18.40 | MIT | 编译期样板代码生成 |
| Micrometer / Prometheus registry | 1.15.4 | Apache-2.0 | Java 指标 |

Maven 传递依赖不在本表逐项展开，解析版本可由 `mvn dependency:list` 重现。构建产物若对外分发，应保留 JAR 内的 `META-INF/LICENSE*` 与 `META-INF/NOTICE*`。

## 前端直接依赖

| 组件 | 验证版本 | 许可证 | 本项目用途 |
| --- | ---: | --- | --- |
| Vue | 3.5.32 | MIT | 前端框架 |
| Vue Router | 4.6.4 | MIT | 路由 |
| Pinia | 2.3.1 | MIT | 状态管理 |
| Axios | 1.18.0 | MIT | HTTP 客户端 |
| Apache ECharts | 6.1.0 | Apache-2.0 | 财务图表 |
| DOMPurify | 3.4.14 | Apache-2.0 OR MPL-2.0 | Markdown 生成后清理 HTML |
| Vitest | 3.2.7 | MIT | 前端行为测试 |
| jsdom | 26.1.0 | MIT | DOM 测试环境 |
| marked | 18.0.5 | MIT | Markdown 渲染 |
| TypeScript | 5.9.3 | Apache-2.0 | 类型系统与编译 |
| Vite | 6.4.3 | MIT | 构建工具 |
| `@vitejs/plugin-vue` | 5.2.4 | MIT | Vue 构建插件 |
| vue-tsc | 2.2.12 | MIT | Vue 类型检查 |
| `@types/node` | 24.12.2 | MIT | Node.js 类型声明 |

前端传递依赖与许可证可从 `frontend/package-lock.json` 和各包的 `node_modules/*/LICENSE*` 复核。

## 数据、内容和服务边界

- `demo/financial_report.sql`、`demo/financial_facts.json`、`demo/sources.json`、`demo/knowledge/` 及公开 `eval/dataset.jsonl` 是项目原创合成数据，采用 Apache-2.0；公司、代码、数字和原因解释均为教学设定，不是上市公司正式披露。
- `database/financial_report.sql` 仅保留无真实数据的兼容结构说明。旧数据、旧评测摘录和本地审查归档不进入公开仓库。
- `data_root/`、`data/chroma_db/`、上传文件、日志、模型权重与本机密钥均被 `.gitignore` 排除，不属于开源发布物。
- 真实财报只在本机演示和验证，不随代码仓库再分发。公开复现使用 `demo/` 中的原创合成数据。
- DashScope 是可选兼容接口；PaddleOCR 远端服务只用于导入阶段。启用远端服务时，使用者需自行遵守服务条款与数据处理要求。
- 第三方名称和商标仅用于说明兼容性与来源，不代表其对 FinSight 的认可或背书。


## 本轮核验与发布边界

公开默认链路由 `demo/v3/spec.json` 生成原创建模的虚构报告，Apache-2.0。`demo/` 的旧 Markdown/SQL 数字仅供历史测试，净利润语义不能代替 v3 事实目录。当前初始化不加载旧字段作为归母。

锁定 Python 全部发行版本及许可元数据见 [Python 资源清单](resources/python-resources.json)，Node 直接及传递资源见 [Node 资源清单](resources/node-resources.json)。Java 直接依赖见上表，完整解析版本见 [Maven 资源清单](resources/java-dependencies.txt)。这些记录是程序及发行元数据核对，不能写成团队成员完成了人工许可审核。

PyMuPDF 使用 AGPL-3.0 开源许可或 Artifex 商业许可，不能把它概括成宽松许可。在对外分发组合应用或提供相关网络服务前，应按 [上游许可说明](https://pymupdf.io/licensing) 履行对应源代码等义务，或取得适合场景的商业许可；FinSight 自研文件的 Apache-2.0 标识不替代第三方许可。MySQL 驱动的 GPL/FOSS 例外、Grafana 的 AGPL、RabbitMQ 的 MPL 等同样按各自原文适用。提交 ZIP 不含这些第三方二进制、权重或服务安装包；包管理器安装时保留上游许可证与 NOTICE。

参考方法采用 [FinGLM](https://github.com/MetaGLM/FinGLM)、[TAT-LLM](https://github.com/fengbinzhu/TAT-LLM) 与 [ConvFinQA](https://github.com/czyssrs/ConvFinQA) 的公开资料，未复制其训练权重、问答数据或实现代码。方法借鉴与自研实现分开声明。Qwen/BGE 权重按模型卡独立下载，许可已从上游模型卡核对；Fin-R1 仅属历史试验，本轮无权重分发、无新增对照成绩。

## AI开发辅助与构建工具

|工具|实际版本/来源|许可与核验状态|用途、自研边界及义务|
|---|---|---|---|
|[Codex / OpenAI](https://developers.openai.com/api/docs/guides/code-generation)|本对话的桌面开发辅助；具体应用/模型版本待真实团队确认|按所用账号的服务条款；未分发其程序或权重；账号授权待人工核验|辅助设计、代码、调试、测试脚本与文稿。AI生成内容不记作成员已人工审核，实际审核者和范围待填|
|Python|3.11.9，本机解释器|PSF许可；解释器不入包|运行自研Python与测试；保留安装发行版声明|
|Node.js / npm|24.12.0 / 11.6.2，本机安装|Node MIT及第三方声明、npm Artistic-2.0；工具未分发|前端构建；依赖来源和许可见锁文件，不将其代码写成自研|
|JDK|23.0.2，本机安装；代码目标17|安装发行商和对应许可待人工核验；本包不再分发JDK|编译/运行Java；不声称已完成各JDK发行版兼容实测|
|Apache Maven|3.9.12|Apache-2.0；未分发工具|解析依赖并构建，不修改其上游源码|
|PowerShell|统一启动需要7；本机实际版本见安装环境|MIT及发行说明；工具未分发|原生启动、停止与预检；Windows PowerShell5.1不支持本入口|
|Poppler|本机工具链，用于PDF图像核对|GPL及上游组件说明；未分发工具|本轮PDF逐页视觉检查；不作为应用金融逻辑依赖|

这些是工具与资源清单，不是人工合规完成声明。模型/BGE权重、外部数据库、缓存与安装工具均由使用者独立准备；具体团队权属、账号条款及AI生成内容人工审核事实仍待真实填写。

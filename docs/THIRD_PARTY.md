# 开源及第三方资源使用清单

清单更新：2026-10-29。r3未新增或升级依赖。当前主链为DeepSeek生成、PaddleOCR识别和百炼向量，BGE仅兼容历史库。版本以Python和前端锁文件、Java POM为准；许可依据发行元数据与注明的上游文件。团队人工核查另行记录，不作为法律结论。

本轮默认数据已选择新119份云端试验库，保留`experimental / relaxed_ocr`、`ocr-trial-v1`和未完整核验提示；旧117份/BGE及其资源配置单独保存，不纳入新默认，也不改写其历史身份。

FinSight 自研部分包括：统一请求与条件修改、LangGraph 编排与事件流、版本化事实审计及发布、确定性 Decimal 执行、原文定位与索引、分项核验、持久任务与恢复、前端证据交互和评测程序。项目没有训练或微调下列模型，也不把第三方模型权重提交进仓库。

## 模型与外部运行时

| 名称 | 使用版本 | 许可证 | 使用方式 | 关键义务与边界 |
| --- | --- | --- | --- | --- |
| DeepSeek 云端服务 | 配置模型 `deepseek-flash`；服务端版本由服务方维护 | 服务及账号条款需使用者核对；不在此声明托管权重许可证 | 统一承担规划、解释、审核、标题与其他生成任务，通过 httpx 调用 | 使用者准备私有 API 密钥并核对费用、服务和数据处理条款；不分发服务端权重，不报价；缺配置或失败不回退本地模型 |
| 百炼云端向量 | `text-embedding-v4`，1024维 | 云端服务及账号条款另行适用，不分发服务端权重 | 通过HTTP生成document/query向量，本地Chroma保存 | 同一索引固定模型、维度与算法指纹；私有密钥不进入持久化的向量函数配置 |
| [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5) | v1.5 | MIT | 历史117份资料的显式兼容模式，新库不加载BGE权重 | 权重不入仓库；保留模型卡与MIT归属，不能作为云端失败回退 |
| [PaddleOCR / PaddleOCR-VL](https://github.com/PaddlePaddle/PaddleOCR) | `PaddleOCR-VL-1.6` | Apache-2.0；托管服务条款另行适用 | 新120份原件重新提交识别，保存JSON、MD和远端作业号；只续用本轮已完成作业 | 凭据仅在私有配置；账号额度需按实际服务核对，不分发模型权重 |
| MySQL Server | 9.3.0（既有环境记录） | GPL-2.0 | 外部财务数据与业务数据库 | 用户自行安装；本项目通过标准网络协议访问 |
| Redis | 本机验收使用 5.0.14.1 | BSD-3-Clause | 外部缓存、配额、热点与 UV | 用户自行安装；不随仓库再分发 |
| RabbitMQ | 本机验收使用 4.3.6 | MPL-2.0 | 外部异步任务、发布确认与死信队列 | 用户自行安装；修改其 MPL 文件时需遵循文件级义务 |
| Prometheus | 用户本机安装版本 | Apache-2.0 | 可选指标采集 | Docker 镜像独立分发，保留上游声明 |
| Grafana | 用户本机安装版本 | AGPL-3.0 | 可选本地观测面板 | 仅作为外部服务使用；若修改并提供网络服务需核查 AGPL 义务 |

## Python主要依赖及相关运行包

| 组件 | 验证版本 | 许可证 | 本项目用途 |
| --- | ---: | --- | --- |
| LangGraph | 1.1.4 | MIT | v3 生产 Agent 状态图 |
| langgraph-checkpoint | 4.0.1 | MIT | LangGraph 运行依赖 |
| langchain-core | 1.2.23 | MIT | 消息及运行时协议 |
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
| jsonschema | 4.26.0 | MIT | 对云端生成的 JSON 合同做独立客户端验证 |
| [httpx](https://github.com/encode/httpx/blob/0.28.1/LICENSE.md) | 0.28.1 | BSD-3-Clause | 统一云端生成传输、异步调用与取消传播 |
| Requests | 2.33.1 | Apache-2.0 | 既有 HTTP、数据导入与评测客户端 |
| sentence-transformers | 5.3.0 | Apache-2.0 | 显式历史BGE兼容模式；新云端索引不加载其权重 |
| DashScope SDK | 1.25.15 | Apache-2.0 | 保留的历史可选依赖；本轮百炼向量使用HTTP适配，不依赖SDK调用，不作为生成失败回退 |
| ChromaDB | 1.5.5 | Apache-2.0 | 本地向量存储 |
| Beautiful Soup | 4.14.3 | MIT | HTML 解析 |
| pypdf | 6.9.2 | BSD-3-Clause | PDF 文本处理 |
| pdfplumber | 0.11.9 | MIT | PDF 页面与表格辅助解析 |
| [pdfminer.six](https://github.com/pdfminer/pdfminer.six/blob/master/LICENSE) | 20251230 | MIT | pdfplumber的文本解析依赖；版本和许可取自已安装发行元数据 |
| pdf2image | 1.17.0 | MIT | PDF 页面渲染适配 |
| Pillow | 12.1.1 | MIT-CMU | 图像处理 |
| Matplotlib | 3.10.8 | Matplotlib License（PSF-compatible） | PNG 图表回退 |
| python-dotenv | 1.2.2 | BSD-3-Clause | 本地环境变量加载 |
| tqdm | 4.67.3 | MPL-2.0 / MIT | 批处理进度显示 |
| Loguru | 0.7.3 | MIT | Python 日志 |
| prometheus-client | 0.25.0 | Apache-2.0 / BSD-2-Clause | Python 指标 |
| pytest | 9.0.3 | MIT | 自动化测试 |
| [ReportLab](https://docs.reportlab.com/developerfaqs/) | 4.5.1 | BSD | 合成报告及技术报告 PDF |
| [PyMuPDF](https://pymupdf.io/licensing) | 1.27.2.3 | AGPL-3.0 或商业许可 | 生产OCR后处理、PDF原件取证、来源定位及验收；AGPL或商业双许可 |
| agentevals | 0.0.9 | MIT | 历史离线评测工具 |

Python 包均通过包管理器动态安装，未复制其源代码到本仓库。再分发二进制环境时，应一并保留各发行包自带的许可证与 NOTICE。

## Java 直接依赖与框架

| 组件 | 验证版本 | 许可证 | 本项目用途 |
| --- | ---: | --- | --- |
| Spring Boot / Spring Framework | 3.5.6 / 6.2.11 | Apache-2.0 | Web、Security、Validation、Redis、AMQP、Actuator |
| [Apache PDFBox](https://pdfbox.apache.org/3.0/getting-started.html) | 3.0.8 | Apache-2.0 | Java上传前解析原始PDF，检查损坏、加密和页面资格 |
| MyBatis-Plus | 3.5.12 | Apache-2.0 | 数据访问层 |
| MySQL Connector/J | 9.4.0 | GPL-2.0 with FOSS License Exception | Java MySQL 驱动 |
| Flyway Community | 11.7.2 | Apache-2.0 | 数据库迁移 |
| JJWT | 0.12.6 | Apache-2.0 | JWT 生成与校验 |
| springdoc-openapi | 2.8.9 | Apache-2.0 | OpenAPI / Swagger UI |
| Resilience4j | 2.3.0 | Apache-2.0 | 重试、熔断与限流 |
| Lombok | 1.18.40 | MIT | 编译期样板代码生成 |
| Micrometer / Prometheus registry | 1.15.4 | Apache-2.0 | Java 指标 |
| H2 Database | 2.3.232 | MPL-2.0 / EPL-1.0（发行POM列明） | 仅用于Java测试，不替代实际MySQL部署 |

Maven 传递依赖不在本表逐项展开，解析版本可由 `mvn dependency:list` 重现。构建产物若对外分发，应保留 JAR 内的 `META-INF/LICENSE*` 与 `META-INF/NOTICE*`。

## 前端直接依赖

| 组件 | 验证版本 | 许可证 | 本项目用途 |
| --- | ---: | --- | --- |
| Vue | 3.5.32 | MIT | 前端框架 |
| Vue Router | 4.6.4 | MIT | 路由 |
| Pinia | 2.3.1 | MIT | 状态管理 |
| Axios | 1.20.0 | MIT | HTTP客户端；记录锁文件实际解析版本，非package.json最低版本 |
| Apache ECharts | 6.1.0 | Apache-2.0 | 财务图表 |
| [vue-echarts](https://github.com/ecomfe/vue-echarts/blob/main/LICENSE) | 8.3.0 | MIT | Vue图表适配 |
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
- 本轮语料为10家新公司的120份真实原件，119份已有新OCR；OCR、事实状态、索引、审核发布及问答分别记录进度。OCR试验配置仍保留未完整原件核验提示，接通不等于严格发布。旧117份/BGE资料保留，旧200/264/324轮成绩及截图只属于各自历史版本；当前64+24轮真实问答成绩为64/88，详见技术报告；旧200轮不挪作当前成绩。此前`demo/`合成数据不作为当前安装或验收依据。
- DeepSeek为统一生成服务，百炼HTTP接口生成1024维向量，PaddleOCR远端服务用于新识别。新的向量集合与历史BGE空间隔离，不自动混查。启用服务时，使用者需核对账号、费用、服务条款、许可和数据处理要求；团队已确认对当前使用范围人工核查，账号条款仍随实际服务变化核对。
- 第三方名称和商标仅用于说明兼容性与来源，不代表其对 FinSight 的认可或背书。


## 本轮核验与发布边界

旧版演示链路由 `demo/v3/spec.json` 生成虚构报告，作为项目原创演示资源采用Apache-2.0。本轮不运行该链路，也不加载旧Markdown/SQL作为真实事实。当前版本的真实原件、事实和来源证明另行验收，真实数据包作为外部输入，最小及完整快照恢复分别记录；网盘由团队提供，不能以历史演示代替。

锁定 Python 全部发行版本及许可元数据见 [Python 资源清单](resources/python-resources.json)，Node 直接及传递资源见 [Node 资源清单](resources/node-resources.json)。Java 直接依赖见上表，完整解析版本见 [Maven 资源清单](resources/java-dependencies.txt)。这些记录是程序及发行元数据核对，团队人工核查以RESOURCE_REVIEW.md中2026-10-25的确认记录为依据，不以程序元数据代替。

PyMuPDF 使用 AGPL-3.0 开源许可或 Artifex 商业许可，不能把它概括成宽松许可。在对外分发组合应用或提供相关网络服务前，应按 [上游许可说明](https://pymupdf.io/licensing) 履行对应源代码等义务，或取得适合场景的商业许可；FinSight 自研文件的 Apache-2.0 标识不替代第三方许可。MySQL 驱动的 GPL/FOSS 例外、Grafana 的 AGPL、RabbitMQ 的 MPL 等同样按各自原文适用。提交 ZIP 不含这些第三方二进制、权重或服务安装包；包管理器安装时保留上游许可证与 NOTICE。

参考方法采用 [FinGLM](https://github.com/MetaGLM/FinGLM)、[TAT-LLM](https://github.com/fengbinzhu/TAT-LLM) 与 [ConvFinQA](https://github.com/czyssrs/ConvFinQA) 的公开资料，未复制其训练权重、问答数据或实现代码。方法借鉴与自研实现分开声明。本轮调用云端DeepSeek、百炼与PaddleOCR服务，不分发服务端权重；历史BGE兼容模式继续保留其模型卡和MIT归属要求。Qwen、Fin-R1和Ollama的历史资源义务见下节，不把旧分数归到云端版本。

## 保留历史资源的归属

Qwen/Ollama 与 Fin-R1 已退出当前生成链路，但保留的历史文档、实验或缓存仍须遵守原资源义务：

| 历史资源 | 已记录版本与许可 | 保留边界 |
| --- | --- | --- |
| [Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B) | 旧 `qwen3.5:9b-q4_K_M`，既有记录为 Apache-2.0 | 权重不随源码分发；重用时保留许可证与归属，不将旧成绩视为 DeepSeek 成绩 |
| [Fin-R1](https://huggingface.co/SUFE-AIFLM-Lab/Fin-R1) | 旧社区 GGUF `fin-r1:7b-q4`、摘要 `b75ab38fe963`；来源/量化许可仍待核验 | 仅属历史对照，不使用或分发其权重，不推广社区量化成绩 |
| [Ollama](https://github.com/ollama/ollama) | 旧实测 0.33.3，既有记录为 MIT | 不属于当前服务依赖；若自行保留或分发，继续保留上游声明 |
| langchain-ollama / Ollama Python SDK | 历史记录 1.1.0 / 0.6.2，均记录为 MIT | 历史锁文件/清单或实验引用不代表当前运行调用；保留发行包时保留其许可 |

历史清单中的版本和许可是既有来源记录，不代表本轮重新运行历史模型。当前报告源稿按本轮架构与证据更新，历史模板与原报告仍按各自版本识别。第三方二进制、缓存或历史材料如另行再分发，不能因移入归档而免除原有许可义务。

## AI开发辅助与构建工具

|工具|实际版本/来源|许可与核验状态|用途、自研边界及义务|
|---|---|---|---|
|[Codex / OpenAI](https://developers.openai.com/api/docs/guides/code-generation)|本对话的Codex桌面开发辅助；应用构建号与内部模型版本未记录，不推定|按所用账号的服务条款；未分发其程序或权重；团队已确认人工核查当前使用授权，具体应用构建号与模型内部版本不推定|辅助设计、代码、调试、测试脚本与文稿。麦明政负责代码实现和代码审核；助手检查不替代财务、许可证或报告全文的成员人工审核，团队已于2026-10-25确认最新代码、报告及来源/权利核查|
|Python|3.11.9，本机解释器|PSF许可；解释器不入包|运行自研Python与测试；保留安装发行版声明|
|Node.js / npm|24.12.0 / 11.6.2，本机安装|Node MIT及第三方声明、npm Artistic-2.0；工具未分发|前端构建；依赖来源和许可见锁文件，不将其代码写成自研|
|JDK|23.0.2，本机安装；代码目标17|团队已确认当前安装与来源/权利人工核查，未扩展到全部发行商；本包不再分发JDK|编译/运行Java；不声称已完成各JDK发行版兼容实测|
|Apache Maven|3.9.12|Apache-2.0；未分发工具|解析依赖并构建，不修改其上游源码|
|PowerShell|本轮隔离入口在Windows PowerShell5.1执行；历史统一启动脚本要求7|MIT及发行说明；工具未分发|本轮通过Python编排启动、恢复和预检，不要求`pwsh`；历史入口另见其运行说明|
|Poppler|本机工具链，用于PDF图像核对|GPL及上游组件说明；未分发工具|本轮PDF逐页视觉检查；不作为应用金融逻辑依赖|

工具与资源清单及团队确认分别记录，见RESOURCE_REVIEW.md。模型/BGE权重、外部数据库、缓存与安装工具均由使用者独立准备；团队为凌云队，麦明政负责代码实现和代码审核，黄俊仪主要完成项目检测报告；团队于2026-10-25确认当前代码、报告及第三方许可、财报分享范围、AI内容来源/权利已人工核查；逐条签字表与逐格财务审计未由本助手代填。

## 最终资源核查记录

团队确认见[资源与人工审核记录](RESOURCE_REVIEW.md)。官方模型说明在2026-10-26查阅：[DeepSeek模型文档](https://api-docs.deepseek.com/quick_start/pricing/)、[百炼向量API](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api)。实际运行固定API型号和请求参数，不能冻结服务端权重。

PyMuPDF实际参与生产PDF处理，不能只写成测试工具。选择开源许可使用时，需提供相应组合程序的完整对应源代码、构建/安装材料及AGPL通知，并向相关网络使用者提供适用的源码获取方式；保留适用版权及生成元数据。自研文件的Apache-2.0授权保留，组合分发义务另行适用；未取得商业许可证就不能宣称商业授权。参考[上游许可说明](https://pymupdf.io/licensing)和[AGPLv3原文](https://www.gnu.org/licenses/agpl-3.0.html)。源码ZIP不捆绑PyMuPDF二进制，安装后仍须履行适用义务。人工核查记录不替代这些具体责任。

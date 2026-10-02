# FinSight 财报证据助手

公开源码仓库：[https://github.com/mzmai0658-source/FinSight](https://github.com/mzmai0658-source/FinSight)

方向：开源赋能的 AI 应用创新。把财务问题编译成统一请求，由程序查询、计算与核验，再提供表格、图表及 PDF 原页证据。技术栈为 Python / LangGraph、Java / Spring Boot、Vue、MySQL 与本地 Ollama；默认模型 `qwen3.5:9b-q4_K_M`，16K 上下文，关闭可配置思考。

公开复现使用 **5 家虚构公司、2022—2024 年 15 份年度报告、240 条事实**。PDF、事实、位置和叙述索引由 `demo/v3/spec.json` 生成，明确标注虚构身份，经过生产原页与口径核验后发布。真实验证库的 10 家公司、117 个报告期与原件仅保留在本机，公开安装不依赖它们。

## 安装与运行

完整步骤见 [公开演示安装指南](docs/DEMO.md)。整套原生联调环境为 Windows、Python 3.11、PowerShell 7、Node.js、JDK 和 Maven；其他系统未完成同等级验收。模型和 Python/Node/Maven 依赖需要网络下载，也可以复用合法的本机缓存。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -X utf8 -m pip install -r requirements.lock.txt
npm --prefix frontend ci
Copy-Item .env.demo.example .env.demo
# 作品说明：先编辑 .env.demo，配置本机 MySQL 管理账号与已启动的 Redis、RabbitMQ。
pwsh -NoProfile -File scripts/demo-seed.ps1 -EnvFile .env.demo
pwsh -NoProfile -File scripts/start-finsight.ps1 -EnvFile .env.demo
```

打开 `http://127.0.0.1:5173`，注册自己的账号。停止应用：`pwsh -NoProfile -File scripts/stop-finsight.ps1`。停止应用不会关闭共享的 MySQL、Ollama、Redis、RabbitMQ。

## 可复核内容

- [技术报告源稿](docs/TECHNICAL_REPORT.md)、[作品简介](docs/WORK_INTRO.md)、[第三方资源与许可](docs/THIRD_PARTY.md)。
- [最终验收记录](docs/DELIVERY_ACCEPTANCE.md)、[运行维护说明](docs/DEPLOYMENT.md)、[上传前检查清单](docs/UPLOAD_CHECKLIST.md)。
- [v3 接口说明](docs/FINSIGHT_V3_API.md)：保留聊天与 SSE 入口，支持持久任务、恢复与取消；结果包含数据版本、分项核验及可选数据集身份。
- `eval/delivery_cases.json` 固定 20 轮公开合成测试；17/20 是项目工程目标，不是比赛规定。旧实验与最终验收分开记录，不拼接成绩。
- `RELEASE_MANIFEST.json` 由打包脚本生成，列出每个源码文件的 SHA-256。提交包不含环境凭据、真实原件、OCR、私人审核清单、数据库、模型权重或运行日志。

## 功能边界

支持已发布事实的查数、比较、排名、图表、概念和原页定位，以及受证据约束的解释。无数据与无证据分别说明；单季查询暂不支持，不换年份补齐，不补零。模型可能误解复杂语言，程序核验不能保证识别全部语义错误。真实用户试用未开展，不能据此宣称效率或满意度提升。

## 验证与打包

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q
mvn -f backend-java/pom.xml test
npm --prefix frontend test
npm --prefix frontend run build
.\.venv\Scripts\python.exe -X utf8 scripts/check_release.py
.\.venv\Scripts\python.exe -X utf8 scripts/package_delivery.py
```

Apache-2.0，见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。团队信息、人工审核事实和成果链接留空待填；视频、真实用户试用、答辩 PPT 本阶段未开展。当前成果为阶段性交付，不代表整套报名材料已齐全。

## 比赛交付材料

[技术报告PDF](docs/FinSight-技术报告.pdf)、[可编辑报告](docs/TECHNICAL_REPORT.md)、[作品简介](docs/WORK_INTRO.md)、[第三方清单](docs/THIRD_PARTY.md)、[验收结果](docs/DELIVERY_ACCEPTANCE.md)和[上传检查清单](docs/UPLOAD_CHECKLIST.md)。

公开示例PDF见 demo/v3/example_reports/，全部标注虚构演示数据；实际初始化仍从同一份spec定义生成并核验，不直接放行示例文件。


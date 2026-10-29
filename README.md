# FinSight财报证据助手

用自然语言查询上市公司财报、比较与排名、绘图并回查PDF原页。DeepSeek理解要求；程序负责只读查询、Decimal计算、图表点和来源核验。网页上传经PaddleOCR解析、候选审核和管理员发布，未批准资料不会自动进入正式库。

参赛编号：**AIC-2026-96123955**。冻结版本：**FinSight-AIC-2026-96123955-r3**（2026-10-29）。当前架构：Python/LangGraph、Java、Vue、MySQL、Chroma、Redis、RabbitMQ；生成使用DeepSeek，OCR使用远端PaddleOCR-VL-1.6，向量使用百炼text-embedding-v4/1024。正常运行无需Ollama或本地问答模型，需要联网和服务端私有密钥，可能产生API费用。

## 安装与复现

先阅读[当前安装运行说明](docs/INSTALL_CURRENT.md)，再按[最小复现说明](docs/MINIMAL_REPRODUCTION.md)恢复和验证。公开源码包不含真实PDF、OCR、用户历史、密钥或向量数据，真实资料需另行准备；不使用虚构报告冒充当前知识库。不要将旧117份BGE向量改名混入新集合。

可先按[最小复现说明](docs/MINIMAL_REPRODUCTION.md)恢复3份严格版本报告，验证查询、金额换算、历史恢复和原件身份。完整119份试验库与最小资料包分开说明；两者都使用外部资料输入，资料ZIP由同批评审材料提供，网盘入口由团队上传后提供。

Windows PowerShell5.1可以分别执行三个服务启动脚本；只有可选的start-finsight.ps1统一入口要求PowerShell7。当前说明不再要求用户直接执行不存在的pwsh。Python依赖固定于requirements.lock.txt，前端固定于package-lock.json，Java按pom.xml构建。

## 数据与效果

固定试验数据为10家公司119份真实财报、7607事实（5554程序核验、1400未完整核验、653派生）与21437叙述片段。ocr-trial-v1保留experimental/relaxed_ocr及未完整核验提示；严格serving另有3份审核报告，两个范围不混称。

r3将公司页、资料库、原件下载及关注列表统一到问答所选发布目录；完整试验库为10家公司119份，严格最小库保持3份季度报告的真实范围。修复年报空图、超过8MiB的原件下载、无效地址白屏和窄屏上传框；保留试验及缺项提示。

本地完整回归：Python1707通过/10跳过、Java142通过、前端81通过及生产构建通过。页面和程序检查不计入问答准确率。

最新同题64轮完整处理46轮；24轮自然改写完整处理18轮，合计64/88（72.73%）。同题旧基线44/64，9题进步、7题退步。数据依据缺项和环境失败留在分母，部分完成不给半分；不是第三方盲测或比赛及格承诺。见[结果摘要](docs/validation/RESULTS.md)与[技术报告源稿](docs/TECHNICAL_REPORT.md)。演示题经过选择，不算新成绩。

复杂长期偏好、多指标精度、原因证据、故障后仅说“照旧”等仍有限制。清晰给出公司、期间和指标更可靠。缺数据不补零，不跨期代替，不提供实时行情、单季拆分或确定预测。

## 材料与许可

- [技术报告PDF](docs/FinSight-技术报告.pdf)、[作品简介](docs/WORK_INTRO.md)。
- [第三方资源](docs/THIRD_PARTY.md)、[AI辅助与人工审核记录](docs/RESOURCE_REVIEW.md)。
- [历史边界](docs/HISTORICAL_MATERIALS.md)、[r3页面与目录验证](docs/PORTAL_FIX_VERIFICATION.md)。
- [演示数字与原件核对](docs/validation/original-financial-values-audit.md)、[本次验证记录](docs/SUBMISSION_VERIFICATION.md)。

凌云队：麦明政负责实现与代码审核，黄俊仪主要完成项目检测报告；团队已确认材料并提供编号AIC-2026-96123955。AI辅助如实披露。自研文件使用[Apache-2.0](LICENSE)，第三方及组合分发仍按[NOTICE](NOTICE)及各自义务执行，尤其PyMuPDF生产依赖的AGPL/商业双许可。

本次源码、技术报告及公开核验材料的[提交分支](https://github.com/mzmai0658-source/FinSight/tree/codex/aic-96123955-final)与RELEASE_MANIFEST.json共同标识交付内容；冻结提交和文件摘要见随包交付回执。团队自用录制稿与报名操作文档存放在外部交付目录，不进入评审源码包。视频、百度网盘链接与报名系统正式提交由团队后续处理；截止日期按团队通知延期至2026年10月30日，具体截止时刻以报名系统为准。真实用户试用未开展。

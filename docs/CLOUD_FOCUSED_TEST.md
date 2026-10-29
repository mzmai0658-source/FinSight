> 历史版本资料：当前安装、模型和数据以 [INSTALL_CURRENT.md](INSTALL_CURRENT.md) 为准；本文旧期间/旧成绩不代表本次交付。

# 用户运行的云端 28 轮复核

原28轮已经由用户在迁移版本运行，独立复核为25/28，记录保留本机。当前修复后的新问法使用[36轮新批次说明](CLOUD_FOLLOWUP36_TEST.md)，尚未运行前不填写成绩。下面仍是原28轮工具的运行方式，不能用原成绩代表新代码。

正式题目严格复用本机冻结的 `cases_focused28.json`：8 道单题及 G5、J12、V3、E01、M03 五组各 4 轮。题目、顺序和原预期按 SHA-256 固定，不改题、不改变单位/原数要求。默认另发 **1 个单独的取消生命周期问题**，以及单份真实OCR缓存的元数据和一张表抽取检查（最多2次云端调用），均不计入28轮分数；异步会话标题也可能产生额外云端调用，并单独统计。请求策略为 `deepseek-flash`、temperature=0、thinking disabled、3072 输出上限、60 秒、重试 0。

## 无服务检查

先在本项目根目录运行。该模式只读取本地源码、冻结题库、独立评分程序、原页标准及已验收数据文件，不连接数据库、应用或云端 API，也不启动进程。

```powershell
pwsh -NoProfile -File scripts/test-cloud-focused.ps1 -CheckOnly -Output .local_runtime/cloud-focused-review
```

需要本机历史冻结目录 `.local_runtime/cloud_calculation6_20261020` 中的原题库、`scoring_review_v2.py`、`scoring.py`、`support.py`、`freeze.json`、`oracle.json`，以及 `.local_runtime/Real Rebuild Room/FinSight/data/runtime/real-rebuild-ocr` 的已验收事实与索引清单。这些私有资产不因代码发布而自动分发。当前项目根目录由运行器 `__file__` 决定，迁入主项目后仍使用主项目当前代码；不会运行旧 `support.setup` 或 `check_frozen`。

## 启动隔离服务并运行

本次本机检查时MySQL可用，真实库与索引核对通过；Redis的16379端口、RabbitMQ的15672端口未监听。没有自动启动或重启它们。运行前先用已有安装包启动这两个本机依赖（脚本通过PATH寻找已安装Redis，复用项目内已有Erlang/RabbitMQ）：

```powershell
pwsh -NoProfile -File scripts/start-native-dependencies.ps1 -EnvFile '.local_runtime/Real Rebuild Room/FinSight/.env'
```

该命令准备现有本机节点，不能用于替换正在其他配置下运行的节点。原端口已被占用时先确认其身份，不停止未知服务；后续测试只使用专用Redis数据库号和RabbitMQ vhost。

私有环境文件保留现有真实库、独立 SELECT 账号、本地 BGE 和现用索引；只在启动进程中覆盖隔离业务配置，不修改环境文件。此电脑默认选用`.local_runtime/Real Rebuild Room/FinSight/.env`；不存在时才选主目录`.env`，且必须通过真实库身份检查。Python 3.11 环境、Maven/JDK、MySQL、Redis、RabbitMQ 须已安装。MySQL 管理账户需能创建下面的专用业务数据库；脚本从PATH或项目内已有的`.local_runtime/native/rabbitmq`找到RabbitMQ工具，并复用已准备节点的私有cookie，只准备专用vhost和该vhost的权限。云端密钥读取私有环境或通过隐藏输入提示提供，不写入输出文件。

```powershell
pwsh -NoProfile -File scripts/test-cloud-focused.ps1 -Output .local_runtime/cloud-focused-review
```

默认 Python 端口 19300、Java 端口 19380。脚本拒绝已被占用的端口，不停止或附着未知服务；应用使用隐藏窗口，日志和监听进程的 PID/启动时间留在本次输出。测试服务保留供检查与续跑，脚本不会停止日常服务或共享依赖。运行前确认没有另一个同命名空间的复核批次正在执行。

| 范围 | 隔离方式 |
| --- | --- |
| 聊天、账号、任务业务表 | `finsight_cloud_alignment_20261020`，只创建这个新业务库并由 Java 迁移 |
| 财务读取 | `SQL_DB_NAME=finsight_real_rebuild_20261002`，沿用现有独立只读账号，不复制/导入原财务库 |
| Redis | 默认 index 13，必须与日常 `.env` 不同；可用 `-RedisDatabase` 指定其他空闲 index |
| RabbitMQ | `/finsight-cloud-alignment-20261020` 独立 vhost |
| Python 任务与调用记录 | 本次输出下的 `tasks.sqlite3`、`model-audit.jsonl` |
| BGE 与资料 | 继续使用真实 117 份 PDF、规范事实、现用 BGE 模型及索引；不重建、不改向量空间 |

`.private` 内仅保存本批次登录凭据及测试 JWT/内部令牌，不能公开。输出不保存 DeepSeek API 密钥。分享逐题结果时排除 `.private`、任务库和原件；模型审计导出只含身份、调用种类、耗时和 token 用量，不含提示词或密钥。用量不等于报价，费用以实际账号条款和账单为准。

## 断点与首发结果

```powershell
pwsh -NoProfile -File scripts/test-cloud-focused.ps1 -Output .local_runtime/cloud-focused-review -UseExisting -Resume
```

`-UseExisting`只接受本脚本创建的`stack.json`，再次核对当前监听进程的PID、启动时间、启动时源码摘要、代码根目录及隔离命名空间，并核对Python服务实际返回的v3构建摘要。端口打开后还须通过HTTP就绪，不能仅凭端口监听开始测试。服务已退出时可省去`-UseExisting`，在端口空闲且冻结版本未变的情况下启动隔离服务续跑。源码、题库、评分程序、原页标准或数据摘要变化会拒绝续跑，应使用新输出目录，不能覆盖已有首发结果。

每题先独占写入完整 `answers/<题号>.json`，再调用旧独立评分入口 `score(..., entry='java')`。评分器异常仍保存回答并记录待复核错误；已保存首发回答与首轮评分不会被重跑覆盖。中断请求保留原客户端请求 ID，续跑沿用同一 ID 由服务端幂等恢复。已完成轮次不再提交。多轮只传真实保存的 session UID，不注入历史文本或预制模型上下文。

`records.jsonl`、`checkpoint.json`、`summary.json` 和 `answers.html` 是可重新生成的汇总视图。原始答案和分数是不可覆盖的记录。全部题目保留独立正文复核状态；K05V1/V2/V3 额外要求核对原始两年金额、万元单位及差额，不能因旧评分器只检查了差额而判全部通过。自动检查通过数与最终确认通过数分别列示，未复核不计最终通过。

## 生命周期范围

第一道正式题在收到任务身份后断开 SSE，并通过任务状态轮询等待真实保存；不新增或重复该题。完成后读取各会话的实际消息、再次轮询终态，再切换会话并回读，检验 API 层的刷新和会话恢复。此处不声称浏览器画面已验收。

默认额外的取消问题在约0.2秒后发送取消，检查保存的取消终态、后续轮询无迟到财务结果及本机调度active/waiting回到0。最少观察3次，若仍有后台标题则最多等15次轮询；期间任何取消终态被改写或迟到财务结果都会失败，不能仅凭最后一次状态抹掉中间错误。若生成先完成，则保留原结果并报告竞争未定，不重发到成功，不伪造取消通过；本地取消不证明服务商计算或计费立即停止。可显式使用`-SkipCancelProbe`跳过并将其记为未执行，不能写成已通过。

模型审计中的标题调用与规划/审核等调用分别计数。生命周期结果保存在 `lifecycle-sessions.json`、`lifecycle-cancel.json`，与正式 28 轮评分分开；模型调用身份和用量汇总见 `model-usage.json`。这些是待用户实际执行的检查，不是本次迁移已经通过的证据。

OCR检查读取已验收的真实PDF及其原始PaddleOCR JSON缓存，并再次核对二者SHA-256。它使用生产`ETLWorker`的元数据提取及正常规则优先表格提取入口，最多2次生成，不调用完整`run()`、保存、索引或发布函数。表格可能由规则完成，应以`extraction-smoke.json`的实际`table_route`和调用数为准；不能把该样例写成117份报告重建或完整RAG验收。首次尝试中断时不会自动重发该额外检查。数值输出保留待独立复核，不自动判全部正确。

完成后可只停止本脚本创建的两个隔离应用进程：

```powershell
pwsh -NoProfile -File scripts/stop-cloud-focused.ps1 -Output .local_runtime/cloud-focused-review
```

停止脚本核对PID和启动时间，拒绝终止PID已被复用的进程；不会停止日常服务、Redis、RabbitMQ或电脑上的模型服务。

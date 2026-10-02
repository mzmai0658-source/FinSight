# v3 请求、任务与本机运行

生产入口为 `src/api/main.py` → `src/agent/v3/agent.py`，LangGraph 顺序是单次紧凑理解、程序条件检查、确定性执行、复杂解释证据审核、分项核验、发布。旧语义实现只供历史实验，不是理解失败后的回退。本文描述当前实现，不代表全部验收已经通过。

## 契约

`src/agent/v3/contracts.py` 是数据定义的来源。运行 `python scripts/generate_v3_contracts.py` 同步生成 `shared/finsight-v3.schema.json` 与前端 TypeScript。请求、条件修改、状态、事实、分项目标、核验、任务均为 version=3；不认识的版本不能升级成已核验结果。

每个目标使用 `condition_edits` 保存本轮局部例外，沿用独立旧目标时填写 `context_goal_id`，由程序复制已确认的条件快照。`selection` 仅供旧v3消息兼容。共享编辑与局部编辑执行同一套替换、添加、删除、仅保留、沿用和清空规则。时间的 `pairs` 表示精确的年份与报告期配对，避免多年份与多报告期形成多余组合。否定与排除约束属于整轮要求，目标不能自行取消。

`context_references` 保存本轮原话中的公司、事实或财务任务引用及单复数；多个候选不能被单数代词任意选中。`Goal.clarification` 只阻塞该目标，已经明确的其他目标可以完成，结果仍按分项状态判断。规划上下文只使用版本化状态，不用取消或失败的旧用户问题给新问题添加任务。

`Request.continuity`明确记录new、continue、resume、clear。生产Planner仅调用一次TurnPlan：任务目标、本轮逐叶编辑、上下文引用、局部例外和真实澄清一起提出，编译到公开v3请求。原生Ollama传输把编辑表示为字段到操作数组的映射，程序登记本轮原话和目标编号。各任务类型始终可选，不按关键词删除任务分支；公司、指标、单位与显式值受登记目录和JSON合同限制。重复JSON键、同字段重复最终替换均拒绝；集合的多次添加/删除允许顺序执行。普通查询不调用同模型布尔审核；程序检查目录、显式条件、引用、修改与执行结果，无法证明所有自由语义。复杂经营解释才审核证据与结论。结构或编译失败共用最多一次修正机会，正常明确查询目标为一次模型调用，整轮仍受六次调用、单次60秒与总时限限制。不存在旧语义回退。

年份在模型采样和程序类型校验中均限制2000—2100；“24年”必须输出2024，不能把24当作合法财务年份。季度报告的“比上年末变化”独立保存为reported_change_vs_year_end，不能以reported_yoy发布。失败诊断仅保存普通JSON类型，不调用异常对象的str/repr，校验错误不能使失败终态无法保存。

事实的 `value` 与 `value_exact` 为十进制字符串。单位是元、百分比或元/股；仅展示时换算与舍入。图表的浮点数组用于 ECharts，`values_exact`/`value_exact` 和事实引用保留准确值。一个任务固定使用一个已验收的事实与索引版本。

同比查询的上年同期基础值单独进入核验输入，不能冒充本轮请求年份。计算结果默认只展示所问差额或变化率；`presentation.include_inputs=true` 才同时展示基础数。增长率图表由计算结果生成，并逐点重建核验。零基期结果为null及未定义，不补零。排名按请求的原始值或计算值排序后再截取数量。

`DialogueState.recent_computed` 保存受验证的计算配方、两个基础事实ID和固定数据版本；再次引用时重新查登记事实并复算，旧答案文本不提供数值。没有同时展示的基础金额不成为单数“这个数”的候选。失败、取消、清空话题及新的财务执行清理旧事实与计算引用。

`DialogueState.interrupted_request` 单独登记失败或取消的请求。新增uncertain_paths只记录未确认修改的条件路径，后续明确修改相应字段即可解除；旧记录没有该字段时保守确认公司与指标。请求审核通过后，Python在执行前原子保存条件检查点。SQL失败、取消和重启保留已确认条件，清理事实与计算引用，并记录失败执行；检查点和终态不能被迟到事件覆盖。概念插话仅暂存财务条件。本轮条件的clear只影响对应请求；删除历史记忆须使用`memory_edits`，其中company_context清空所有可继承公司引用，financial_context清空财务任务及引用。两种操作均保留可读的实际执行历史；历史记录不能重建被清掉的条件。

共享和目标条件完成合并之后，`condition_defaults.finalize_conditions` 统一确定默认时间策略和比较维度；明确选择的共同最新、各自最新及计算方向保持原样。报告名称与年份分别校验，最新策略必须有本轮依据，不能借修改年报清除原年份。

失败诊断保存调用数量、耗时、经过类型校验的规划提案及程序纠错反馈；格式错误仅保存不含输入值的类型错误位置。整轮最多一次格式修正，仍计入六次调用预算，不改变原话，也不转成用户澄清。不保存驱动异常文本、连接设置或模型原始输出。诊断仍是定位问题的记录，不能当作审核通过或执行完成证据。

原生传输枚举与正式v3类型一一对应。紧凑目标只有一个wire形状，kind确定适用的concept_mode、quote_mode或catalog_target；解码只丢弃不适用的附带模式，不丢弃目标或财务条件。生产使用紧凑TurnPlan传输，历史实验实现已移出交付目录。程序条件检查保存audit_mode=program，前端显示“程序条件与来源检查通过”，不能称独立模型认证了整轮语义。diagnostics记录每次实际响应model、step、tokens、耗时、model_calls和结构修正原因，实际执行完成与证据支持仍分别判断。

取消与完成竞争时，Java保存Python已经取得的真实终态。Python已完成的任务不能因随后收到取消请求而被Java改标为取消；完成、失败及取消各自完整保存后才释放会话锁。

`Source.original_cell` 可登记原PDF数值、行名、报告期表头、单位及跨页上下文的物理坐标。发布前按文件哈希、原文、原始单位与精度重新核验。`comparisons` 保存同口径比较双方事实ID及greater/less/equal，由Decimal程序生成；Java完整保存在消息元数据。

原件按SHA-256保存在`data_root/original_versions`，登记的资产ID和旧引用绑定原件字节版本。上传路径被替换或删除后，核验与PDF网关仍读取对应保留版本。前端优先使用已登记资产ID；公司、年份和报告期只作为缺少资产ID时的兼容入口。

Java 将用户端请求编号映射到固定任务 ID；Python 把任务与事件写入工作区 SQLite。截止时间均为 Unix 毫秒。刷新和 SSE 断连不会取消后台任务；只有取消接口或服务截止时间可以终结执行。

## 用户端接口

所有接口须登录并校验会话及任务归属。

| 接口 | 用途 |
|---|---|
| POST `/api/chat/stream` | question、可选 sessionUid、clientRequestId；提交或恢复同一任务 |
| GET `/api/chat/tasks/{taskId}` | 状态、截止时间、saved、result、error |
| GET `/api/chat/tasks/recover?clientRequestId=...` | 连接中断时按请求编号找回任务 |
| GET `/api/chat/sessions/{sessionUid}/tasks` | 恢复会话中的执行任务 |
| POST `/api/chat/tasks/{taskId}/cancel` | 归属校验后请求真正取消；以返回状态为准 |

同一客户端编号不能代表不同问题或会话。一个会话同时只能有一个活动任务；执行中删除会话返回 409。重复提交、重复 done 和迟到事件不会另保存一轮。保存失败为 saved=false，不得当作历史保存成功。

Java的`dispatch_started`只代表已取得唯一送出权；任务仍为queued，直到Python实际取得执行名额才变running。明确的`model_queue_full`拒绝直接保存失败终态并显示忙碌；未知传输异常继续恢复，不凭断连宣布后台任务失败。SSE和状态查询共用同一终态结果及错误原因。

SSE 保留 session、plan、tool_call、tool_result、answer_delta、chart、clarify、error、done 等名称。session 增加 task_id、version、deadline。处理进度可立即显示，财务正文及图表只在分项核验后的 done 结果发布。

Python 内部任务接口使用 `/internal/tasks/{id}`、`/internal/tasks/recover`、`/internal/tasks/{id}/cancel`、`/internal/tasks/{id}/saved`。内部接口需要服务凭据；`/internal/runtime` 只提供资源计数，不提供提示词或凭据。

## 预算和恢复

- 整轮 270 秒；Python 最多 240 秒，同时为保存预留时间。
- 模型最多六次调用，单次六十秒且受剩余总时限约束；预留十秒核验与保存，没有无限重试或旧规则回退。
- 应用模型资源同时执行一个调用，最多八个等待者，排队三十秒。同步 ETL、异步问答和后台标题共用调度入口。
- 本机 Python 服务采用单 worker；多 worker 或多服务实例需另行共享调度，不能声称进程内锁保证全局限制。
- Python 重启明确终结无法恢复的活动调用；Java 从持久记录找回状态，不永久占会话锁。Java 与 Python 暂时失联时继续恢复任务状态。

## 本机公开演示启动（PowerShell 7）

保留本机 MySQL 和 Ollama。Redis、RabbitMQ 使用真实本机服务，预检执行 Redis AUTH/PING 与 AMQP 握手及建队列。RabbitMQ 的 AMQP 端口以环境配置为准，不能把管理页面端口当作就绪证据。

```powershell
pwsh -File scripts/start-native-dependencies.ps1 -EnvFile .env.demo
.venv/Scripts/python.exe -X utf8 scripts/preflight.py --env .env.demo
pwsh -File scripts/start-finsight.ps1 -EnvFile .env.demo
```

`.local_runtime` 保存服务日志与进程编号。使用隐藏窗口启动，启动入口需要先通过协议预检。不同端口可用 AgentPort、JavaPort、FrontendPort 参数。`FINSIGHT_TASK_DB` 可指定工作区内独立任务库，适用于隔离的故障实验；不得与另一运行实例共享。

公开初始化运行 `scripts/demo-seed.ps1 -EnvFile .env.demo`，同时生成PDF、事实、索引并验收发布。下述原件审计仅供具有合法私有语料的本机真实库维护使用，不是公开安装前置条件。新版本先在独立目录与集合中准备；审核失败或发布事务失败保留旧指针。未核实的单元格保留在报告核对清单，不补零。索引仅在文档位置、内容、元数据与 embedding 配置一致时复用已验收向量。

```powershell
.venv/Scripts/python.exe -X utf8 scripts/audit_canonical_facts.py --output data/runtime/v3/audit-new
.venv/Scripts/python.exe -X utf8 scripts/build_canonical_index.py --audit data/runtime/v3/audit-new
.venv/Scripts/python.exe -X utf8 scripts/accept_canonical_release.py --audit data/runtime/v3/audit-new --publish
```

旧消息保持可读；旧状态只迁移明确公司身份，未知报表范围重新确认。旧回答数字与旧核验徽标不能升级为 v3 事实或完整核验。

## 验收资产

Source.scope_proof 保存原件标题矩形、报表家族及行和值矩形的哈希。checked_source 同时复读这些区域，并检查原件全篇中最近的同类报表标题，不能只凭OCR表名判断合并/母公司。旧来源没有这项证明时只能继续查看历史消息，不能升级为新版可信事实。

comparisons 保存程序生成的比较对象和结论；query_trace 保存实际执行的参数化SELECT、参数、用途、行数、状态和耗时，禁止用固定SQL模板冒充实际执行记录。Python API、Java保存和前端恢复均保留这些字段。它们与数字正文一样以实际执行为依据，不包含连接配置或凭据。

`eval/v3_native_gpu_cancel.py` 同时观察真实GPU负载、任务终态、模型槽及后续查询。显存保留已加载模型不视为仍在推理；取消终态不会因后续任务或迟到事件重新完成。实验记录含采样和任务身份，不保存认证信息。

本机 `eval/regression_v3` 保存 153 轮内部历史回归问题、上下文与预期，该目录及其清单不随公开包发布；包含原件摘录的修复前记录同样只留本机。公开验收使用 `eval/delivery_cases.json` 和 `docs/evidence/final20` 的虚构演示问题与实测记录。`eval/run_v3_regressions.py` 对真实 Java、Python 和本地模型运行；`eval/v3_native_faults.py` 进行真实取消、归属、幂等、输入限制和 MySQL 保存失败实验。结果需独立断言与人工复核，不能按模型自报通过判成功。

尚未封存并通过两次新盲测时，不得宣称全面修复完成。结构化输出使用原生JSON Schema约束及同源紧凑字段说明，再次执行严格类型校验；本机Ollama 0.33.3的数组前缀约束实际实验失败记录保留，不能只凭schema合法判采样约束生效。基础机制参照 [Ollama 官方说明](https://docs.ollama.com/capabilities/structured-outputs)。

## 模型配置

启动脚本的Model仅覆盖当前进程；当前默认Qwen，实测问题使用隔离会话。当前公开Qwen成绩见[交付验收](DELIVERY_ACCEPTANCE.md)。Fin-R1只属前期研究，不纳入本轮成绩。

```powershell
pwsh -File scripts/start-agent.ps1 -EnvFile .env.demo -OverrideEnv -Port 18000 -Model qwen3.5:9b-q4_K_M
# 停止对应Python实例后，可使用相同服务端口运行Fin-R1
pwsh -File scripts/start-agent.ps1 -EnvFile .env.demo -OverrideEnv -Port 18000 -Model fin-r1:7b-q4
```

图形、排序和数量使用同源文字约束；公司纠正检查保留集合。显式删除会在任务提交时清理持久化上下文，包括all_companies，模型失败或取消不能恢复已删除公司。概念定义的指标集合独立于挂起的财务条件。

## 数据集身份

财务结果的 `dataset_profile` 保留 id、kind、reports、companies 与label。合成版本kind为synthetic，Java完整保存并由前端显示“虚构演示数据”；刷新恢复后也保留。旧消息无此字段时不追认其为新演示或新核验结果。金额以 `value_exact` 十进制字符串为准，兼容值不得代替精确计算。

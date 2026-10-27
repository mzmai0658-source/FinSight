> 历史版本资料：当前安装、模型和数据以 [INSTALL_CURRENT.md](INSTALL_CURRENT.md) 为准；本文旧期间/旧成绩不代表本次交付。

# 上传、审核与云端向量：隔离候选运行说明

> 2026-10-22主目录已采用新118份云端试验问答库，旧117份/BGE保留。主目录使用`.env`，本文`.env.upload120`命令属于独立重建工具；主目录启动及切换见[主项目运行说明](CLOUD118_MAIN_RUN.md)，实际数据与网页结果见[本轮记录](ROLLOUT_146_20261022.md)。

此文档是云端上传链路的工程运行说明。隔离候选位于`D:/data_discovery/.local_runtime/upload_cloud120_20261021/candidate`，旧117份原件及BGE索引继续本地保留。本轮允许漏提取和OCR失败；未核实数字不能标为完整核验通过。技术报告按本轮真实证据更新；没有执行GitHub提交或公开上传。

用户已选择新118份云端试验库作为默认，旧117份/BGE保留为独立历史配置。新默认仍是`experimental / relaxed_ocr`，使用`FINSIGHT_OCR_TRIAL=true`、`ocr-trial-v1`与独立`ocr_trial`指针，保留未完整核验提示。主目录资料迁移和定向页面验收已完成，范围见本轮记录，不能把默认选择写成118份已全部严格核验。

## 实际链路

普通用户登录后在“财报提交”上传PDF，Java检查文件、归属及100MB限制，通过MySQL任务与RabbitMQ调度Python。Python按PDF内容摘要建立处理资产，提交新的PaddleOCR异步作业；远端作业号和每日页数预先持久保存。中断只续查原作业，不重新付费提交。OCR输出保留JSON、MD、模型身份、页码和摘要。

DeepSeek从原件前部与新的MD识别公司、证券代码及报告期，程序再核实身份。从OCR表格提取的数字必须与PDF原件中的行、列、单位和报表口径对应。合并、母公司、归母、扣非分别登记，金额使用Decimal。已核实的子集可进入审核；没有提取到的字段、来源冲突和无法确认的单元格分别列出。

新的MD按段落和物理页切分，使用百炼`text-embedding-v4`的document/query约定生成1024维向量，进入独立Chroma候选集合。失败批次保留已完成向量。管理员确认公司身份、查看数字与缺项后批准，再发布选中的候选；识别成功不等于发布。

普通候选走`user-financial-v1`严格发布。本轮默认问答采用用户明确放宽的`ocr-trial-v1`，读取独立`ocr_trial`指针，保留`source_audit_complete=false`和未核验提示；它不会把原始失败/待审核记录改成管理员已审核。当前118份新OCR的事实、向量与小型问答进度见[OCR试验说明](UPGRADE120_OCR_TRIAL.md)。

发布时先完整准备版本，再将规范事实、兼容表、公司登记和现用指针一起提交。旧版本保留。失败不清空原库；重试旧发布回放原回执，不把已被更正版替换的原件重新激活。问答每轮从已发布目录读取公司并固定数据版本，新增公司不用重启Agent。历史公司名称保留在`original_company`和别名中，同一证券代码使用最新已确认名称展示。

## 新语料和评分范围

- 开发公司：美的集团、比亚迪、贵州茅台、长江电力、中国建筑、立讯精密。
- 保留公司：宁德时代、伊利股份、中国神华、三一重工。
- 每家2023/2024/2025年FY、HY、Q1、Q3，共120份。原件总15,107页，366,944,547字节，均来自官方披露链接；详见`data/runtime/cloud_upload120_sources.json`。
- 200轮固定问题：120个单问、20组四轮对话；开发120轮，保留80轮。题目在正式问答前冻结，不设及格线，不把既有题称为第三方盲测。
- 允许缺项继续评测时使用`--record-data-gaps`。没有提取出的支持指标计为数据/证据缺口，不改成“预期无数据”、不删题、不抬高分数。原因和概念正文先列待独立复核，未经复核不计通过。

## 私有配置与启动

本机Python：`D:/data_discovery/.venv/Scripts/python.exe`。以下命令在候选目录执行；也可以用脚本绝对路径运行。密钥只在本地隐藏输入并写入被Git忽略的`.env.upload120`，不要放进聊天、脚本参数、报告或前端。

```powershell
Set-Location 'D:/data_discovery/.local_runtime/upload_cloud120_20261021/candidate'
$py = 'D:/data_discovery/.venv/Scripts/python.exe'
& $py -X utf8 scripts/financial_upload_upgrade.py configure --source-env 'D:/data_discovery/.env'
& $py -X utf8 scripts/financial_upload_upgrade.py init
& $py -X utf8 scripts/financial_upload_upgrade.py start
& $py -X utf8 scripts/financial_upload_upgrade.py check
```

已启动后补充或修改密钥，执行`restart`加载新配置，只重启本候选的Python与Java。存在待执行/执行中上传时会阻止重启；不会停止日常服务、Ollama或电脑上的其他模型。Redis6391、RabbitMQ5673、Python8120、Java18280全部与日常端口分开。MySQL使用独立`finsight_upload120_20261021`数据库与独立只读账号；绝不指向日常财务库。

`init`可重复运行，也可在Java首次完成Flyway迁移后再执行一次，为新出现的资料表补只读权限。`start`复用本机已安装的Redis/Erlang/RabbitMQ可执行文件，但使用候选自身配置和存储；缺依赖时在私有配置填写`REDIS_EXECUTABLE`、`RABBITMQ_SERVER_EXECUTABLE`、`ERLANG_HOME`。

```powershell
& $py -X utf8 scripts/financial_upload_upgrade.py restart
# 可选，小型真实连接验证会调用一次向量接口与一次生成接口。
& $py -X utf8 scripts/financial_upload_upgrade.py check --probe-cloud
```

前端在`frontend`目录运行`npm run dev -- --host 127.0.0.1 --port 5199`。配置器生成只含公开API地址的`frontend/.env.local`；页面访问`http://127.0.0.1:5199`。普通用户与管理员账号保存在私有`.env.upload120`，不在日志或本说明中明文展示密码。

## 按顺序处理，不需要逐份手工运行

先用少量原件验证，再处理全批；同一命令可以续跑，已经完成的上传、OCR作业、身份提案和向量会复用。`--retry`只对已失败记录发起恢复，首次结果另存保留。连续两份新处理在服务阶段失败会暂停，避免网络断开时继续提交整批。

上传和评测脚本共用双令牌续期，登录过期后最多续期并重放一次；403权限拒绝不会触发续期。PDF重发先恢复文件读取位置，问答重发保持同一客户端请求ID。续期失败则停止并保留进度，不记录令牌、不重新注册用户。

```powershell
# 定向小样本；两份均为本轮真实原件，不是旧117份的缓存。
& $py -X utf8 scripts/financial_upload_upgrade.py upload --report-id 000333_2025_Q1 --report-id 002594_2025_Q1 --retry
# 全部120份，通过真实普通用户上传入口进入后台。
& $py -X utf8 scripts/financial_upload_upgrade.py upload --retry
& $py -X utf8 scripts/financial_upload_upgrade.py status
```

管理员可在网页逐份审核。测试时也可先检查候选清单，再用下面的批量确认：它核对官方原件清单、候选身份和程序核验，只批准符合条件的数字子集。身份不一致的候选跳过留待人工处理；这属于管理员使用程序辅助确认，不宣称团队成员人工逐数字审过。

```powershell
& $py -X utf8 scripts/financial_upload_upgrade.py review --confirm-identities
& $py -X utf8 scripts/financial_upload_upgrade.py publish
```

OCR提交响应丢失时不会自动再提交。先在Paddle控制台查到对应已有作业号，再绑定：

```powershell
& $py -X utf8 scripts/financial_upload_upgrade.py reconcile-ocr --candidate-id '原件64位SHA256' --job-id '已存在的作业号'
```

每日额度按本账号设置为20,000页，由持久台账控制；这是用户提供的账号额度，不保证任何账号均免费。已提交但未确认的作业也占预留页数。全量15,107页是否在当天完成仍取决于账号实际额度和远端处理情况。

## 最终200轮和查看每题回答

开发测试用单独目录，最多两轮针对性调整。冻结代码、模型设置、数据和索引后才执行最终完整200轮；保留公司结果出现后不再针对题目修改。

```powershell
# 可选开发检查；不与最终成绩拼接。
& $py -X utf8 scripts/financial_upload_upgrade.py evaluate --partition development --output '.local_runtime/upgrade120/development200'
# 最终完整200轮，默认输出final200，可续跑未执行的题，不覆盖首次答案。
& $py -X utf8 scripts/financial_upload_upgrade.py evaluate
```

运行通过真实Java/SSE入口，使用真实保存的上一轮历史。程序读取实际已发布版本，不能把某次历史发布的回执当作当前库。即使部分原件失败，仍保留200题分母，缺项明确归为数据/证据问题。

冻结记录只保存公开模型策略：服务、temperature、关闭思考、输出上限、调用超时和额外网络重试次数；不保存密钥或登录令牌。实际响应型号及调用仍从每轮服务端记录核对。

结果在`.local_runtime/upgrade120/final200/`：`answers.html`逐题页面，`records.jsonl`首次原始记录，`answers/`完整回答，`scores/`独立检查，`oracle.json`原件卡，`summary.json`统计。正式成绩只有运行完成并完成正文复核后才报告。本说明不预填通过率。

## 本地证据和恢复

原件在`data_root/financial_reports/new120/`，候选资产在`data/runtime/submissions/assets/<sha>/`，远端作业与审核在`data/runtime/submissions/state.sqlite3`，发布材料在`data/runtime/submissions/releases/<version>/`。运行日志与分阶段记录位于`.local_runtime/upgrade120/`；这些均不公开上传。

旧117份/BGE继续作为独立历史库保存；如需恢复使用，必须显式选择旧数据库、原件和缓存、BGE模型及旧索引配置，不能仅关闭OCR试验或修改集合名称。代码基线、原工作区文件摘要和本轮变更清单位于候选上一级`baseline/`、`baseline.json`及本轮执行记录。主目录的新默认迁移另按本轮最终记录核对。当前技术报告以本轮最终版本和实际证据为准；旧报告与旧200/264/324轮成绩保留历史身份，不转用为本轮成绩。

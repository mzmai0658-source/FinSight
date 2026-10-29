> 历史版本资料：当前安装、模型和数据以 [INSTALL_CURRENT.md](INSTALL_CURRENT.md) 为准；本文旧期间/旧成绩不代表本次交付。

# 36轮新问法测试

这批用来检查格式合同、失败恢复和常见口语变化。12个单问加6组完整四轮对话，共36轮；属于项目内新问法，不能写成第三方盲测。没有准确率及格线，首发结果全部保留，自动检查后还需独立复核正文。

题目和目标预期见 [cloud_followup36_cases.json](../eval/cloud_followup36_cases.json)，原件依据复用已经核对的真实财报卡。36轮中有比较、差额及原数、合并/归母/扣非/母公司、每股单位、缺报告、公司集合修改、临时展示恢复、概念插话、原页、正负前提、上下文清空和无关话题引导。不是324轮全量验收，也不专门评估经营原因RAG召回。

模型仍为DeepSeek Flash，向量仍用本地BGE；真实财务库只读，不重建数据或索引。新输出目录、用户会话、任务记录与旧28轮分开。沿用测试专用的业务库、Redis号和RabbitMQ vhost，因此须先关闭此前同一命名空间的隔离应用，避免两套消费者同时工作。下面的停止命令只接受旧测试marker中相同PID及启动时间，不停止日常服务或共享依赖。

此电脑PowerShell 7没有加入PATH，请在 `D:\data_discovery` 使用已验证的绝对路径：

```powershell
$ps7 = 'C:\Users\21754\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\powershell\pwsh.exe'
& $ps7 -NoProfile -File 'D:\data_discovery\scripts\stop-cloud-focused.ps1' -Output 'D:\data_discovery\.local_runtime\cloud-focused-review'
& $ps7 -NoProfile -File 'D:\data_discovery\scripts\test-cloud-focused.ps1' -SuiteProfile followup36 -Output 'D:\data_discovery\.local_runtime\cloud-followup36-review'
```

首次运行会隐藏输入云端密钥。不要把密钥放进命令行。新目录尚未完成过的题会顺序提交；多轮只使用真实保存历史。默认另外检查一次取消、刷新与会话回读，生命周期结果单列，不计入36轮；会话标题可能额外调用模型。本批不重复OCR抽取冒烟。

若共享依赖没有运行，先执行原有 `scripts/start-native-dependencies.ps1`，参数仍选择 `.local_runtime/Real Rebuild Room/FinSight/.env`。若端口被未知进程占用，脚本会停止启动并说明，不强杀进程。

不发API请求的检查命令：

```powershell
& $ps7 -NoProfile -File 'D:\data_discovery\scripts\test-cloud-focused.ps1' -SuiteProfile followup36 -CheckOnly -Output 'D:\data_discovery\.local_runtime\cloud-followup36-review'
```

中断后在代码、题目、数据版本不变时续跑：

```powershell
& $ps7 -NoProfile -File 'D:\data_discovery\scripts\test-cloud-focused.ps1' -SuiteProfile followup36 -UseExisting -Resume -Output 'D:\data_discovery\.local_runtime\cloud-followup36-review'
```

若隔离应用也退出，确认旧监听已经结束后去掉 `-UseExisting`。不要在恢复同一目录时换源码或题目；冻结摘要不同会拒绝续跑，不能覆盖首发失败。

完成后打开 `.local_runtime/cloud-followup36-review/answers.html` 查看每道问题和完整回答。`answers/*.json` 是首发原始记录，`scores/*.json` 是首轮自动检查，`records.jsonl` 是汇总，`model-usage.json` 记录真实型号和调用数。含有私有会话信息的 `.private` 和任务数据库不可公开。

本批新增检查会验证临时单位是否真的恢复、长期偏好是否保存，以及明确要求的差额原数是否交付。概念、引用和引导的语言仍由本助手逐项复核，不让被测模型自行打分。36轮正式云端尚未运行时不填写准确率；原28轮复核25/28是此前版本的固定回归成绩，不能冒充本批成绩。

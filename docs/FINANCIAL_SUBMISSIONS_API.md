> 历史版本资料：当前安装、模型和数据以 [INSTALL_CURRENT.md](INSTALL_CURRENT.md) 为准；本文旧期间/旧成绩不代表本次交付。

# 财报提交与审核接口（候选实现约定）

本轮隔离目录：`.local_runtime/upload_cloud120_20261021/candidate`。不启动或修改日常服务，不提交 Git，不更新比赛 PDF。

## 分工与边界

Java 持有用户身份、原始上传与已有 `etl_task` / RabbitMQ 调度；Python 持有按内容摘要去重的处理资产、候选事实、审核记录、候选索引和发布版本。普通用户不能调用管理员操作。所有财报入口进入同一候选链路；识别完成不能自动发布。

## 公共接口（Java，统一 ApiResponse 包装）

- `POST /api/financial-submissions`：multipart `file`，仅 PDF，登录用户提交；返回 SubmissionView。
- `GET /api/financial-submissions?page=1&size=20`：仅本人记录；管理员仍通过管理接口读取全库。
- `GET /api/financial-submissions/{id}`：仅本人或管理员；包含状态和候选摘要，不返回服务器文件路径。
- `POST /api/financial-submissions/{id}/retry`：仅本人或管理员；只重试可恢复的处理失败。
- `GET /api/financial-submissions/{id}/original`：仅本人或管理员，返回 `application/pdf`，前端取得带登录凭据的 Blob 后预览。响应保留 `private, no-store` 和 `nosniff`；文件名由提交详情提供，不发送 `Content-Disposition`，避免本机浏览器文件响应拦截导致加载挂起。未发布文件不能通过共用资料接口读到。
- `GET /api/admin/financial-submissions?page=1&size=20&reviewStatus=WAITING`：管理员列表。
- `GET /api/admin/financial-submissions/{id}/candidate`：管理员候选详情，代理 Python。
- `POST /api/admin/financial-submissions/{id}/review`：`{decision:"approve"|"reject",reason:"...",identity:{stock_code:"000333",company:"美的集团",year:2025,period:"FY",aliases:[]}}`。批准必须明确确认身份；拒绝必须写原因。
- `POST /api/admin/financial-submissions/publish`：`{ids:[123,124]}`，只发布本次选中的已批准候选；保留当前版本中未被明确替换的报告。返回 `{version,reports,facts,collection}`。批量选择不绕过逐文档核验。

`SubmissionView`：`id,taskUid,fileName,status,reviewStatus,candidateId,sourceSha256,stockCode,company,reportYear,reportPeriod,message,createdAt,startedAt,finishedAt,publishedVersion`。未得到候选时相关字段为 null。`status` 沿用 ETL 状态 PENDING/RUNNING/SUCCESS/PARTIAL/FAILED；`reviewStatus` 为 PROCESSING/WAITING/APPROVED/REJECTED/PUBLISHED。SUCCESS 仅指处理完成。

原 `/api/admin/etl/upload` 保留，财报与普通用户提交走同一服务及候选处理。研报行为不变。

## 内部接口（Python，仅内部凭据）

- 原 `POST /internal/etl/run` 的 financial 分支改为准备候选。输入不变。返回原 PipelineReport 字段，并附加 `candidate_id,source_sha256,review_status,company,report_period`；`stock_code,report_year` 沿用原字段。处理成功返回 `status:"success"`、`review_status:"WAITING"`。
- `GET /internal/financial-submissions/{candidate_id}`：候选详情。
- `POST /internal/financial-submissions/{candidate_id}/review`：公共 review body 加可信 `operator_id`，执行原件身份与质量检查并保存审核。
- `POST /internal/financial-submissions/publish`：`{candidate_ids:["sha256",...],operator_id:123}`，返回发布回执。无已验收云端发布版本时允许初始化；存在不兼容的旧向量版本时阻断，不丢弃旧资料。

候选详情至少包含：`candidate_id,status,review_status,source_sha256,identity,page_count,steps,facts,excluded,narrative_count,index,review,quality`。`facts` 为 v3 Fact 对象；`excluded` 区分未披露/无法识别/待核验/冲突。步骤为 `{name,status,message}`，不放凭据。

## 基础约束

候选以内容 SHA-256 去重；状态持久化；OCR 任务 ID 提交后立即保存；中断只能续查同一任务。新公司目录来自当前已发布报告，不缓存为整个进程的固定名单。财务数字必须通过现有来源核验；审核操作不提供绕过核验的开关。发布同时固定事实、索引和目录，失败不改变当前指针。

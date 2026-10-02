# 真实财报运行前提与本轮复核

本轮只使用已有的117份真实PDF，不执行`demo-seed`、`bootstrap_demo_v3`或合成报告生成器。历史演示工具不属于本轮数据准备和评测入口。

## 安装代码依赖

本机验证环境为Windows、Python3.11、PowerShell7、Node.js、JDK/Maven。模型、Python/Node/Maven依赖需要网络下载或合法缓存。本轮复用了已有Qwen和BGE缓存，不宣称验证了无缓存权重下载。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -X utf8 -m pip install -r requirements.lock.txt
npm --prefix frontend ci
Copy-Item .env.example .env
ollama pull qwen3.5:9b-q4_K_M
```

编辑私有`.env`，填写本机数据库、只读查询账号、Redis、RabbitMQ及模型配置。不得把密码、JWT密钥或内部令牌写入公开源码。先按运行维护说明准备真实本机依赖，再做预检与启动。

## 真实数据不是单独放几份PDF就绪

服务需要相互一致的原件目录、规范事实、来源证据、叙述索引、验收记录和发布指针。原件身份按内容SHA-256登记；母公司和合并数字分开；原始单位、行列、物理页及派生输入关系保持一致。已有事实依赖的OCR与来源注释不能用未验证缓存替换。

当前原件、OCR缓存和来源审核资产尚未公开。因此，这份说明能用于理解和复核本机流程，不能声称此前公开仓库单独下载即可重建本次真实数据版本。后续确定真实数据交付范围后，需要从正式提交包另外做干净环境安装；该步骤尚未完成，不用历史合成安装证据替代。

本轮标准和结果使用固定题库`eval/real200_cases.json`。候选事实先重新核对来源、口径、单位、期间、公式与兼容投影，叙述向量复用也检查文本、位置和配置，最后原子发布。以下命令中的路径应指向已经准备好的真实候选资产；不自动猜缺失目录。

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/build_canonical_index.py --env .env --audit <真实候选目录> --reuse-collection <对应旧版本集合>
.\.venv\Scripts\python.exe -X utf8 scripts/accept_canonical_release.py --env .env --audit <真实候选目录> --publish
.\.venv\Scripts\python.exe -X utf8 scripts/preflight.py --env .env
pwsh -NoProfile -File scripts/start-finsight.ps1 -EnvFile .env
```

全量验收失败时不切换发布指针；一个任务固定使用同一已验收数据和索引版本。数据、单位或指标目录变更要生成新版本，不能改旧摘要放行。原件缺失、未识别和有依据的未披露分开记录。

## 200轮复核与停止

具体运行、标准生成、独立评分与冻结规则见[200轮方法](REAL200_ACCEPTANCE.md)。测试通过真实Java/SSE入口、多轮实际保存状态，不直接调用执行器替代问答。账号仅用于隔离验收，凭据不在公开证据中。

停止应用使用`pwsh -NoProfile -File scripts/stop-finsight.ps1`，不会关闭共享MySQL/Ollama/Redis/RabbitMQ。临时断网与刷新继续后台任务；停止生成通过任务取消接口处理，不能用断开订阅冒充取消。

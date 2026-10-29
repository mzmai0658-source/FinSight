> 历史版本资料：当前安装、模型和数据以 [INSTALL_CURRENT.md](INSTALL_CURRENT.md) 为准；本文旧期间/旧成绩不代表本次交付。

# 当前真实数据快速入口

当前 FinSight 使用 10 家公司、117 份真实财报，所有生成任务调用 DeepSeek `deepseek-flash`，BGE 向量继续在本机执行。旧 `demo/` 虚构报告及合成初始化属于历史演示，不是当前数据安装、原页核验或验收成绩的入口。

首次安装按[真实资料安装与重建](REAL_DATA_INSTALL.md)准备依赖、私有云端凭据和真实资料包。已有数据环境按[部署说明](DEPLOYMENT.md)更新 `LLM_*`，保留原件、事实、发布指针与 BGE 索引；无需本地生成模型或重建数据。

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\runtime_env.py --env-file .env
.\.venv\Scripts\python.exe -X utf8 scripts\preflight.py --env .env
pwsh -NoProfile -File scripts\start-finsight.ps1 -EnvFile .env
```

准确配置、网络预检、服务端口与停止方式见[运行维护说明](DEPLOYMENT.md)，问答测试见[评测说明](../eval/README.md)。云端版本的正式成绩尚未取得，28 轮批次尚待用户运行；历史技术报告和本地模型成绩不证明当前云端效果。

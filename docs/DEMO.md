# 公开演示安装指南

本指南面向收到 ZIP 的评委。解压目录可以含空格；在目录内打开 **PowerShell 7（pwsh）**，不要使用 Windows PowerShell 5.1 调用统一启动入口。无需真实财报、OCR 缓存或私人数据库。

## 1. 外部服务与工具

安装 Python 3.11、Node.js、JDK 17 及以上、Maven 3.9、PowerShell 7、MySQL（本轮实测9.3.0）和 Ollama。先启动 MySQL、Ollama、Redis、RabbitMQ，使用本机回环地址。Ollama 桌面服务已运行时不重复执行 `ollama serve`。

RabbitMQ 可使用已安装的本机服务，或执行 `python scripts/prepare-native-packages.py` 下载固定摘要的 Erlang/RabbitMQ，再设置 `REDIS_EXECUTABLE` 并运行 `pwsh -File scripts/start-native-dependencies.ps1 -EnvFile .env.demo`。该脚本不安装 MySQL 或 Ollama。Redis 的来源与许可由使用者核对，本机版本列在第三方清单。端口被占用时先核对已有服务，不自动关闭无关进程。

工具须能从当前终端 PATH 调用。磁盘预留模型、Python、Node、Maven 缓存空间；模型按机器内存/显存运行，不承诺所有硬件具有相同延迟。

## 2. 安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -X utf8 -m pip install -r requirements.lock.txt
npm --prefix frontend ci
mvn -f backend-java/pom.xml test
Copy-Item .env.demo.example .env.demo
```

包源访问失败时可配置可用的公开镜像，锁定版本不改变。UTF-8 模式避免中文注释在 Windows 上按 GBK 解码失败。首次运行独立下载 BGE embedding 权重；缓存可复用，但记录不能冒充无缓存下载。

## 3. 私有配置

编辑 `.env.demo` 中 `DB_*` / `MYSQL_*` 的管理账号、密码和库名，以及 Redis/RabbitMQ 连接。默认 `finsight_demo`；只允许本机 `finsight_demo` 或 `finsight_demo_*` 执行公开初始化。两组 MySQL 变量保持一致，Python 和 Java 指向同一库。

初始化会生成独立 `fs_demo_r_*` 只读查询账号、内部令牌，并将模板 JWT/管理员密码换成随机值。管理员密码仅从自己的环境文件查看；用户可以注册。问答账户仅有 SELECT，Java 的业务写入与 ETL 使用另一个账号。不要在截图、反馈或提交包中公开环境文件。

## 4. 初始化与检查

```powershell
pwsh -NoProfile -File scripts/demo-seed.ps1 -EnvFile .env.demo
.\.venv\Scripts\python.exe -X utf8 scripts/verify_demo.py --env-file .env.demo
.\.venv\Scripts\python.exe -X utf8 scripts/preflight.py --env .env.demo
```

生成 `data_root/synthetic_demo_v3/` 的 15 份 PDF，及 `data/runtime/demo-v3/` 的事实、坐标、摘要、覆盖与验收清单；向量位置由 `CHROMA_DB_PATH` 指定。公司为星河医药、晨光医疗、青禾生物、远山诊断、云杉科技，均属虚构身份。

生成、索引或验收失败不切换发布指针；同版本重复初始化不重复写事实或片段。变更指标定义会得到新版本并重新验收。真实历史版本不被重写。

`-SkipModelPull` 适用于已有模型；不能因此宣称无缓存下载成功。`-SkipDatabase` 仅适用于已经验收发布的同一演示库。

## 5. 启动与停止

```powershell
pwsh -NoProfile -File scripts/start-finsight.ps1 -EnvFile .env.demo
# 作品说明：端口冲突时选择三个空闲端口。
pwsh -NoProfile -File scripts/start-finsight.ps1 -EnvFile .env.demo -AgentPort 19000 -JavaPort 19080 -FrontendPort 5276
pwsh -NoProfile -File scripts/stop-finsight.ps1
```

统一入口预检真实协议，等待服务就绪后给出地址。日志与 PID 登记在 `.local_runtime/`；停止仅处理此目录登记且启动时间一致的进程树，依赖服务单独维护。

注册登录后可问：“星河医药2024年合并营业收入，用亿元”“比较星河医药和云杉科技2024年收入”“库内公司2024年归母净利润前三名”“星河医药2022到2024年收入画柱状图”。原件链接打开 PDF 原页，回答显示虚构身份。刷新和断网不取消后台任务，“停止生成”向服务端取消并保存状态。

## 6. 故障定位

| 症状 | 处理 |
| --- | --- |
| 安装解码失败 | 加 `-X utf8`，不要擅自改锁版本 |
| 模型不存在或不可达 | 核对 `ollama list`、11434 端口和别名，运行 `ollama pull qwen3.5:9b-q4_K_M` |
| 数据库初始化失败 | 核对管理账号、库名限制及 CREATE 权限；问答须独立只读账号 |
| 来源/版本验收失败 | 看审核清单，修正定义后初始化，不手改摘要或指针 |
| BGE 下载失败 | 检查公开源网络或合法缓存，换模型后必须重建索引 |
| Java 无法启动 | 看启动 stderr，确认 Maven/JDK、Redis/RabbitMQ、MySQL 迁移权限 |
| 跨域失败 | 核对端口；浏览器地址应加入 CORS 配置 |
| 保存失败 | 应显示未保存；用任务 ID 查服务端，不能把显示过当落库成功 |

整套真实联调仅覆盖 Windows；Shell/Docker 文件作为配置参考，未完成同等级验收。

若与其他 FinSight 实例共享 Redis/RabbitMQ，请使用空的 `REDIS_DATABASE` 和独立 `RABBITMQ_VHOST`，由消息队列管理员先创建并授权该 vhost。启动预检会对指定空间完成实际认证。本轮隔离验收使用 Redis DB 13 和独立 vhost，不复用原系统配额或任务队列。

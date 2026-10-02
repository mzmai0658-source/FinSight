# 当前公开演示验收

生产默认链路为v3与Qwen3.5-9B，公开数据为5家虚构公司、15份PDF、240事实。按 [安装指南](../docs/DEMO.md) 准备真实服务。当前冻结20轮结果与限制见 [交付验收](../docs/DELIVERY_ACCEPTANCE.md) 及 [逐题记录](../docs/evidence/FINAL20.md)。本组是公开固定工程回归，不是盲测或比赛规定。

`delivery_acceptance.py` 通过真实Java/SSE与实际保存状态运行；不直接注入准备好的历史。指定实际服务base、新的output目录以及模型身份，输出目录不可覆盖。默认端口为前期实测端口；在公开默认部署中应传入 `--base http://127.0.0.1:8080`。

```powershell
.venv/Scripts/python.exe -X utf8 eval/delivery_acceptance.py --model qwen3.5:9b-q4_K_M --base http://127.0.0.1:8080 --output data/runtime/delivery/new-run
```

`delivery_lifecycle.py`、`delivery_component_faults.py`、`delivery_terminal_race.py` 验证真实取消/恢复/归属/保存与限定数据库故障；**仅限本机隔离的 finsight_demo_ 数据库**，不能对真实或其他数据库注入故障。delivery_component_faults中模型和数据库连接中断使用独立原生Python组件，不宣称Java端到端覆盖。故障脚本端口与环境以源码明确配置为准；前端脚本需要单独启动隔离Chrome调试会话。浏览器脚本不能同时共享一个profile执行，避免相互修改登录状态。

旧60题Markdown演示及五模式 `run_eval.py`、30题 `local_model_evaluation.py`、153题历史回归和v3_native工具供研究回归引用保留，公开初始化不使用旧事实。这些资产不属于本轮最终成绩；依赖私有原件的完整回归不能在公开包中冒充已复现。

评分为独立程序按冻结预期检查公司、指标、口径、时间、单位、数值、图形、原页及保存状态；合法澄清与明确不支持可以通过，支持范围内未完成仍失败。不得把模型自报审核或只验证来源文件存在当作正确完成。

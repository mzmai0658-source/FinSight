# AIC-2026-96123955最小复现

版本：FinSight-AIC-2026-96123955-r1。2026-10-26已恢复并执行首次问答，后续入口核查见[本次验证记录](SUBMISSION_VERIFICATION.md)。本说明使用真实的既有严格资料，验证安装和基础路径；不重新做全量OCR，不作为新的准确率评测。

## 1. 资料与环境

源码从本次冻结提交获取。依赖和服务按[安装说明](INSTALL_CURRENT.md)准备。数据库、只读账户、Chroma目录、任务存储须独立；已有MySQL安装和依赖下载缓存可复用。

外部资料包名为`AIC-2026-96123955_minimal-data.zip`，由团队提供网盘链接。未提供链接前，GitHub源码本身不能生成该财报库。压缩包和解压后的`transport.json`摘要记录在交付回执及`validation/minimal-reproduction.json`中。

资料包保留完整既有严格release，3份报告、106条事实、57个1024维向量片段，版本为`ce133135ef5308bf7b73efd6ebcc1665e761250f8fa6c8888e7f608affae4e95`。不含119份试验库或用户/会话表，也不包含密钥。`accepted`与`verified`沿用原版本，不表示本次重新人工审计全部106条事实。

| 报告 | 登记来源 | SHA-256 |
|---|---|---|
| 美的集团2025一季报 | [官方原件](https://static.cninfo.com.cn/finalpage/2025-04-30/1223412432.PDF) | `d99c61dec30e4fe226734c95ec5aa5508b1e994ed9e234a26846c7abcb147c21` |
| 比亚迪2025一季报 | [官方原件](https://static.cninfo.com.cn/finalpage/2025-04-26/1223309368.PDF) | `6ae70e06d80b96f3ae790583c5eb861d01e08cb738eee1152ef1269ad2609db6` |
| 立讯精密2024一季报 | [官方原件](https://static.cninfo.com.cn/finalpage/2024-04-25/1219799518.PDF) | `4f398f064179ccdd4323a22aa70f90d5c75f4e1946fec52fd80858be6a9549a7` |

链接取自来源登记，验收针对冻结PDF字节，未把在线文件当前可下载性当作已测结果。只下载这三个PDF并不等于已拥有规范事实与向量；从原件重建须另走上传、OCR、候选审核和发布流程。

## 2. 恢复

在项目根复制`.env.example`为私有`.env`，填写各服务配置。最小复现设置`FINSIGHT_DATA_PROFILE=user-financial-v1`、`FINSIGHT_OCR_TRIAL=false`；DB_NAME、MYSQL_DATABASE、SQL_DB_NAME使用同一个尚不存在的`finsight_*`库；SQL_DB_USER/SQL_DB_PASSWORD使用新的专用只读账户；CHROMA_DB_PATH使用空目录。不要将真实密码填入公开模板。

```powershell
$py = '.venv/Scripts/python.exe'
& $py -B -X utf8 scripts/restore_release.py verify --folder 'D:/复现资料/minimal-input'
& $py -B -X utf8 scripts/restore_release.py restore --folder 'D:/复现资料/minimal-input' --env .env
```

必须看到`Transport integrity verified`和`status: restored`。恢复回执写入`data/runtime/release-restore-receipt.json`，核对3份报告、106事实、57片段、一个serving指针和原始数据版本。若配置失败，保留失败记录，改用新库和新索引目录重试；不要覆盖原环境。首次安装因误用已有只读账户被拒绝，修正SQL_DB_USER/SQL_DB_PASSWORD并更换全新库、全新索引后完整恢复成功。

按安装说明分别启动Python、Java、Vue，使用私有配置里首启播种的管理员登录。恢复阶段不调用OCR和向量生成；问答仍调用配置的云端模型，须联网并有可用额度。

## 3. 问答、历史与来源验收

在新会话顺序执行M01—M03，M04另起会话。表中为保留的首次实际结果，未删除失败重抽答案。

| 编号 | 原始问题 | 首次结果 |
|---|---|---|
| M01 | 查询立讯精密2024年一季度合并营业收入，单位亿元、保留两位小数，并给出PDF原件页码。 | 完成并保存；524.07亿元，物理第8页；原始值52,406,775,584.57元 |
| M02 | 请给出这个数在PDF中的物理页码和原表整行，原文不要改写。 | 模型条件未通过请求规范；失败被保存，未执行财务查询；不计成功 |
| M03 | 只把单位改成万元，保留四位小数，用表格，不画图。 | 完成并保存；公司/期间未丢失，5,240,677.5585万元 |
| M04 | 查询比亚迪2024年全年合并营业收入，单位亿元，保留两位小数。 | 明确当前库无该报告，不补零、不借用2025一季报或试验库金额 |

计算依据：52,406,775,584.57 ÷ 100,000,000 = 524.0677558457亿元，四舍五入为524.07；除以10,000 = 5,240,677.558457万元，四位为5,240,677.5585。

刷新M01所在会话，核对原问题、首次失败记录与单位变更后的答案仍在；点击答案的原件入口，核对文件SHA和`#page=8`；退出登录后不得绕过鉴权获取该会话的资产。入口、浏览器和权限的最终实测结果见[本次验证记录](SUBMISSION_VERIFICATION.md)。

基础查询、单位追问、历史恢复与资料恢复可复现；M02表明云端原文追问仍会失败，不能宣称所有问法稳定成功。模型网络超时、规范失败应保留原任务，不重复提交来覆盖首次结果。

## 4. 完整试验数据

另行提供的`AIC-2026-96123955_full-data.zip`保存119份试验报告与3份严格报告，恢复方法相同但必须使用另一个空库和空索引。试验问答须开启`ocr-trial-v1`和`FINSIGHT_OCR_TRIAL=true`，保留experimental/relaxed_ocr及未完整核验提示。最小包不能重现D02—D06的全年数据；相关演示应使用完整包。两种恢复不混写原数据版本或成绩。

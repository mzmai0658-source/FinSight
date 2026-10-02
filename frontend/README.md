# FinSight 财报问答界面

Vue 3、TypeScript、Pinia、Vite 和 ECharts。浏览器通过 Java API 登录、管理会话、接收 SSE，并查看每次查询的结果行、核验范围和原文证据。

在仓库根目录按 [演示说明](../docs/DEMO.md) 启动 Python 和 Java 服务，再运行：

```powershell
cd frontend
npm ci
npm run dev
```

开发页面为 `http://localhost:5173`，Vite 将 `/api` 代理至 Java `http://localhost:8080`。生产部署配置 `VITE_API_BASE_URL` 或使用同源反向代理。Java 和 Python 必须共用 `INTERNAL_API_TOKEN`，该令牌不应进入前端环境变量。

原文与图表文件通过 `/api/assets/{asset_id}` 授权获取。前端使用登录令牌读取 Blob，原文 PDF 保留页码跳转；不提供本地文件路径下载或绕过 Java 的静态资源代理。

资料库 `/reports` 默认展示机构研报，支持分类、搜索和分页；登录后点击卡片，通过 `/api/materials/research/{id}/pages` 阅读已导入的 OCR 正文。公司财报另设页签（`/reports?tab=financial`），展示已登记财报全文和摘要，通过 `/api/materials/financial/{id}/pages` 阅读正文；仅与当前已发布结构化快照匹配的条目显示数字问答入口。工作台保留中文字段事实与页码，按用户要求移除了不稳定的“查看数字原文”按钮。导入与复查步骤见 [637 份研报导入记录](../docs/RESEARCH_LIBRARY_IMPORT.md) 和 [1,417 份公司财报导入记录](../docs/FINANCIAL_LIBRARY_IMPORT.md)。

未核验的公司诊股入口默认关闭。前端和 Java 的旧开关不能恢复 Python 已关闭的未核验报告服务；如未来重新开放，需先接入统一事实与证据核验契约。

```powershell
npm test
npm run build
```

行为测试覆盖 Markdown 安全处理、证据往返与多查询显示、登记资产身份、流式失败终态、ETL 部分完成和 ECharts 渲染。生产构建包括 TypeScript 检查。

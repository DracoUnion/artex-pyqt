# ARTEX Standalone（PyQt 桌面版）

用 **PySide6** 重写的 ARTEX 桌面客户端，配套 **SQLite 后端 services（进程内直连）**，
替代原「Go 后端 + Next.js 前端」架构。保留完整功能面：双图探索引擎、
任务控制台、人在环路对话、流量录制、拦截审批、系统管理等。

PyQt 直接调用后端 services 类（`src/services/gateway.py` 进程内分发），
不再启动 HTTP 服务，无 FastAPI / uvicorn 依赖，无登录/账号系统（直进主界面）。

## 架构

```
standalone/
├── src/                    # 全部源码
│   ├── main.py             # 入口：装配后端 services + Qt 主窗口
│   ├── services/           # SQLite services（对应原 server/）
│   │   ├── schema.sql      # 从 PostgreSQL 双图 schema 移植
│   │   ├── engine.py       # planner/worker 双图闭环（LLM 或演示模式）
│   │   ├── gateway.py      # 进程内分发：path -> service 调用（替代 server.py 路由）
│   │   └── …               # llm/assets/exploration/tasks/conversations/…
│   ├── gui/                # PySide6（对应原 web/）
│   │   ├── app.py          # 主窗口（无登录）
│   │   ├── sidebar.py      # 功能/系统两组导航
│   │   ├── api_client.py   # 进程内客户端（SSE 改为队列订阅）
│   │   ├── graph_widget.py # 力导向图组件
│   │   └── pages/          # 全部页面
│   └── smoke_test.py       # 后端冒烟测试
├── data/                   # 运行数据（SQLite、workspace、uploads）
├── skills/                 # 技能目录
├── requirements.txt
└── start.bat               # Windows 启动脚本
```

- 后端数据存 `data/`（SQLite `artex.db`），技能目录 `skills/`。
- 流量录制代理监听 `127.0.0.1:8788`（系统设置可开关）。
- 无 LLM Key 时引擎进入**演示模式**，可完整体验闭环。

## 运行

```bash
pip install -r requirements.txt
python src/main.py
```

启动后直接进入主界面（后端 services 已在进程内装配，无需登录）。

## 配置 LLM

系统配置 → LLM 配置 添加 OpenAI/Anthropic 兼容配置并设为激活。之后任务由
planner/worker 用真实推理驱动。

## 功能对照

| 原 web 页面 | 本桌面页 | 说明 |
| --- | --- | --- |
| `/dashboard` | 总览 | 统计卡 / Token / 活动流 |
| `/function/tasks` | 任务 | 列表 / 归档 / 新建 / 批量 |
| 任务详情 10 Tab | 任务详情弹窗 | 会话/总览/链路/播报/发现/复测/资产/覆盖图/拦截/报告 |
| `/chat` | 对话 | 会话 / 消息 / @提及 / /btw |
| `/function/findings` | 发现 | 平铺/分组/资产树 + 详情 |
| `/function/traffic` | 流量 | 分页浏览 + 原文 |
| `/function/assets` | 资产 | 7 类型 + DSL + 企业 |
| `/function/commands` `/llm-records` | 命令 / LLM 记录 | |
| `/function/workspace` `/sync` | 工作空间 / 资产同步 | |
| `/system/*` | 系统配置组 | LLM / Agent / 工具 / MCP / 技能 / 拦截 / 审批 / 推送 / 日志 / 设置 |

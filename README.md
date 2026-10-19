# ARTEX Standalone（PyQt 桌面版）

用 **PySide6** 重写的 ARTEX 桌面客户端，配套 **FastAPI + SQLite** 重写的后端，
替代原「Go 后端 + Next.js 前端」架构。保留完整功能面：双图探索引擎、
任务控制台、人在环路对话、流量录制、拦截审批、系统管理等。

## 架构

```
standalone/
├── main.py               # 入口：内嵌启动后端(:8787) + Qt 主窗口
├── requirements.txt
├── app/
│   ├── backend/          # FastAPI + SQLite（对应原 server/）
│   │   ├── schema.sql    # 从 PostgreSQL 双图 schema 移植
│   │   ├── engine.py     # planner/worker 双图闭环（LLM 或演示模式）
│   │   ├── server.py     # 261 路由 API 面
│   │   └── …             # auth/llm/assets/exploration/tasks/conversations/…
│   └── ui/               # PySide6（对应原 web/）
│       ├── app.py        # 登录/初始化 + 主窗口
│       ├── sidebar.py    # 功能/系统两组导航
│       ├── graph_widget.py # 力导向图组件
│       └── pages/        # 全部页面
└── smoke_test.py         # 后端冒烟测试
```

- 后端数据存 `data/`（SQLite `artex.db`、`jwt.key`），技能目录 `skills/`。
- 流量录制代理监听 `127.0.0.1:8788`（系统设置可开关）。
- 无 LLM Key 时引擎进入**演示模式**，可完整体验闭环。

## 运行

```bash
pip install -r requirements.txt
python main.py
```

首次启动设置管理员密码，随后登录进入主界面（`http://127.0.0.1:8787` 后端已在
进程内启动）。

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

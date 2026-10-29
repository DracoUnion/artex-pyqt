# artex-pyqt 架构设计

> ARTEX Standalone 桌面应用（PySide6）。本文件描述架构对齐后的当前代码结构（2026-10-30）。

## 1. 项目简介

把原 ARTEX Web/API 平台重写为进程内桌面应用：无 uvicorn/FastAPI、无登录账号系统。
PyQt 直接经 `ApiClient` 调用进程内 `Gateway`（方法+路径分发到 20+ 域服务），SSE 改为
进程内 Broadcaster/LogSink 队列 + QThread 桥。

变更历史：`8f48424` 新增零 Qt 架构层（common/tasks/bootstrap）、`main.py` 改用 bootstrap 装配、
`.gitignore` 忽略运行时 `data/`。

## 2. 技术栈与依赖（`requirements.txt`）

| 项 | 值 |
|---|---|
| Qt 绑定 | PySide6 ≥ 6.6 |
| 后端 | services 域包（自建 sqlite3 + schema.sql，WAL + RLock + 手工迁移） |
| 外部 | openai / requests / cryptography / PyYAML / pydantic / json-repair / ctx-compact / dateutil |
| 测试 | `src/smoke_test.py`（后端 Gateway 冒烟）+ `src/tests/test_arch.py`（零 Qt 架构层） |

## 3. 目录结构

```text
artex-pyqt/
├─ .gitignore                 # 忽略 data/（artex.db / uploads / workspace / e2e）
├─ src/
│  ├─ main.py                 # 入口 → bootstrap.build_context() → ApiClient → ArtexApplication
│  ├─ bootstrap.py            # build_context(data_dir, skills_dir)：Gateway + EventHub + TaskManager → AppContext
│  ├─ common/                 # ★ 零 Qt
│  │  ├─ events.py            # EventHub
│  │  ├─ context.py           # AppContext
│  │  └─ __init__.py
│  ├─ tasks/                  # ★ 零 Qt
│  │  ├─ task.py              # Task/TaskState/TaskManager + TaskCancelled
│  │  └─ __init__.py
│  ├─ services/               # ★ 零 Qt 域服务（26 模块）
│  │  ├─ gateway.py           # Gateway：进程内 method+path 分发（HTTP 兼容层）
│  │  ├─ db.py                # DB：裸 sqlite3 + schema.sql + WAL
│  │  ├─ engine.py / tasks.py / exploration.py / llm.py / agents.py / tools.py / ...
│  │  └─ __init__.py
│  ├─ gui/                    # ★ 唯一 PySide6
│  │  ├─ app.py               # ArtexApplication（侧栏导航 + 动态页面）
│  │  ├─ api_client.py        # ApiClient + SseWorker（QThread 收 Broadcaster/LogSink 事件）
│  │  ├─ sidebar.py / theme.py / widgets.py / graph_widget.py
│  │  └─ pages/               # dashboard/tasks/task_detail/系统设置/... + tabs/
│  ├─ tests/test_arch.py      # EventHub/TaskManager/Gateway 装配（零 Qt）
│  └─ smoke_test.py           # 后端 Gateway 冒烟（临时目录，建任务/跑引擎）
├─ skills/                    # 技能目录（运行时）
└─ data/                      # 运行时数据库 + workspace/uploads（gitignored）
```

## 4. 架构分层与依赖方向

```text
gui/ (唯一 PySide6；ApiClient 持 gateway)
 └─ services/gateway.py  (Gateway.dispatch(method, path, ...))
     └─ services/* 域服务（LLM/任务/探索/资产/流量/...）
         └─ common/（EventHub / AppContext）+ tasks/（TaskManager）
```

`Gateway` 是按参考架构保留的 **HTTP 兼容适配层**（对齐原 `server.py` 响应面）；页面/客户端沿用
原 get/post/put/patch/upload 接口形状，仅把 HTTP 换为进程内 dispatch。

## 5. 关键流程

```text
python src/main.py
→ bootstrap.build_context(data_dir, skills_dir)
     ├─ events = EventHub()
     ├─ tasks = TaskManager()
     └─ gateway = Gateway(data, skills)   # DB + LogSink + Broadcaster + 服务容器 + 恢复运行中引擎
→ ApiClient(gateway) → QApplication + apply_theme → ArtexApplication(client)
→ app.exec() → gateway.close() + ctx.tasks.shutdown()
```

事件流：Engine 调度（planner 线程 + worker 池 + Event/Lock/Timer）→ Broadcaster/LogSink 队列 →
`SseWorker(QThread)` → Qt 信号 → 页面。

## 6. 线程模型

| 线程 | 职责 |
|---|---|
| Qt 主线程 | 窗口 / 页面渲染 / 信号槽 |
| SseWorker(QThread) | 订阅 Broadcaster/LogSink 队列转 Qt 信号 |
| engine.py（原生线程） | planner loop + worker 池（每任务），Event/Lock/Timer |
| TaskManager 池（零 Qt） | 通用后台任务（本仓为 UI 桥预留） |
| TrafficProxy | 独立监听 127.0.0.1:8788（HTTP 工具留痕） |

## 7. 配置管理

- 运行数据：`data/`（artex.db、workspace、uploads）——已 gitignore。
- LLM：`services/llm.py` 配置文件入 DB（profiles），支持演示模式（无 key）。
- 技能：`skills/` 目录。
- 当前无 `.env`/QSettings 分层（配置在 DB 与默认值）。

## 8. 测试

```bash
python src/smoke_test.py                # 后端 Gateway 冒烟（临时 data/skills）
python -m unittest discover -s src/tests -t src   # 架构层（EventHub/TaskManager/Gateway 装配）
QT_QPA_PLATFORM=offscreen               # GUI 理论上支持 offscreen（无现成 GUI fixture）
```

## 9. 与参考架构的差距（后续 TODO）

- `services/db.py` 为裸 sqlite3 + schema.sql，未用 SQLAlchemy models/schemas；
  可迁 `db/`（base/session/migrations）+ `models/` + `schemas/`（Pydantic DTO）。
- `Gateway` 承担全部路由（600+ 行），参考架构倾向更薄的 facade + 按域 services。
- 无 Alembic / 版本迁移脚本（手工 `_migrate()`）。
- 无 QSettings 偏好存储；测试仅 smoke + 架构层，无完整 GUI 自动化。
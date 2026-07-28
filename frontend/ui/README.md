# 城市变化研究 · Web 前端

基于 [agent-chat-ui](https://github.com/langchain-ai/agent-chat-ui) 模板，通过 SSE 连接 LangGraph Server，提供对话式研究界面。

## 启动

### 1. 后端（LangGraph Server）

在 `frontend/` 目录下，激活 `xiongan_agent` Conda 环境：

```powershell
$env:PYTHONUTF8 = "1"
langgraph dev --host 0.0.0.0 --port 2024 --no-browser
```

如果 Windows 应用控制策略阻止了 `langgraph.exe`，改用 Python 入口调用：

```powershell
$env:PYTHONUTF8 = "1"
python -c "from langgraph_cli.cli import cli; cli(prog_name='langgraph')" dev --host 0.0.0.0 --port 2024 --no-browser
```

> 当前使用 `langgraph-cli 0.4.31`。不要用 `python -m langgraph_cli.cli`，该版本不会自动调用 CLI 入口。

启动后 API 位于 `http://localhost:2024`。如果报 8001-8003 端口无模型，说明 CLI 正常，需另行启动 vLLM 服务。

### 2. 前端（Next.js）

在 `frontend/ui/` 目录下：

```bash
pnpm install
pnpm dev
```

前端监听 `http://0.0.0.0:3000`，局域网设备可通过主机 IP 访问。

## 环境变量

`frontend/ui/.env`（已提供 `.env.example`）：

```bash
# 浏览器侧 API 地址（推荐 /api，走 Next.js 同源代理）
NEXT_PUBLIC_API_URL=/api
NEXT_PUBLIC_ASSISTANT_ID=agent

# 服务端代理目标（Next.js 把 /api/* 转发到 LangGraph Server）
LANGGRAPH_API_URL=http://localhost:2024
```

设置后跳过初始配置表单，直接进入对话界面。

## 项目结构

```
frontend/
├── graph.py              # LangGraph 主图（router → planner → researcher → reporter）
├── langgraph.json        # LangGraph Server 配置
├── .env                  # 后端环境变量（模型地址等）
└── ui/
    ├── src/
    │   ├── app/api/      # Next.js API 代理（/api/* → LangGraph Server）
    │   ├── components/thread/   # 对话界面（消息渲染、进度卡片、Markdown）
    │   └── providers/Stream.tsx # SSE 连接与状态管理
    └── .env              # 前端环境变量
```

## 消息显示控制

### 隐藏流式输出

对内部 LLM 调用添加 `TAG_NOSTREAM` 标签，前端不会渲染其流式 token：

```python
from langgraph.constants import TAG_NOSTREAM

resp = await llm.ainvoke(messages, config={"tags": [TAG_NOSTREAM]})
```

当前 `router_node`、`parse_input_node`、`web_planner_node`、`web_researcher_node` 的内部调用均已添加此标签。

### 完全隐藏消息

将消息 `id` 加上 `do-not-render-` 前缀，前端会彻底过滤：

```python
result.id = f"do-not-render-{result.id}"
return {"messages": [result]}
```

## Researcher 进度系统

后端通过 `get_stream_writer()` 发送自定义事件，前端 `Stream.tsx` 按 `execution_id` 聚合为实时进度卡片：

- 事件字段：`execution_id`、`task_id`、`topic`、`stage`、`detail`、`content`、`status`、`sequence`
- 状态流转：`running` → `finalizing` → `completed` / `failed`
- 持久化卡片（`name="internal"` 的 AIMessage）到达后，临时进度条自动清除
- 卡片 payload 为 version 3 JSON，内嵌完整 events 时间线

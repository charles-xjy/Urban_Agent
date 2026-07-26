# 对话历史持久化问题排查与解决

记录一次 `langgraph dev` 重启后历史会话内容丢失的问题排查与修复过程。

---

## 问题现象

用户反馈：重启 `langgraph dev` 后，前端侧边栏（Thread History）仍能看到历史会话的标题记录，但**点进去后对话内容为空**，无法恢复之前的上下文。

---

## 排查过程

### 1. 确认 UI 侧已有历史功能

先检查前端代码，确认"查看历史"的能力本身是存在的：

- `src/components/thread/history/index.tsx`：Thread History 侧边栏，通过 `client.threads.search()` 列出所有线程，点击切换 `threadId`。
- `src/providers/Thread.tsx`：`getThreads()` 调用 LangGraph Server 的 `threads/search` 接口。

所以问题不在前端 UI，而在**服务端数据是否真正持久化**。

### 2. 用 API 复现问题

不依赖 LLM 模型，直接用 LangGraph REST API 做最小复现：

```bash
# 1. 创建线程（必须带 graph_id，否则无法写状态）
curl -X POST http://127.0.0.1:2024/threads \
  -H "Content-Type: application/json" \
  -d '{"metadata":{"graph_id":"agent"}}'

# 2. 写入一条消息状态
curl -X POST http://127.0.0.1:2024/threads/<thread_id>/state \
  -H "Content-Type: application/json" \
  -d '{"values":{"messages":[{"role":"human","content":"test"}]},"as_node":"__start__"}'

# 3. 重启 langgraph dev

# 4. 分别验证两个接口
curl -X POST http://127.0.0.1:2024/threads/search ...   # 侧边栏数据来源
curl http://127.0.0.1:2024/threads/<thread_id>/state     # 点开会话数据来源
```

**关键发现：重启后两个接口表现不一致。**

| 接口 | 重启后表现 | 对应前端行为 |
|------|-----------|-------------|
| `threads/search` | 仍返回线程，且 `values.messages` 有内容 | 侧边栏能看到记录 |
| `threads/{id}/state` | 返回 `values: {}`，消息丢失 | 点进去内容为空 |

这与用户描述的现象完全吻合。

### 3. 定位根因

阅读 `langgraph_runtime_inmem` 源码，弄清两套数据的存储机制：

- **线程元数据 + 最新值缓存**：存在 `GlobalStore`（一个 `PersistentDict`），定期刷盘到 `.langgraph_api/.langgraph_ops.pckl`。`threads/search` 读这里 → 重启后仍在。
- **Checkpoint 历史**（重建完整状态所需）：由 `InMemorySaver` 管理，理应通过 `PersistentDict` 刷盘到 `.langgraph_api/.langgraph_checkpoint.N.pckl`。`threads/{id}/state` 读这里。

但实际检查发现：

```bash
ls frontend/.langgraph_api/
# 只有 .langgraph_ops.pckl
# 没有 .langgraph_checkpoint.*.pckl
```

等待超过 flush 间隔（10 秒）后，checkpoint 文件**始终没有生成**。全局搜索整个项目目录也找不到。说明这个版本（`langgraph-api 0.11.1` / `langgraph_runtime_inmem 0.31.1`）的内置文件持久化对 checkpoint 实际失效——只有 ops 元数据被持久化，checkpoint 历史随进程消亡。

**根因结论：** 侧边栏读的元数据持久化了，点开会话读的 checkpoint 没有持久化，导致"有记录、无内容"。

---

## 解决方案

放弃不可靠的内置 in-memory 持久化，改用**自定义 SQLite checkpointer**（LangGraph 官方支持的配置项）。

### 1. 安装依赖

```bash
pip install langgraph-checkpoint-sqlite
```

### 2. 新建 `frontend/checkpointer.py`

```python
import os
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

# 路径相对本文件，避免受服务进程工作目录影响
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoints.db")

checkpointer = AsyncSqliteSaver.from_conn_string(DB_PATH)
```

> 注意：`from_conn_string()` 返回异步上下文管理器，LangGraph 的 checkpointer adapter 会自动 enter 它。路径用 `__file__` 锚定到 `frontend/` 目录，规避之前怀疑过的工作目录（CWD）问题。

### 3. 配置 `frontend/langgraph.json`

```json
{
  "dependencies": ["."],
  "graphs": { "agent": "./graph.py:graph" },
  "env": "../.env",
  "checkpointer": {
    "path": "./checkpointer.py:checkpointer"
  }
}
```

LangGraph 启动时会读取 `checkpointer.path`，加载自定义 checkpointer 替换默认 in-memory 实现。

### 4. 依赖与运行时文件

将 `langgraph-checkpoint-sqlite` 声明在 `frontend/pyproject.toml` 中，避免换环境或重新安装项目时遗漏 SQLite checkpointer。

SQLite 运行时会在 `frontend/` 下生成以下文件：

- `checkpoints.db`
- `checkpoints.db-wal`
- `checkpoints.db-shm`

这些文件属于运行时数据，不应提交到 Git；项目根目录的 `.gitignore` 已统一忽略它们。

### 5. 配套清理

- `.gitignore` 增加 `frontend/checkpoints.db`（运行时数据，不入库）。

---

## 验证

重启服务后逐项确认：

```
[info] Configuring custom checkpointer at ./checkpointer.py:checkpointer
[info] Using custom checkpointer: AsyncSqliteSaver
```

1. 创建线程、写入消息 → `frontend/checkpoints.db` 生成。
2. **重启 `langgraph dev`**。
3. `threads/{id}/state` 正常返回消息（修复前为空）✓
4. `threads/search` 返回线程列表且带首条消息（侧边栏正常）✓
5. 清理测试线程（`DELETE /threads/{id}` 均返回 204）✓

---

## 涉及文件

| 文件 | 改动 |
|------|------|
| `frontend/checkpointer.py` | 新建，SQLite checkpointer |
| `frontend/langgraph.json` | 增加 `checkpointer.path` |
| `.gitignore` | 忽略 `frontend/checkpoints.db` |
| `README.md` | 补充"对话历史持久化"说明 |

---

## 经验总结

1. **区分"列表可见"与"内容可恢复"**：两者由不同存储支撑（线程元数据 vs checkpoint），一个持久化成功不代表另一个也成功。排查时要分别验证。
2. **用 REST API 做最小复现**：绕过 LLM 和前端，直接对 `threads/search` 和 `threads/{id}/state` 打请求，能快速隔离问题层。
3. **内置 dev 持久化不可全信**：`langgraph dev` 的 in-memory + 文件刷盘机制在特定版本对 checkpoint 失效。需要可靠持久化时，显式配置 checkpointer（SQLite 轻量够用，生产用 Postgres）。
4. **自定义 checkpointer 的能力降级提示**：启动日志会警告 `AsyncSqliteSaver` 缺少 `adelete_for_runs` / `acopy_thread` / `aprune`，这些只影响回滚清理、线程复制、历史裁剪等次要功能，核心的读写（`aget_tuple` / `aput` / `aput_writes` / `alist`）完整，不影响对话历史保存与恢复。

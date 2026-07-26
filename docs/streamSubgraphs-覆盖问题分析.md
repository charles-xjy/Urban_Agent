# `streamSubgraphs: true` 导致父图消息被子图覆盖

## 问题现象

当 researcher 通过 `Send` 开始执行时，主对话流出现两个异常：

1. **之前的对话消失** —— 用户输入、planner 回复、用户确认等历史消息突然不见。
2. **子 agent 的内部 query 被当作 human 消息渲染** —— 例如「研究任务：智慧校园建设…」以用户气泡形式出现在对话里。

## 根因结论

4 处 `stream.submit(...)` 都带了 `streamSubgraphs: true`：

- `frontend/ui/src/components/thread/index.tsx:343`
- `frontend/ui/src/components/thread/index.tsx:361`
- `frontend/ui/src/components/thread/index.tsx:385`
- `frontend/ui/src/components/thread/messages/human.tsx:61`

```ts
streamMode: ["values"],
streamSubgraphs: true,   // ← 罪魁
```

这个开关在当前项目里是**纯粹的死代码**，从未被任何地方消费，唯一效果就是触发这个 bug。

## 触发链路

### 1. `streamSubgraphs: true` 让子图状态也流式推送

后端 `web_researcher_node`（`frontend/graph.py`）内部手动运行了一个编译过的子图：

```python
agent = _make_researcher()   # create_react_agent(...)
async for event in agent.astream_events({"messages": [HumanMessage(query)]}, ...):
    ...
```

`create_react_agent` 返回的是编译过的图，于是它的内部状态（`messages`：那条 `HumanMessage("研究任务：…")` + 工具调用 + 工具结果）会被一并推到前端。

### 2. SDK 只认 `"tools:"` 命名空间，其余一律「整体替换」

SDK `ui/manager.cjs:400-454` 每来一个 `values` 事件，分支如下：

```js
if (namespace && isSubagentNamespace(namespace)) {
  // 子代理分支：只塞进 subagentManager，不碰主 values
  if (namespaceId && this.filterSubagentMessages) { ... }
} else {
  // ★ return data  ← 把整个 stream.values 直接替换成 data
}
```

而 `isSubagentNamespace`（`ui/subagents.cjs:18-21`）的定义是：

```js
function isSubagentNamespace(namespace) {
  if (typeof namespace === "string") return namespace.includes("tools:");
  return namespace.some((s) => s.startsWith("tools:"));
}
```

**只有命名空间里含 `"tools:"` 段才算「子代理」。** ReAct agent 内部调用工具的那个 `tools:<uuid>` 节点算；但**子图最外层那一级的命名空间（类似 `web_researcher:<uuid>`）不含 `"tools:"`，不算** —— 于是它走 `else` 分支，执行 `return data`：

> `stream.values` 被整个替换成子图那一份状态，`stream.messages` 也随之变成子图自己的 `messages`。

这一条就是「覆盖」的真正机理 —— 不是 SDK 把子图消息 merge 进父图，而是**子图的非 `tools:` 层级的 values 事件，被当成普通 root 事件做了全量替换**。

## 两个症状如何对应

### 症状 1：之前的对话消失

父图原本的 `messages`（用户输入、planner、确认）被替换没了。下一帧 root 事件回来时才短暂恢复，子图事件一到又被冲掉 —— 所以看着是「消失」。

### 症状 2：「研究任务：…」被当作 human 消息渲染

子图 `messages[0]` 正是 `web_researcher_node` 喂给子 agent 的那条 `HumanMessage`（`graph.py:526-532`）。它替换进来后，自然按 human 气泡渲染。

## 为什么 `index.tsx:256-270` 的过滤救不了

那段过滤的本意是「按 `checkpoint_ns` 把子图消息剔掉」：

```ts
const checkpointNamespace = streamMetadata?.langgraph_checkpoint_ns ??
  streamMetadata?.checkpoint_ns;
return !(typeof checkpointNamespace === "string" && checkpointNamespace.length > 0);
```

但它挡不住，原因有二：

1. **过滤是逐条消息的，而覆盖发生在 values 层** —— 问题不是「多了几条子图消息要剔除」，而是**整个 `stream.messages` 数组被换成子图的那份**。逐条过滤没法把根本不在这一帧里的父图消息找回来。

2. **`checkpoint_ns` 元数据要靠 `messages-tuple` 模式才挂得上** —— 那个 metadata 是在 `manager.cjs:455-493` 的 `messages` 事件分支里 `this.messages.add(serialized, metadata)` 写入的。而项目提交时只声明了 `streamMode: ["values"]`；`messages-tuple` 是靠读 `stream.messages` 时 `trackStreamMode` 懒订阅才补上的（`stream.lgp.cjs:536`），覆盖并不可靠。没有 metadata → `getMessagesMetadata` 返回 undefined → 过滤条件不成立 → 子图消息原样保留并渲染。

> 即便开了 `filterSubagentMessages: true` 也救不了，因为它只作用于含 `"tools:"` 的命名空间，对外层那个非 `tools:` 子图无效。

## 最后一锤：这个开关在项目里根本没用

对整个 `src/` 搜索后确认：

- **没有任何地方读取 `stream.subagents` / `activeSubagents` / `getSubagent`** —— 子图流式的产物没人消费。
- researcher 的实时进度是靠**另一条独立通道**送的：后端 `get_stream_writer()` 发 `research_progress` 自定义事件 → 前端 `onCustomEvent` 接 → 落到 `researchProgress` 本地 state（`Stream.tsx:140-189`）。跟子图流式毫无关系。

所以 `streamSubgraphs: true` 是从 agent-chat-ui 模板里带过来、但从未被使用的死代码，**唯一的效果就是触发这个 bug**。

## 修复方向

把那 4 处的 `streamSubgraphs: true` 删掉即可。删掉后：

- 父图 `values` 照常整体替换，但每次都是**完整的父图状态**（含全部累积消息），不再被子图状态冲掉。
- researcher 进度仍由 `research_progress` 自定义事件驱动，不受影响。
- 那段 `checkpoint_ns` 过滤会变成无害的 no-op（不会再有子图消息进来），可以保留。

## 涉及的关键代码位置

| 文件 | 行号 | 作用 |
|------|------|------|
| `frontend/ui/src/components/thread/index.tsx` | 343, 361, 385 | 3 处 `streamSubgraphs: true`（submit） |
| `frontend/ui/src/components/thread/messages/human.tsx` | 61 | 第 4 处 `streamSubgraphs: true` |
| `frontend/ui/src/components/thread/index.tsx` | 256-270 | 无效的子图消息过滤 |
| `frontend/ui/src/providers/Stream.tsx` | 140-189 | researcher 实时进度（独立通道） |
| `frontend/graph.py` | 526-534 | researcher 喂给子 agent 的 `HumanMessage` |
| `node_modules/.../ui/manager.cjs` | 400-454 | SDK 子图 values 事件分支 |
| `node_modules/.../ui/subagents.cjs` | 18-21 | `isSubagentNamespace` 只认 `"tools:"` |

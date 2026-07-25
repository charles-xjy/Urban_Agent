# Urban Deep Research Agent

城市变化多工具研究系统 — 基于 LangGraph 架构，支持 CLI 和 Web 两种运行模式。

---

## 研究假设

在 Deep Research 框架中，Researcher Agent 除调用网页搜索外，还能自主调用**卫星图像分析**和 **POI 历史数据查询**两个工具，由 Agent 自己决定何时用哪类工具——与纯网页搜索相比，最终报告的幻觉率是否显著下降？

**幻觉定义：** 报告中无法被公开可查资料核实的陈述。

**目标：** 幻觉率下降 ≥ 50%，覆盖率下降 ≤ 20%。

### 三组对照实验

| 组别 | 工具集 | 说明 |
|------|--------|------|
| Baseline A | `web_search` only | 纯网页搜索，无图像和 POI |
| Baseline B | `web_search` + `satellite` | 加入卫星图，验证视觉幻觉减少效果 |
| 实验组 | `web_search` + `satellite` + `poi` | 三路工具全开 |

---

## 调度模式：领域化 Super Agent Harness

本项目现在对标的是 DeerFlow 2.0 重构后的 **super agent harness** 思路：上层有一个负责拆解和编排的主 Agent / 主图，下层通过子 Agent 或工具执行具体任务。但本项目不是通用 super agent，而是把编排边界收窄到**城市变化分析**：先让 Planner 生成城市研究维度，再让用户确认或修改，最后并发派发多个领域 Researcher。

> 注：DeerFlow 2.0 是一次重写，官方 README 明确说明它与 v1 不共享代码。2.0 的核心不再是 v1 的固定 Deep Research 计划图，而是 Lead Agent + middleware + tools + subagents + memory + sandbox 的通用 harness。ODR 指 `langchain-ai/open_deep_research` 当前主线实现。

| 维度 | 本项目 | DeerFlow 2.0 | ODR 当前主线 |
|------|--------|--------------|-------------|
| 系统定位 | 城市变化分析专用研究系统 | 通用 super agent harness | 通用 Deep Research Agent |
| Agent 编排 | 主图显式执行 `planner → human_approval → Send(researcher)` | Lead Agent 在运行中通过 `task` 工具委派 subagent | Supervisor 在运行中通过 `ConductResearch` 委派 researcher subgraph |
| 用户确认 | **有**：研究维度确认/修改后才执行 | 不是固定执行前计划审批；可通过 clarification / todo / middleware 辅助交互 | 通常无执行前计划审批；先生成 research brief 后自动研究 |
| 规划透明度 | 高：城市研究维度执行前可见、可改 | 中等：Lead Agent 可用 todo/思考管理任务，但 subagent 委派主要在运行中产生 | 中等：有 research brief，但具体 `ConductResearch` 委派在运行中产生 |
| 子 Agent 粒度 | 一个城市变化维度对应一个 Researcher | `task(description, prompt, subagent_type)` 启动通用或自定义 subagent | 每个 `ConductResearch(research_topic)` 启动一个 researcher 子图 |
| 工具体系 | web_search + 卫星图像 + POI 历史计数 | 配置化 tools、MCP、sandbox、skills、memory、subagents | 搜索工具、native web search、MCP 工具、`think_tool` 等 |
| 核心目标 | 验证多源城市证据能否降低报告幻觉率 | 让通用 Agent 能分解、委派、使用工具和沙箱完成复杂任务 | 自动完成开放式深度研究 |

**为什么仍然保留执行前审批：** DeerFlow 2.0 和 ODR 都更偏“运行中动态委派”，这适合开放式任务；但城市变化分析的研究口径很敏感，例如用户可能真正关心产业、公共服务或空间扩张，而不是模型默认拆出的生态或交通。因此本项目把“研究哪些维度”前置为可审计划，用一次用户确认来减少跑偏和无效工具调用。

**与 DeerFlow 2.0 / ODR 的定位差异：** 与 DeerFlow 2.0 相比，本项目不是通用 harness，不追求任意任务的 subagent/tool/sandbox 编排，而是把子 Agent 固定为城市 Researcher，并把工具限制在网页、卫星图、POI 历史计数三类城市证据。与 ODR 相比，本项目不让 Supervisor 在运行中不断产生新研究主题，而是先固定可审的城市研究维度，再并发研究。

### Agent 调用方式差异

DeerFlow 2.0、ODR 当前主线和本项目的核心差异不只是“有没有 Planner”，还体现在**谁决定调用哪个 Agent、调用前是否可见、Agent 内部能否自主使用工具**。

源码核对依据：

- DeerFlow 2.0 对比基于 `bytedance/deer-flow` 当前 `main` 分支：README 将其定义为 “super agent harness”；`backend/packages/harness/deerflow/client.py` 中 `DeerFlowClient._ensure_agent()` 是类型无关的通用 agent 创建方法，当 `agent_name` 配置为 `lead_agent` 时才创建 Lead Agent；`backend/packages/harness/deerflow/tools/tools.py` 在 `subagent_enabled=True` 时加入 `task_tool`；`backend/packages/harness/deerflow/tools/builtins/task_tool.py` 中 `task(description, prompt, subagent_type)` 会创建 `SubagentExecutor` 并异步执行 subagent（另有 `runtime`、`tool_call_id` 两个框架注入参数，不由 LLM 提供）；`backend/packages/harness/deerflow/agents/lead_agent/prompt.py` 的 subagent prompt 要求 Lead Agent “DECOMPOSE, DELEGATE, SYNTHESIZE”。
- ODR 对比基于 `langchain-ai/open_deep_research` 当前主线：主图是 `clarify_with_user → write_research_brief → research_supervisor → final_report_generation`；Supervisor 绑定 `ConductResearch`、`ResearchComplete`、`think_tool`，在运行时通过 `ConductResearch` tool call 动态委派研究主题，并用 `asyncio.gather()` 并发执行多个 researcher subgraph。
- 本项目对比基于当前仓库：`graph/main_graph.py` 中 `planner_node → human_approval_node → dispatch_researchers()`，其中 `dispatch_researchers()` 用 LangGraph `Send()` 对确认后的 `plan` 并发派发；`graph/researcher_graph.py` 中每个 Researcher 是 `create_react_agent`，工具集固定为 `web_search`、`analyze_satellite_image`、`query_poi_history`。

| 维度 | DeerFlow 2.0 | ODR | 本项目 |
|------|--------------|-----|--------|
| Agent 调用入口 | Lead Agent 通过 `task` 工具按需委派 subagent | `clarify_with_user → write_research_brief → research_supervisor` | `clarify_with_user → planner → 用户审批 → Send(researcher)` |
| 调度单位 | `task(description, prompt, subagent_type)`；subagent 类型可为 `general-purpose`、`bash` 或配置中的 custom agent | `ConductResearch(research_topic)`；Supervisor 运行时决定委派哪些研究主题 | `ResearchTask`；每个城市变化维度对应一个 Researcher，并发执行 |
| 并发方式 | Lead Agent 可在一轮中发起多个并行 `task` 调用，并受最大并发数限制 | 多个 `ConductResearch` tool call 触发多个 `researcher_subgraph.ainvoke()`，用 `asyncio.gather()` 并发 | `Send()` 将用户确认后的多个子任务并发派发到 `researcher_graph` |
| 调用透明度 | 中等：可以看到 task 事件和 subagent 运行状态，但委派通常由 Lead Agent 在执行中决定 | 中等：用户能看到过程，但具体研究主题由 Supervisor 在运行中产生 | 高：研究维度在执行前展示给用户，可确认或修改 |
| 人工介入 | 通过 clarification、middleware、工具权限等机制交互；不是固定的执行前计划审批 | 当前主线主要自动执行；legacy workflow 才强调计划反馈 | 明确保留一次计划确认/修改，确认后才启动 Researcher |
| 工具调用 | Lead Agent 和 subagent 使用配置化工具、MCP、sandbox、skills、memory；subagent 默认不再递归拥有 `task` 工具 | researcher 子图在 ReAct 循环中调用搜索、native web search、MCP、`think_tool` 等 | Researcher 在 `web_search`、`analyze_satellite_image`、`query_poi_history` 中自主选择 |
| 与本项目关系 | 本项目借鉴“主 Agent/主图拆解后并发委派子 Agent”的 harness 思路，但将任务域和工具集固定为城市变化分析 | 本项目不像 ODR 那样由 Supervisor 运行时动态产生研究委派，而是先固定可审维度再并发研究 | 本项目关注多源城市证据交叉验证，而不是通用开放域研究 |

一句话概括：**DeerFlow 2.0 是通用 Lead Agent 用 `task` 工具动态调 subagent；ODR 是 Research Supervisor 用 `ConductResearch` 动态调 researcher 子图；本项目是先让用户审定城市研究维度，再用 `Send()` 并发启动多个领域 Researcher。**

### 参考框架执行流程

#### DeerFlow 2.0：Lead Agent + task subagent harness

DeerFlow 2.0 的核心是一个通用 Lead Agent。工具、MCP、sandbox、memory、skills、subagent 能力通过配置和 middleware 注入。开启 subagent 后，Lead Agent 会获得 `task` 工具，并被提示先分解任务、再并行委派、最后综合结果。

```
用户请求
  ↓
Lead Agent（create_agent）
  ├─ 直接调用普通工具 / MCP / sandbox / memory
  ├─ 需要并行探索时：task(description, prompt, subagent_type)
  │     ↓
  │   SubagentExecutor
  │     ↓
  │   subagent 独立上下文执行
  │     ↓
  │   返回结果给 Lead Agent
  ↓
Lead Agent 综合多个 subagent / tool 结果
  ↓
最终回答
```

它的关键点是：subagent 调用不是预先固定在一个城市研究计划里，而是 Lead Agent 在执行过程中根据任务复杂度动态决定；`task` 工具本身要求适合复杂、多步、并行探索任务，不适合简单单步操作。

#### ODR：Supervisor 动态委派的研究子图

ODR 当前主线的入口是 `deep_researcher` 图，主流程先澄清问题，再把用户消息改写成 `research_brief`，然后交给 `research_supervisor` 子图。Supervisor 不是先输出一个用户审批的完整 step plan，而是在运行中通过工具调用动态决定要委派哪些研究主题。

```
START
  ↓
clarify_with_user
  ↓
write_research_brief
  ↓
research_supervisor
  ├─ think_tool：策略思考
  ├─ ConductResearch：委派一个研究主题给 researcher_subgraph
  └─ ResearchComplete：结束研究阶段
  ↓
final_report_generation
  ↓
END
```

其中 `ConductResearch` 是 Supervisor 绑定的工具，但它的作用不是直接搜索网页，而是启动一个 researcher 子图。多个 `ConductResearch` 调用会触发多个 `researcher_subgraph.ainvoke()`，并通过 `asyncio.gather()` 并发执行。

```
researcher_subgraph
  ↓
researcher
  ↓
researcher_tools（搜索 / native web search / MCP / think_tool 等）
  ↺ researcher 继续 ReAct 循环
  ↓
compress_research
  ↓
返回 compressed_research 给 Supervisor
```

因此，ODR 的架构更接近“Supervisor 动态委派 + 子 Researcher 并发 + ReAct 工具循环 + 最终报告生成”。它适合开放域深度研究；而本项目把委派边界提前到用户可审的城市研究计划中，更强调研究维度、时间范围和证据类型的可控性。

#### 本项目：领域化并发 Researcher

本项目在结构上吸收 DeerFlow 2.0 的“主 Agent/主图拆解任务后委派子 Agent”思想，但没有采用完全动态的 `task` 工具委派；它先把待研究的城市维度显式列出来，让用户确认后再并发派发。

```
clarify_with_user
  ↓
planner 生成 2-4 个城市研究维度
  ↓
human_approval：用户确认或修改计划
  ↓ Send()
Researcher × N 并发执行
  ├─ web_search：先获取文字背景
  ├─ analyze_satellite_image：验证空间/物理变化
  └─ query_poi_history：补充 POI/OSM 历史计数
  ↓
reporter 汇总 findings 生成最终报告
```

与 DeerFlow 2.0 相比，本项目没有开放式的 subagent 类型选择、skills、memory 和 sandbox 编排，而是让多个同构 Researcher 分别负责不同城市研究维度。与 ODR 相比，本项目不让 Supervisor 在运行中不断产生新研究委派，而是先让用户确认研究维度，再通过限定工具集做多源证据交叉验证。

---

## 整体架构

```
用户输入："分析雄安新区 2018-2024 年的城市变化"
    │
    ▼
clarify_with_user
    LLM 判断问题是否足够清晰，缺少地点或时间时追问一次
    │
    ▼
planner
    主模型制定研究子任务列表（2-4 个维度）
    ⏸ interrupt() 暂停，展示计划给用户
    │
    ▼
【用户审批】
    ✓ 确认 → 直接执行
    ✏ 修改 → 更新计划后执行（支持自然语言描述或 JSON）
    │
    ▼ Send() 并发派发
┌──────────────────────────────────────────────────────────────┐
│   Researcher × N（各自独立上下文，并发执行）                   │
│                                                              │
│   Evidence-driven ReAct 循环：                               │
│     web_search     → 语义证据（source_score 0.3-1.0）        │
│     web_fetch      → 精读权威全文（Jina / MCP fallback）      │
│     satellite ★    → 空间证据（像素级视觉观察）               │
│     poi_history ★  → 实体证据（ohsome 0.8 / 高德 1.0）       │
│                                                              │
│   证据冲突时触发三步仲裁，>30 轮未定论输出冲突声明             │
│   runtime 计数器全程追踪工具调用轮次                          │
└──────────────────────────────────────────────────────────────┘
    │ findings 追加到主图 state
    ▼
reporter
    汇总所有 findings → 主模型生成最终报告
```

---

## 完整示例

**用户：** "分析雄安新区 2018-2024 年的城市变化"

**Step 1 — clarify**：问题清晰，不追问。

**Step 2 — planner 展示计划：**
```
我计划从以下维度研究雄安新区 2018-2024 年的变化：

  Task 1：城市空间扩张 — 建成区面积、建设用地扩张趋势
  Task 2：交通网络演进 — 路网密度、轨道交通及枢纽建设
  Task 3：生态格局优化 — 白洋淀水质、蓝绿空间占比变化
  Task 4：公共服务配套 — 学校、医院等设施布局与覆盖

请确认，或告诉我需要调整哪些维度。
```

**Step 3 — 用户审批：** "把生态改成经济发展，其他可以。"→ 修改 Task 3 → 确认。

**Step 4 — 三个 Researcher 并发（实时输出进度）：**
```
[城市空间扩张] 正在搜索：雄安新区建设用地 2024
[城市空间扩张] 搜索完成
[城市空间扩张] → query_poi_history(雄安新区)
[城市空间扩张] ← query_poi_history 结果：
  building: 772个 → 1360个（+76.2%）
  residential: 5个 → 99个（+1880%）
...
```

**Step 5 — 报告生成**，输出最终分析报告。

---

## 工具详解

### web_search

- **数据源：** DuckDuckGo Search（DDGS）
- **用途：** 建立背景知识、获取政策/新闻/统计报道
- **关键词约束：** 4-6 个词，不写完整句子
- **搜索限制：** 同一方向最多 3 次，超过则记录"数据缺失"
- **来源评分：** 每条结果附带 `source_score`（0.3–1.0）和 `source_label`，结果按分数降序排列，LLM 优先看到高权威来源

| 来源类型 | source_score | source_label |
|---------|-------------|-------------|
| 国家统计局 | 1.0 | 国家统计局 |
| 政府官网（.gov.cn） | 0.95 | 政府官网 |
| 官方媒体（新华/人民/央视） | 0.85 | 官方媒体 |
| 高校/科研机构（.edu.cn / .ac.cn / cnki） | 0.80 | 学术机构 |
| 门户/聚合（新浪/搜狐/今日头条） | 0.45 | 门户/聚合 |
| 其他 | 0.40 | 普通网站 |

### web_fetch

- **用途：** 精读 web_search 返回的重要链接，获取摘要之外的完整正文、数据表格、政策原文
- **抓取策略（双层 fallback）：**
  1. **Jina AI**（`r.jina.ai`）— 境外服务器，走本机代理，直接返回 Markdown
  2. **MCP fetch**（`uvx mcp-server-fetch`）— 本机进程，Jina 超时时自动触发，能访问国内网站
- **触发时机：** 来源域名权威（gov.cn、stats.gov.cn 等）或摘要含具体数字/政策名称时调用
- **依赖：** `mcp`（Python 客户端）+ `uvx`（运行 mcp-server-fetch，无需单独安装）

### analyze_satellite_image ★

- **数据源：** Google Earth Engine（GEE）Sentinel-2 影像
- **流程：** 高德 geocode → GEE 下载两期影像 → 小模型（8002）生成像素级视觉描述
- **给 Researcher 的：** 纯像素级观察，不含推断（"左上区域从绿色植被变为灰色地面，约20%面积"）
- **同时存档：** 小模型完整分析报告，写入 `data/baselines/`，用于 eval 对照，Researcher 看不到

### query_poi_history ★

- **数据源：** ohsome API（OSM 历史快照，HeiGIT/海德堡大学，国内可达）+ 高德实时 POI（fallback）
- **支持类别：** `building` / `road_primary` / `road_secondary` / `hospital` / `school` / `residential` / `commercial` / `park` / `water` / `industrial`
- **三条独立路径：**

```
路径 A — ohsome（历史对比，置信度 0.8）
  高德 geocode → 构造 bbox → ohsome 查 start_year / end_year 两端计数
  → 有数据：返回变化量 + 变化率，置信度 0.8

路径 B — 高德实时 fallback（当前快照，置信度 1.0）
  触发条件：
    ① geocode 失败（直接跳过 ohsome，仍可用高德中文地名查询）
    ② ohsome 两端均为 0（OSM 中国覆盖不足）
  高德 place/text API，直接传中文地名，无需 geocode
  → 有数据：返回当前 POI 总数，注明"仅当前快照，无历史对比"
  注：building / road / water 无高德类型映射，不走此路径

路径 C — 无数据
  两条路径均无有效数据 → 返回置信度 0.0，建议改用 web_search
```

---

## Evidence-driven ReAct 循环

Researcher 不是固定流水线，而是基于当前证据状态动态决定下一步。每类工具提供不同维度的证据：

| 证据类型 | 工具 | 触发时机 |
|---------|------|---------|
| 语义证据 | `web_search` + `web_fetch` | 先调用，建立背景；搜到权威域名或具体数字时精读全文 |
| 空间证据 | `analyze_satellite_image` | 文字证据提到空间/物理变化时，视觉验证 |
| 实体证据 | `query_poi_history` | 有定性结论需量化支撑时调用 |

### 证据置信度

```
三路证据（语义 + 空间 + 实体）互相印证 → 高，明确陈述
两路证据支撑                           → 中，正常陈述
单路证据                               → 低，标注"仅有X证据支持"
零证据                                 → 不写入结论，说明"该方向证据不足"
```

### 证据冲突处理

当两路证据得出相反结论时（如文字报道绿化增加、卫星图显示植被减少），不降级置信度了事，而是触发三步仲裁：

```
Step 1: 再次 web_search，加"官方数据"或机构名寻找第三方裁定
Step 2: query_poi_history 获取量化数据作独立仲裁
Step 3: 再次 analyze_satellite_image（换时间节点或更小区域）

三步仍无法消解，或累计工具调用 > 30 次 →
  输出：【冲突未解决】证据存在冲突，暂无法定论
        - 支持"结论A"的证据：...
        - 支持"结论B"的证据：...
禁止在冲突未解决时给出确定性结论。
```

### 轮数追踪

`ResearcherState.runtime` 累计本次工具调用次数（`Annotated[int, operator.add]`），初始 query 注入当前轮数，LLM 全程感知，超 30 轮未定论自动触发冲突声明。

---

## 目录结构

```
Urban_Agent/
├── graph/
│   ├── state.py            ResearchTask / AgentState / ResearcherState
│   ├── main_graph.py       主图（clarify→planner→审批→researcher×N→reporter）
│   └── researcher_graph.py ReAct Researcher + 工具策略 prompt
│
├── tools/
│   ├── web_search.py       DDGS 搜索（@tool）
│   ├── web_fetch.py        网页全文抓取，Jina → MCP fallback（@tool）
│   ├── satellite.py        GEE 影像 + 视觉描述（@tool）
│   ├── poi.py              ohsome 历史计数（@tool）
│   ├── gaode_geocode.py    地名 → 经纬度（高德）
│   └── google_earth.py     GEE Sentinel-2 影像下载
│
├── core/
│   └── models.py           ImageResult（google_earth.py 使用）
│
├── config/
│   └── settings.py         vLLM 端点、模型扫描与确认、API Key
│
├── frontend/
│   ├── graph.py            LangGraph Server 入口（Web 模式）
│   ├── langgraph.json      LangGraph 服务配置
│   ├── pyproject.toml
│   └── ui/                 Next.js 聊天界面
│       ├── src/
│       │   ├── app/
│       │   │   ├── layout.tsx
│       │   │   ├── page.tsx
│       │   │   └── api/
│       │   │       ├── [..._path]/route.ts   (代理 LangGraph Server)
│       │   │       └── local-image/route.ts  (卫星图片服务)
│       │   ├── providers/Stream.tsx
│       │   └── components/thread/
│       │       ├── index.tsx                 (任务进度面板)
│       │       └── messages/ai.tsx           (Researcher 折叠卡片)
│       └── package.json
│
├── data/
│   ├── satellite_images/   GEE 影像本地缓存
│   └── baselines/          小模型原始报告存档（eval 用）
│
├── eval/
│   └── hallucination_eval.py  三组对比评估脚本
│
├── main.py                 CLI 入口
├── .env
├── .env.example
└── requirements.txt
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 .env

复制 `.env.example` 为 `.env`，填入实际值：

```bash
cp .env.example .env
```

```env
# vLLM 模型端点（模型名启动时自动从 /v1/models 查询）
BASE_LLM_URL=http://10.129.107.145

# 高德地图
GAODE_API_KEY=your_key

# Google Earth Engine
GEE_PROJECT=ee-yourproject

# 代理（GEE + DDGS + Jina 国内必须；未设置时自动读取系统代理）
HTTP_PROXY=http://127.0.0.1:7897
HTTPS_PROXY=http://127.0.0.1:7897

# Jina Reader（可选；未设置时匿名限速 20 RPM）
# JINA_API_KEY=your_key
```

### 3. GEE 认证（首次）

```bash
earthengine authenticate
```

---

## 启动方式

### 方式一：CLI 模式

交互式命令行，适合调试和单次分析。

```bash
python main.py
```

启动后会：
1. 自动扫描 8001-8003 端口上可用的 vLLM 模型
2. 如果只有一个模型，自动分配；多个模型时交互确认每个角色使用哪个模型
3. 进入对话循环，输入分析请求即可开始研究

示例交互：

```
> 分析雄安新区 2018 到 2024 年的城市变化

[计划确认]
  Task 1：城市空间扩张
  Task 2：交通网络演进
  Task 3：生态格局优化
  确认执行？(Y/n)

> y

[研究中...] Researcher × 3 并发执行
[报告生成] ...
```

---

### 方式二：Web 前端模式

基于 LangGraph Server + Next.js 的聊天界面，支持：
- 流式输出
- 每个 Researcher 显示为可折叠卡片（实时进度、卫星图缩略图）
- 研究进度面板（N/M 完成）
- 计划确认 interrupt
- 普通对话 / 无效输入的智能路由

#### 前置安装

**Linux / macOS：**

```bash
pip install "langgraph-cli[inmem]"
cd frontend/ui && pnpm install
```

**Windows (PowerShell)：**

```powershell
pip install "langgraph-cli[inmem]"
cd frontend/ui; pnpm install
```

#### 启动 LangGraph Server（终端 1）

**Linux / macOS：**

```bash
cd frontend
langgraph dev --host 0.0.0.0 --port 2024
```

**Windows (PowerShell)：**

```powershell
cd frontend
$env:PYTHONUTF8=1; langgraph dev --host 0.0.0.0 --port 2024
```

> Windows 中文系统必须设置 `PYTHONUTF8=1`，否则 langgraph-api 内部读文件会报 GBK 解码错误。

服务启动后监听 `http://0.0.0.0:2024`（`--host 0.0.0.0` 使局域网内其他机器也能直接访问后端 API；仅本机使用可省略该参数）。

模型配置通过环境变量指定（无需交互确认）：
- `VLLM_MAIN_PORT`：主模型端口（默认自动扫描 8001-8003 取第一个可用）
- `VLLM_CLAIM_PORT`：卫星图视觉模型端口（默认取第二个可用端口，或与主模型相同）

#### 启动前端（终端 2）

**Linux / macOS：**

```bash
cd frontend/ui
pnpm dev
```

**Windows (PowerShell)：**

```powershell
cd frontend/ui
pnpm dev
```

`pnpm dev` 已配置绑定 `0.0.0.0`（见 `package.json`），因此：

- 本机访问：`http://localhost:3000`
- 局域网访问：`http://<服务器局域网 IP>:3000`（如 `http://192.168.1.100:3000`）

服务器 IP 可用 `ipconfig`（Windows）或 `ifconfig` / `ip addr`（Linux/macOS）查看。若局域网内无法访问，请检查系统防火墙是否放行了 3000 和 2024 端口。

#### 前端环境变量（可选）

`frontend/ui/.env.local`（已提供默认值，通常无需修改）：

```env
# 浏览器侧：走同源 /api 代理，本机和局域网访问都可用，无需写死服务器 IP
NEXT_PUBLIC_API_URL=/api
NEXT_PUBLIC_ASSISTANT_ID=agent

# 服务端：Next.js 把 /api/* 转发到本机 LangGraph Server
LANGGRAPH_API_URL=http://localhost:2024
```

**为什么用 `/api` 代理而不是 `http://localhost:2024`：** 浏览器里的 `localhost` 指向用户自己的机器。局域网用户打开页面后，若直连 `http://localhost:2024` 会连到他们本机而非服务器。改用相对路径 `/api` 后，所有请求发回页面所在的服务器，再由 Next.js 代理转发到后端，因此任何机器访问都正常。

#### 对话历史持久化

历史对话自动保存，重启服务后仍可查看：

- **存储位置（本地）：**
  - 对话内容本体：`frontend/checkpoints.db`（SQLite），由 `frontend/checkpointer.py` 配置
  - 线程元数据（侧边栏列表）：`frontend/.langgraph_api/`
- **查看方式：** 前端左侧 "Thread History" 面板列出所有历史会话，点击即可恢复完整上下文
- **删除单条：** 鼠标悬停在某条会话上，点击右侧出现的垃圾桶图标，再次点击确认删除
- **原理：** `langgraph.json` 中配置了自定义 SQLite checkpointer（`checkpointer.path`）。`langgraph dev` 默认的 in-memory checkpointer 重启后会丢失 checkpoint（侧边栏有记录但点进去内容为空），SQLite 解决了这个问题

如需清空全部历史，删除 `frontend/checkpoints.db` 和 `frontend/.langgraph_api/` 后重启即可。

---

## 模型分工

| 模型 | 端点 | 用途 |
|------|------|------|
| 主模型 | `10.129.107.145:8001` | clarify、planner、Researcher、reporter、router |
| 视觉模型 | `10.129.107.145:8002` | 卫星图视觉描述 + baseline 存档 |

模型名称启动时自动从 `/v1/models` 查询，无需手填。

---

## 工具详解

### web_search

- **数据源：** DuckDuckGo Search（DDGS）
- **用途：** 建立背景知识、获取政策/新闻/统计报道
- **关键词约束：** 4-6 个词，不写完整句子
- **搜索限制：** 同一方向最多 3 次，超过则记录"数据缺失"

### analyze_satellite_image ★

- **数据源：** Google Earth Engine（GEE）Sentinel-2 影像
- **流程：** 高德 geocode → GEE 下载两期影像 → 视觉模型生成像素级描述
- **给 Researcher 的：** 纯像素级观察，不含推断
- **同时存档：** 完整分析报告写入 `data/baselines/`，用于 eval 对照

### query_poi_history ★

- **数据源：** ohsome API（OSM 历史快照，HeiGIT/海德堡大学，国内可达）
- **流程：** 高德 geocode → ohsome 查历史计数 → 返回起止年份数量及变化率
- **支持类别：** `building` / `road_primary` / `road_secondary` / `hospital` / `school` / `residential` / `commercial` / `park` / `water` / `industrial`

---

## 工具选择策略

Researcher 按以下顺序决定调用哪个工具：

```
1. web_search（必选，先建立文字背景）

2. analyze_satellite_image（可选）
   → 当文字证据提到空间/物理变化时调用做视觉确认
   → 返回像素级观察，需结合其他证据才能得出结论

3. query_poi_history（可选）
   → 有定性结论需要量化支撑时调用
   → OSM 中国覆盖率有限，返回 0 时改用 web_search

置信度：
  三路证据互相印证 → 高，明确陈述
  两路证据          → 中，正常陈述
  单路证据          → 低，标注不确定性
  零证据            → 不写入结论，记录数据缺失
```

---

## 数据源

| 数据源 | 用途 | 访问方式 |
|--------|------|----------|
| DuckDuckGo Search | 网页文字证据 | 无需 Key，需代理 |
| Jina AI | 网页全文抓取（主） | 无需 Key（限速），需代理 |
| mcp-server-fetch | 网页全文抓取（fallback） | 本机进程，需 `uvx` |
| GEE Sentinel-2 | 卫星影像 | 需 GEE 项目 + 代理 |
| ohsome API | OSM 历史 POI 计数 | 免费，国内可达 |
| 高德地图 | 地名 → 经纬度 | 需 API Key |

---

## 状态结构

```python
class ResearchTask(TypedDict):
    id: str           # "task_1"
    topic: str        # "城市空间扩张"
    description: str  # "建成区面积、建设用地扩张趋势"

class AgentState(TypedDict):
    user_input: str
    location: str
    start_year: int
    end_year: int
    batch_mode: bool                               # True 时跳过 clarify/human_approval
    clarify_needed: bool
    clarify_answer: str
    plan: list[ResearchTask]
    findings: Annotated[list[str], operator.add]  # 并发写入，operator.add 自动追加
    report: str

class ResearcherState(TypedDict):
    task: ResearchTask
    location: str
    start_year: int
    end_year: int
    findings: str
    runtime: Annotated[int, operator.add]          # 累计工具调用次数
```

---

## 评估方案

```
幻觉率 = 无法被公开资料核实的陈述 / 报告全部陈述  （越低越好）
覆盖率 = 报告覆盖的真实变化 / 所有已知真实变化    （不能太低）
```

三组实验对同一 test_case 各跑一次，对比幻觉率和覆盖率：

| 实验 | 工具配置 |
|------|----------|
| Baseline A | 仅 web_search |
| Baseline B | web_search + satellite |
| 实验组 | web_search + satellite + poi |

---

## 依赖

```
langgraph
langchain
langchain-openai
langchain-core
ddgs
earthengine-api
httpx
requests
pydantic
python-dotenv
langgraph-cli[inmem]  # Web 模式需要
mcp           # MCP 客户端，web_fetch fallback 用
```

> `mcp-server-fetch` 通过 `uvx` 按需拉取运行，无需加入 requirements.txt。

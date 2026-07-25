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
┌─────────────────────────────────────────────────────┐
│   Researcher × N（各自独立上下文，并发执行）           │
│                                                     │
│   工具集：                                           │
│     web_search           DDGS 网页搜索               │
│     analyze_satellite ★  GEE 卫星图 + 视觉描述       │
│     query_poi_history ★  ohsome OSM 历史计数         │
│                                                     │
│   每个 Researcher 聚焦一个子任务，自主调用工具          │
│   同一方向最多搜索 3 次，找不到则记录数据缺失           │
└─────────────────────────────────────────────────────┘
    │ findings 追加到主图 state
    ▼
reporter
    汇总所有 findings → 主模型生成最终报告
```

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

# 代理（GEE + DDGS 国内必须）
HTTP_PROXY=http://127.0.0.1:7897
HTTPS_PROXY=http://127.0.0.1:7897
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
    clarify_needed: bool
    clarify_answer: str
    plan: list[ResearchTask]
    findings: Annotated[list[str], operator.add]  # 并发写入
    report: str

class ResearcherState(TypedDict):
    task: ResearchTask
    location: str
    start_year: int
    end_year: int
    findings: str
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
duckduckgo-search
earthengine-api
httpx
requests
pydantic
python-dotenv
langgraph-cli[inmem]  # Web 模式需要
```

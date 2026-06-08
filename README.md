# Urban Deep Research Agent

城市变化多工具研究系统 — 基于 LangGraph DeerFlow 架构

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

## 调度模式：DeerFlow 白盒规划

用户可以在执行前审批并修改任务计划：

| 维度 | 本项目（DeerFlow） | ODR 模式 |
|------|-------------------|---------|
| 任务拆分 | Planner 给出计划初稿，用户可修改 | Supervisor 内部决定，用户看不到 |
| 用户确认 | **有**（确认/修改后才执行） | 无 |
| 规划透明度 | 白盒 | 黑盒 |

**选择 DeerFlow 的原因：** 城市分析的研究维度因需求而异，给用户一次确认和调整机会，避免浪费算力跑无关方向。

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

### analyze_satellite_image ★

- **数据源：** Google Earth Engine（GEE）Sentinel-2 影像
- **流程：** 高德 geocode → GEE 下载两期影像 → 小模型（8002）生成像素级视觉描述
- **给 Researcher 的：** 纯像素级观察，不含推断（"左上区域从绿色植被变为灰色地面，约20%面积"）
- **同时存档：** 小模型完整分析报告，写入 `data/baselines/`，用于 eval 对照，Researcher 看不到

### query_poi_history ★

- **数据源：** ohsome API（OSM 历史快照，HeiGIT/海德堡大学，国内可达）
- **流程：** 高德 geocode → ohsome 查历史计数 → 返回起止年份数量及变化率
- **支持类别：** `building` / `road_primary` / `road_secondary` / `hospital` / `school` / `residential` / `commercial` / `park` / `water` / `industrial`
- **geocode 失败时：** 返回提示信息，要求 LLM 换更具体的行政区划名称重试

---

## 工具选择策略

Researcher 按以下顺序决定调用哪个工具：

```
1. web_search（必选，先建立文字背景）

2. analyze_satellite_image（可选）
   → 当文字证据提到空间/物理变化时调用做视觉确认
   → 注意：返回像素级观察，需结合其他证据才能得出结论

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

## 目录结构

```
Urban Agent/
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
│   └── settings.py         vLLM 端点、API Key（模型名自动查询）
│
├── data/
│   ├── satellite_images/   GEE 影像本地缓存
│   └── baselines/          小模型原始报告存档（eval 用）
│
├── eval/
│   └── hallucination_eval.py  三组对比评估脚本（待完善）
│
├── main.py                 CLI 入口（含自然语言解析 + interrupt 交互）
├── .env
└── requirements.txt
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 .env

```bash
# vLLM（模型名自动从 /v1/models 查询，无需手填）
# AGENT_MODEL_NAME=
# CLAIM_MODEL_NAME=

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

### 4. 启动

```bash
python main.py
```

---

## 模型分工

| 模型 | 端点 | 用途 |
|------|------|------|
| 主模型 | `10.129.107.145:8001` | clarify、planner、Researcher、reporter |
| 小模型（有幻觉） | `10.129.107.145:8002` | 卫星图视觉描述 + baseline 存档（eval 对照） |

模型名称启动时自动从 `/v1/models` 查询。

---

## 数据源

| 数据源 | 用途 | 访问方式 |
|--------|------|---------|
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
|------|---------|
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
```

import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── vLLM 服务器 ───────────────────────────────────────────────────────────────
VLLM_BASE = "http://10.129.107.145"
VLLM_API_KEY = "placeholder"
_NO_PROXY = {"http": None, "https": None}

# 模型用途定义
MODEL_ROLES = {
    "agent":  "主模型（clarify / planner / researcher / reporter）",
    "claim":  "小模型（卫星图视觉描述 + baseline 存档）",
    "extra":  "备用模型（预留）",
}

# 端口 → 用途 默认映射
PORT_ROLE_MAP = {
    8001: "agent",
    8002: "claim",
    8003: "extra",
}


def scan_models() -> dict[int, dict]:
    """扫描 8001-8003 端口，返回 {port: {"url": ..., "model": ..., "role": ...}}"""
    available = {}
    for port, role in PORT_ROLE_MAP.items():
        url = f"{VLLM_BASE}:{port}/v1"
        try:
            resp = requests.get(f"{url}/models", proxies=_NO_PROXY, timeout=3)
            data = resp.json()
            models = data.get("data", [])
            if models:
                available[port] = {
                    "url": url,
                    "model": models[0]["id"],
                    "role": role,
                }
        except Exception:
            pass
    return available


def print_model_status(available: dict[int, dict]) -> None:
    """打印模型检测结果。"""
    print("\n" + "=" * 60)
    print("  模型检测")
    print("=" * 60)
    for port in sorted(PORT_ROLE_MAP.keys()):
        role = PORT_ROLE_MAP[port]
        role_desc = MODEL_ROLES[role]
        if port in available:
            info = available[port]
            print(f"  [OK] 端口 {port} → {info['model']}")
            print(f"       用途：{role_desc}")
        else:
            print(f"  [--] 端口 {port} → 不可用")
            print(f"       用途：{role_desc}")
    print("=" * 60)


def confirm_models(available: dict[int, dict]) -> dict[str, str]:
    """
    展示可用模型，让用户确认每个用途使用哪个模型。
    只有一个模型时自动分配，无需确认。
    返回 {"agent": {...}, "claim": {...}, ...}
    """
    if not available:
        print("\n[错误] 没有检测到任何可用模型，请检查 vLLM 服务是否启动")
        return {}

    print_model_status(available)

    # 只有一个模型时，自动分配给所有角色，跳过确认
    if len(available) == 1:
        port, info = next(iter(available.items()))
        print(f"\n仅检测到一个模型（端口 {port}: {info['model']}），自动分配给所有角色。")
        return {
            role: {"port": port, "model": info["model"], "url": info["url"]}
            for role in ["agent", "claim"]
        }

    # 构建默认分配
    assignments = {}
    for port, info in available.items():
        role = info["role"]
        if role not in assignments:
            assignments[role] = {"port": port, "model": info["model"], "url": info["url"]}

    # 检查是否有角色缺失
    missing_roles = [r for r in ["agent", "claim"] if r not in assignments]

    if missing_roles:
        print(f"\n[警告] 以下角色没有默认模型：{', '.join(missing_roles)}")
        print("可用模型：")
        for port, info in available.items():
            print(f"  {port}: {info['model']}")

        for role in missing_roles:
            print(f"\n请为 [{MODEL_ROLES[role]}] 选择端口（输入端口号，如 8001）：")
            choice = input("> ").strip()
            if choice.isdigit() and int(choice) in available:
                port = int(choice)
                assignments[role] = {
                    "port": port,
                    "model": available[port]["model"],
                    "url": available[port]["url"],
                }
            else:
                print(f"[跳过] {role} 未分配模型")

    # 展示最终分配
    print("\n模型分配确认：")
    for role, info in assignments.items():
        print(f"  {MODEL_ROLES[role]}")
        print(f"    → 端口 {info['port']}: {info['model']}")

    confirm = input("\n确认以上配置？(回车确认 / 输入 q 退出)：").strip()
    if confirm.lower() in ("q", "quit", "exit"):
        return {}

    return assignments


# ── 运行时模型配置（由 main.py 启动时设置）─────────────────────────────────────
AGENT_MODEL_URL = ""
AGENT_MODEL_NAME = ""
CLAIM_MODEL_URL = ""
CLAIM_MODEL_NAME = ""


def apply_model_config(assignments: dict[str, dict]) -> None:
    """将用户确认的模型分配应用到全局配置。"""
    global AGENT_MODEL_URL, AGENT_MODEL_NAME, CLAIM_MODEL_URL, CLAIM_MODEL_NAME

    if "agent" in assignments:
        AGENT_MODEL_URL = assignments["agent"]["url"]
        AGENT_MODEL_NAME = assignments["agent"]["model"]
    if "claim" in assignments:
        CLAIM_MODEL_URL = assignments["claim"]["url"]
        CLAIM_MODEL_NAME = assignments["claim"]["model"]

    # 兼容 .env 覆盖
    AGENT_MODEL_NAME = os.getenv("AGENT_MODEL_NAME") or AGENT_MODEL_NAME
    CLAIM_MODEL_NAME = os.getenv("CLAIM_MODEL_NAME") or CLAIM_MODEL_NAME

# ── 高德 ──────────────────────────────────────────────────────────────────────
GAODE_API_KEY = os.getenv("GAODE_API_KEY", "")

# ── Geoapify ──────────────────────────────────────────────────────────────────
GEOAPIFY_API_KEY = os.getenv("GEOAPIFY_API_KEY", "")

# ── Google Earth Engine ───────────────────────────────────────────────────────
GEE_PROJECT = os.getenv("GEE_PROJECT", "")

# ── 代理（GEE 需要翻墙） ──────────────────────────────────────────────────────
HTTP_PROXY = os.getenv("HTTP_PROXY", "")
HTTPS_PROXY = os.getenv("HTTPS_PROXY", HTTP_PROXY)

# ── 影像参数 ──────────────────────────────────────────────────────────────────
IMAGE_BUFFER_METERS = 1500
IMAGE_DIMENSION = 512
IMAGE_CLOUD_PCT = 10
IMAGE_SAVE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "satellite_images"
)

# ── Claim 评分 ────────────────────────────────────────────────────────────────
RESEARCH_WEIGHT = 0.6
POI_WEIGHT = 0.4
CLAIM_THRESHOLD = float(os.getenv("CLAIM_THRESHOLD", "60"))
CLAIM_COUNT = 8

import logging
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── vLLM 服务器 ───────────────────────────────────────────────────────────────
VLLM_BASE = "http://10.129.107.145"
AGENT_MODEL_URL = f"{VLLM_BASE}:8001/v1"
CLAIM_MODEL_URL = f"{VLLM_BASE}:8002/v1"
VLLM_API_KEY = "placeholder"

_NO_PROXY = {"http": None, "https": None}


def _fetch_model_name(base_url: str) -> str:
    """从 vLLM /v1/models 自动获取部署的模型名，失败时返回空字符串。"""
    try:
        resp = requests.get(f"{base_url}/models", proxies=_NO_PROXY, timeout=5)
        data = resp.json()
        models = data.get("data", [])
        if models:
            return models[0]["id"]
    except Exception as e:
        logger.warning("获取模型名失败 (%s): %s", base_url, e)
    return ""


# 优先读 .env，没有则自动 curl 查询
AGENT_MODEL_NAME = os.getenv("AGENT_MODEL_NAME") or _fetch_model_name(AGENT_MODEL_URL)
CLAIM_MODEL_NAME = os.getenv("CLAIM_MODEL_NAME") or _fetch_model_name(CLAIM_MODEL_URL)

if not AGENT_MODEL_NAME:
    logger.warning("AGENT_MODEL_NAME 未能获取，LLM 调用可能失败")
if not CLAIM_MODEL_NAME:
    logger.warning("CLAIM_MODEL_NAME 未能获取，LLM 调用可能失败")

# ── Jina Reader ───────────────────────────────────────────────────────────────
JINA_API_KEY = os.getenv("JINA_API_KEY", "")

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

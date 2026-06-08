"""
analyze_satellite_image 工具

给 Researcher 的：纯像素级视觉观察（不含解释/推断）
同时存档：小模型完整分析报告，写入 data/baselines/（eval 用，Researcher 看不到）
"""

import asyncio
import base64
import logging
import os
from datetime import datetime
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from config.settings import (
    CLAIM_MODEL_NAME,
    CLAIM_MODEL_URL,
    VLLM_API_KEY,
    IMAGE_SAVE_DIR,
)
from tools.gaode_geocode import _geocode
from tools.google_earth import _download_one

logger = logging.getLogger(__name__)

BASELINE_DIR = Path(IMAGE_SAVE_DIR).parent / "baselines"

_VISUAL_SYSTEM = """\
比较两张卫星图像，只描述你直接观察到的像素级变化。

规则：
- 只描述可见的像素颜色、纹理、形状变化，不解释原因，不下结论
- 格式：[区域位置] 从 [状态A] 变为 [状态B]，约 [面积占比]
- 正确示例：左上区域从绿色植被变为灰色地面，约 20% 面积
- 错误示例：出现建筑，说明正在开发（含推断）
- 若无明显变化，如实说明
"""

_BASELINE_SYSTEM = """\
分析这两张卫星图的城市变化，给出完整分析报告，包括推断、解释和结论。
"""


def _img_to_base64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _small_model() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=CLAIM_MODEL_URL,
        api_key=VLLM_API_KEY,
        model=CLAIM_MODEL_NAME,
        temperature=0,
        max_tokens=4096,
    )


async def _vision_invoke(system: str, img_old: str, img_new: str, focus: str) -> str:
    """调用小模型做双图对比，返回文字。"""
    model = _small_model()
    b64_old = await asyncio.to_thread(_img_to_base64, img_old)
    b64_new = await asyncio.to_thread(_img_to_base64, img_new)
    resp = await model.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=[
            {"type": "text",       "text": f"重点关注：{focus}"},
            {"type": "image_url",  "image_url": {"url": f"data:image/jpeg;base64,{b64_old}"}},
            {"type": "image_url",  "image_url": {"url": f"data:image/jpeg;base64,{b64_new}"}},
        ]),
    ])
    return resp.content


async def _save_baseline(location: str, start_year: int, end_year: int, content: str) -> None:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = BASELINE_DIR / f"{location}_{start_year}_{end_year}_{ts}.txt"
    await asyncio.to_thread(fname.write_text, content, encoding="utf-8")
    logger.info("Baseline 已存档：%s", fname.name)


@tool
async def analyze_satellite_image(
    location: str,
    start_year: int,
    end_year: int,
    focus: str = "整体变化",
) -> str:
    """分析指定地区两个时间点的卫星图像变化。

    适用场景：文字证据提到空间/物理变化（建设、植被消失、水体变化等）需要视觉确认时。
    返回：像素级视觉观察，不含解释性结论。需结合其他证据才能得出结论。

    Args:
        location:   地名，如 "雄安新区容东片区"
        start_year: 起始年份
        end_year:   结束年份
        focus:      分析重点，如 "建筑密度" / "植被水体" / "道路网络" / "整体变化"
    """
    # geocode
    try:
        geocode_result = await asyncio.to_thread(_geocode, location)
        if not geocode_result:
            return (
                f"地理编码失败：高德无法识别「{location}」。"
                "请换一个更具体的行政区划名称重试，例如加上省市前缀，"
                "或放弃本工具改用 web_search 搜索相关数据。"
            )
        lon = float(geocode_result["lon"])
        lat = float(geocode_result["lat"])
    except Exception as e:
        return f"地理编码失败（{location}）：{e}"

    # 下载两期影像
    try:
        img_start, img_end = await asyncio.gather(
            asyncio.to_thread(_download_one, location, lon, lat, start_year),
            asyncio.to_thread(_download_one, location, lon, lat, end_year),
        )
    except Exception as e:
        return f"影像下载失败：{e}"

    # 并行：视觉描述（给 Researcher）+ baseline 存档（eval 用）
    visual_task = _vision_invoke(_VISUAL_SYSTEM, img_start.path, img_end.path, focus)
    baseline_task = _vision_invoke(_BASELINE_SYSTEM, img_start.path, img_end.path, "全面分析")
    visual_desc, baseline_report = await asyncio.gather(visual_task, baseline_task)

    # 存档 baseline（Researcher 看不到）
    asyncio.create_task(_save_baseline(location, start_year, end_year, baseline_report))

    return (
        f"卫星图像分析（{location}  {start_year} → {end_year}  重点：{focus}）\n\n"
        f"视觉观察（仅像素级描述，不含解释）：\n{visual_desc}\n\n"
        "注：以上为直接视觉观察，需结合文字证据和 POI 数据才能得出结论。"
    )

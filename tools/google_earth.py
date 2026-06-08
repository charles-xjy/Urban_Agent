import asyncio
import logging
import os
from pathlib import Path

import ee
import requests
from langchain_core.tools import tool

from config.settings import (
    GEE_PROJECT,
    HTTP_PROXY,
    HTTPS_PROXY,
    IMAGE_BUFFER_METERS,
    IMAGE_CLOUD_PCT,
    IMAGE_DIMENSION,
    IMAGE_SAVE_DIR,
)
from core.models import ImageResult

logger = logging.getLogger(__name__)

_ee_initialized = False


def _set_proxy() -> None:
    if HTTP_PROXY:
        os.environ["HTTP_PROXY"] = HTTP_PROXY
        os.environ["HTTPS_PROXY"] = HTTPS_PROXY


def _ensure_ee_initialized() -> None:
    global _ee_initialized
    if _ee_initialized:
        return
    _set_proxy()
    try:
        ee.Initialize(project=GEE_PROJECT)
        _ee_initialized = True
        logger.info("GEE 初始化成功")
    except Exception as e:
        raise RuntimeError(
            f"GEE 初始化失败: {e}\n请先运行: earthengine authenticate"
        ) from e


def _build_collection(roi: ee.Geometry, year: int) -> ee.ImageCollection:
    """
    优先取目标年春夏季（4-9月）云量低的影像，
    若无结果则退化为全年，再退回前一年兜底。
    """
    filters = [
        (f"{year}-04-01", f"{year}-09-30"),   # 春夏，云最少
        (f"{year}-01-01", f"{year}-12-31"),   # 全年
        (f"{year-1}-01-01", f"{year}-12-31"), # 跨前一年兜底
    ]
    for start, end in filters:
        col = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(roi)
            .filterDate(start, end)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", IMAGE_CLOUD_PCT))
        )
        if col.size().getInfo() > 0:
            return col
    return col  # 最后一次结果，即使为空也返回，让调用方处理


def _download_one(name: str, lon: float, lat: float, year: int) -> ImageResult:
    _ensure_ee_initialized()

    save_dir = Path(IMAGE_SAVE_DIR)
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / f"{name}_{year}.jpg"

    if save_path.exists():
        logger.info("缓存命中，跳过下载: %s", save_path.name)
        return ImageResult(location=name, year=year, path=str(save_path), lon=lon, lat=lat)

    roi = ee.Geometry.Point([lon, lat]).buffer(IMAGE_BUFFER_METERS).bounds()
    collection = _build_collection(roi, year)

    count = collection.size().getInfo()
    if count == 0:
        raise ValueError(f"{year} 年附近没有找到符合条件的 Sentinel-2 影像")

    image = collection.median()
    url = image.visualize(bands=["B4", "B3", "B2"], min=0, max=3500, gamma=1.4).getThumbURL(
        {"region": roi, "dimensions": IMAGE_DIMENSION, "format": "jpg"}
    )

    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    save_path.write_bytes(resp.content)
    logger.info("下载完成: %s", save_path.name)

    return ImageResult(location=name, year=year, path=str(save_path), lon=lon, lat=lat)


@tool
async def download_satellite_images(
    name: str,
    lon: float,
    lat: float,
    years: list[int],
) -> list[dict]:
    """
    下载指定地点多个年份的 Sentinel-2 卫星影像。

    参数：
    - name: 地点标识符，用于文件命名
    - lon / lat: 目标经纬度
    - years: 年份列表，如 [2020, 2025]

    返回：
    - ImageResult 列表（含本地文件路径）
    """
    results = []
    for year in years:
        try:
            result = await asyncio.to_thread(_download_one, name, lon, lat, year)
            results.append(result.model_dump())
        except Exception as e:
            logger.warning("年份 %d 下载失败: %s", year, e)
    return results

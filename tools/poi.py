"""
query_poi_history 工具

基于 ohsome API（OSM 历史快照）返回指定地区 POI 数量的历史变化。
数据是客观计数，Researcher 自己解读含义。
"""

import asyncio
import logging
import math

import httpx
from langchain_core.tools import tool

from config.settings import IMAGE_BUFFER_METERS
from tools.gaode_geocode import _geocode

logger = logging.getLogger(__name__)

# category → ohsome OSM filter
_CATEGORY_FILTER: dict[str, str] = {
    "building":        "building=*",
    "road_primary":    "highway=primary or highway=secondary or highway=trunk",
    "road_secondary":  "highway=tertiary or highway=residential or highway=unclassified",
    "hospital":        "amenity=hospital or amenity=clinic",
    "school":          "amenity=school or amenity=university or amenity=college",
    "residential":     "building=residential or building=apartments or landuse=residential",
    "commercial":      "landuse=commercial or shop=* or building=commercial",
    "park":            "leisure=park or landuse=grass or landuse=forest or landuse=recreation_ground",
    "water":           "natural=water or waterway=river or waterway=stream",
    "industrial":      "landuse=industrial or building=industrial",
}

VALID_CATEGORIES = list(_CATEGORY_FILTER.keys())


def _bbox(lon: float, lat: float, radius_m: float) -> str:
    """返回 ohsome 格式 bbox 字符串：west,south,east,north"""
    delta_lat = radius_m / 111_000
    delta_lon = radius_m / (111_000 * math.cos(math.radians(lat)))
    w = lon - delta_lon
    s = lat - delta_lat
    e = lon + delta_lon
    n = lat + delta_lat
    return f"{w:.6f},{s:.6f},{e:.6f},{n:.6f}"


async def _ohsome_count(bbox: str, osm_filter: str, start_year: int, end_year: int) -> tuple[int, int]:
    params = {
        "bboxes": bbox,
        "filter":  osm_filter,
        "time":    f"{start_year}-07-01,{end_year}-07-01",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post("https://api.ohsome.org/v1/elements/count", data=params)
    resp.raise_for_status()
    results = resp.json().get("result", [])
    if len(results) < 2:
        raise ValueError("ohsome 返回结果不足两个时间点")
    return int(results[0]["value"]), int(results[1]["value"])


@tool
async def query_poi_history(
    location: str,
    start_year: int,
    end_year: int,
    category: str,
) -> str:
    """查询指定地区 POI 历史数量变化（基于 OSM 历史快照）。

    适用场景：需要量化数据支撑定性结论时，如"建了多少建筑""道路增加了多少""医院有没有新增"。
    返回：起止年份的 POI 数量及变化率，供你自行判断证据强度。

    Args:
        location:   地名，如 "雄安新区"
        start_year: 起始年份
        end_year:   结束年份
        category:   POI 类别，必须是以下之一：
                    building / road_primary / road_secondary /
                    hospital / school / residential / commercial /
                    park / water / industrial
    """
    if category not in _CATEGORY_FILTER:
        return (
            f"不支持的类别：{category}\n"
            f"可用类别：{', '.join(VALID_CATEGORIES)}"
        )

    # geocode
    try:
        geocode_result = await asyncio.to_thread(_geocode, location)
        if not geocode_result:
            return (
                f"地理编码失败：高德无法识别「{location}」。"
                "请换一个更具体的行政区划名称重试，例如把「白洋淀」改为「河北省保定市白洋淀」，"
                "或放弃本工具改用 web_search 搜索相关数据。"
            )
        lon = float(geocode_result["lon"])
        lat = float(geocode_result["lat"])
    except Exception as e:
        return f"地理编码失败（{location}）：{e}"

    bbox = _bbox(lon, lat, IMAGE_BUFFER_METERS)
    osm_filter = _CATEGORY_FILTER[category]

    try:
        count_old, count_new = await _ohsome_count(bbox, osm_filter, start_year, end_year)
    except Exception as e:
        logger.warning("ohsome 查询失败: %s", e)
        return (
            f"POI 数据获取失败（ohsome API 无响应或该地区数据不足）\n"
            f"建议改用 web_search 搜索相关统计数据。\n错误：{e}"
        )

    if count_old == 0 and count_new == 0:
        return (
            f"POI 历史数据（{location}  {start_year}→{end_year}  类别：{category}）\n"
            "两个时间点均为 0，可能是 OSM 该地区数据覆盖不足。\n"
            "建议改用 web_search 获取文字证据。"
        )

    change = count_new - count_old
    change_rate = change / max(count_old, 1) * 100
    trend = "增加" if change > 0 else ("减少" if change < 0 else "持平")

    return (
        f"POI 历史数据（{location}  {start_year}→{end_year}  类别：{category}）\n"
        f"{start_year} 年：{count_old} 个\n"
        f"{end_year} 年：{count_new} 个\n"
        f"变化：{trend} {abs(change)} 个（{change_rate:+.1f}%）\n"
        f"数据来源：OpenStreetMap 历史快照（ohsome API）\n"
        "注：OSM 在中国部分地区覆盖率有限，建议结合其他证据使用。"
    )

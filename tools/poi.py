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

from config.settings import GAODE_API_KEY, IMAGE_BUFFER_METERS
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

# category → 高德 POI 类型码（None 表示高德无对应类型，不做 fallback）
_GAODE_TYPE: dict[str, str | None] = {
    "building":       None,          # 高德无通用建筑计数
    "road_primary":   None,          # 高德无道路计数
    "road_secondary": None,
    "hospital":       "090000",      # 医疗保健服务
    "school":         "141000",      # 科教文化服务
    "residential":    "120000",      # 商务住宅
    "commercial":     "060000",      # 购物服务
    "park":           "110100",      # 公园广场
    "water":          None,          # 自然水体无 POI 类型
    "industrial":     "150000",      # 工矿用地
}


async def _gaode_poi_count(location: str, type_code: str) -> int:
    """调用高德文本搜索，按地名和 POI 类型返回总数（取 count 字段，不翻页）。"""
    params = {
        "key":       GAODE_API_KEY,
        "types":     type_code,
        "city":      location,
        "citylimit": "true",
        "offset":    1,
        "page":      1,
        "output":    "json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get("https://restapi.amap.com/v3/place/text", params=params)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "1":
        raise ValueError(f"高德 API 错误：{data.get('info', '未知')}")
    return int(data.get("count", 0))


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

    gaode_type = _GAODE_TYPE.get(category)

    # ── 路径 A：geocode → ohsome ──────────────────────────────────────────────
    ohsome_result = None
    geocode_ok = False
    try:
        geocode_result = await asyncio.to_thread(_geocode, location)
        if geocode_result:
            geocode_ok = True
            lon = float(geocode_result["lon"])
            lat = float(geocode_result["lat"])
            bbox = _bbox(lon, lat, IMAGE_BUFFER_METERS)
            osm_filter = _CATEGORY_FILTER[category]
            count_old, count_new = await _ohsome_count(bbox, osm_filter, start_year, end_year)
            if count_old > 0 or count_new > 0:
                ohsome_result = (count_old, count_new)
    except Exception as e:
        logger.warning("ohsome 路径失败（%s）: %s", location, e)

    if ohsome_result is not None:
        count_old, count_new = ohsome_result
        change = count_new - count_old
        change_rate = change / max(count_old, 1) * 100
        trend = "增加" if change > 0 else ("减少" if change < 0 else "持平")
        return (
            f"POI 历史数据（{location}  {start_year}→{end_year}  类别：{category}）\n"
            f"{start_year} 年：{count_old} 个\n"
            f"{end_year} 年：{count_new} 个\n"
            f"变化：{trend} {abs(change)} 个（{change_rate:+.1f}%）\n"
            f"数据来源：OpenStreetMap 历史快照（ohsome API）\n"
            f"数据置信度：0.8（ohsome — OSM 众包数据，中国覆盖率有限）\n"
            "注：建议结合文字证据或卫星图交叉验证。"
        )

    # ── 路径 B：高德实时 fallback ─────────────────────────────────────────────
    # geocode 失败或 ohsome 两端均为 0 时进入此路径
    if gaode_type and GAODE_API_KEY:
        try:
            current_count = await _gaode_poi_count(location, gaode_type)
            if current_count > 0:
                reason = "geocode 失败，无法查询 ohsome" if not geocode_ok else "ohsome OSM 覆盖不足（两端均为 0）"
                return (
                    f"POI 数据（{location}  类别：{category}）\n"
                    f"ohsome 路径：{reason}，已切换至高德实时数据\n"
                    f"高德实时数量（当前）：{current_count} 个\n"
                    f"数据置信度：1.0（高德商业采集，实时准确）\n"
                    "注：仅为当前快照，无法提供历史对比；如需变化趋势，请补充 web_search。"
                )
        except Exception as e:
            logger.warning("高德 POI fallback 失败: %s", e)

    # ── 路径 C：两条路径均无数据 ──────────────────────────────────────────────
    if not geocode_ok:
        return (
            f"POI 查询失败（{location}  类别：{category}）\n"
            "geocode 失败且该类别无高德映射，无法获取任何数据。\n"
            "建议改用 web_search 搜索相关统计数据。"
        )
    return (
        f"POI 历史数据（{location}  {start_year}→{end_year}  类别：{category}）\n"
        "ohsome 和高德均无有效数据。\n"
        "数据置信度：0.0\n"
        "建议改用 web_search 获取文字证据。"
    )

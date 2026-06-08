import requests
from langchain_core.tools import tool

from config.settings import GAODE_API_KEY

_NO_PROXY = {"http": None, "https": None}


def _geocode(address: str, city: str = "") -> dict | None:
    if not GAODE_API_KEY:
        raise RuntimeError("GAODE_API_KEY 未配置")
    params = {"address": address, "output": "json", "key": GAODE_API_KEY}
    if city:
        params["city"] = city
    try:
        resp = requests.get(
            "https://restapi.amap.com/v3/geocode/geo",
            params=params,
            proxies=_NO_PROXY,
            timeout=10,
        )
        data = resp.json()
        if data.get("status") == "1" and data.get("geocodes"):
            lng, lat = data["geocodes"][0]["location"].split(",")
            return {"lon": float(lng), "lat": float(lat)}
    except Exception:
        pass
    return None


@tool
def gaode_geocode(address: str, city: str = "") -> dict:
    """
    将地名或地址转换为经纬度坐标（高德地图）。

    参数：
    - address: 地名或地址，如"北京邮电大学"
    - city: 可选，限定城市提高精度，如"北京市"

    返回：
    - {"lon": 经度, "lat": 纬度} 或 {"error": 错误信息}
    """
    result = _geocode(address, city)
    if result:
        return result
    return {"error": f"高德无法解析地址：{address}"}

from pydantic import BaseModel


class ImageResult(BaseModel):
    location: str
    year: int
    path: str
    lon: float
    lat: float

"""지역 표 — 지역명 → 위경도 → 기상청 격자(nx, ny) · 중기예보 구역코드.

외부 API 에 의존하지 않는 로컬 표다. 카카오로 지오코딩하면 카카오 키 누락(R4) 때
날씨·박명까지 함께 실패하므로 여기에 둔다. 모델의 판단이 필요 없는 변환이라 Tool 이 아니다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Region:
    name: str
    lat: float
    lon: float
    aliases: tuple[str, ...] = ()
    mid_land_reg_id: str = "11B00000"   # 중기육상예보 regId
    province: str = ""

    @property
    def grid(self) -> tuple[int, int]:
        return latlon_to_grid(self.lat, self.lon)


# 중기육상예보 regId: 11B00000 서울·인천·경기 / 11D10000 강원영서 / 11D20000 강원영동 /
# 11C20000 대전·세종·충남 / 11C10000 충북 / 11F20000 광주·전남 / 11F10000 전북 /
# 11H10000 대구·경북 / 11H20000 부산·울산·경남 / 11G00000 제주
REGIONS: tuple[Region, ...] = (
    Region("서울", 37.5665, 126.9780, ("서울시", "서울특별시"), "11B00000", "서울"),
    Region("인천", 37.4563, 126.7052, ("인천시", "인천광역시"), "11B00000", "인천"),
    Region("수원", 37.2636, 127.0286, ("수원시",), "11B00000", "경기"),
    Region("양평", 37.4917, 127.4875, ("양평군",), "11B00000", "경기"),
    Region("가평", 37.8315, 127.5095, ("가평군",), "11B00000", "경기"),
    Region("춘천", 37.8813, 127.7298, ("춘천시", "강원", "강원도", "강원특별자치도"), "11D10000", "강원"),
    Region("원주", 37.3422, 127.9202, ("원주시",), "11D10000", "강원"),
    Region("영월", 37.1836, 128.4617, ("영월군",), "11D10000", "강원"),
    Region("평창", 37.3705, 128.3903, ("평창군",), "11D10000", "강원"),
    Region("강릉", 37.7519, 128.8761, ("강릉시",), "11D20000", "강원"),
    Region("태백", 37.1641, 128.9856, ("태백시",), "11D20000", "강원"),
    Region("대전", 36.3504, 127.3845, ("대전시", "대전광역시"), "11C20000", "대전"),
    Region("세종", 36.4800, 127.2890, ("세종시", "세종특별자치시"), "11C20000", "세종"),
    Region("천안", 36.8151, 127.1139, ("천안시", "충남", "충청남도"), "11C20000", "충남"),
    Region("청주", 36.6424, 127.4890, ("청주시", "충북", "충청북도"), "11C10000", "충북"),
    Region("단양", 36.9845, 128.3655, ("단양군",), "11C10000", "충북"),
    Region("광주", 35.1595, 126.8526, ("광주시", "광주광역시"), "11F20000", "광주"),
    Region("순천", 34.9507, 127.4872, ("순천시", "전남", "전라남도"), "11F20000", "전남"),
    Region("전주", 35.8242, 127.1480, ("전주시", "전북", "전라북도", "전북특별자치도"), "11F10000", "전북"),
    Region("무주", 36.0071, 127.6608, ("무주군",), "11F10000", "전북"),
    Region("대구", 35.8714, 128.6014, ("대구시", "대구광역시"), "11H10000", "대구"),
    Region("영천", 35.9733, 128.9386, ("영천시", "경북", "경상북도"), "11H10000", "경북"),
    Region("안동", 36.5684, 128.7294, ("안동시",), "11H10000", "경북"),
    Region("부산", 35.1796, 129.0756, ("부산시", "부산광역시"), "11H20000", "부산"),
    Region("울산", 35.5384, 129.3114, ("울산시", "울산광역시"), "11H20000", "울산"),
    Region("창원", 35.2280, 128.6811, ("창원시", "경남", "경상남도"), "11H20000", "경남"),
    Region("제주", 33.4996, 126.5312, ("제주시", "제주도", "제주특별자치도"), "11G00000", "제주"),
    Region("서귀포", 33.2541, 126.5601, ("서귀포시",), "11G00000", "제주"),
)

_INDEX: dict[str, Region] = {}
for _r in REGIONS:
    _INDEX[_r.name] = _r
    for _a in _r.aliases:
        _INDEX.setdefault(_a, _r)


def find_region(text: str | None) -> Region | None:
    """지역명(또는 별칭)으로 찾는다. '서울 근처', '대전에서' 같은 조사는 떼고 본다."""
    if not text:
        return None
    key = text.strip()
    if key in _INDEX:
        return _INDEX[key]
    for suffix in ("에서", "근처", "근교", "쪽", "에", "으로", "로", "시", "도", "군"):
        if key.endswith(suffix) and key[: -len(suffix)] in _INDEX:
            return _INDEX[key[: -len(suffix)]]
    for name, region in _INDEX.items():
        if name in key:
            return region
    return None


def nearby_regions(region: Region, limit: int = 3) -> list[Region]:
    """재계획 ③ '인접 지역' 후보 — 거리순, 자신 제외."""
    others = [r for r in REGIONS if r.name != region.name]
    others.sort(key=lambda r: haversine_km(region.lat, region.lon, r.lat, r.lon))
    return others[:limit]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def latlon_to_grid(lat: float, lon: float) -> tuple[int, int]:
    """기상청 단기예보 격자 변환 (Lambert Conformal Conic, 기상청 공식 파라미터)."""
    RE, GRID = 6371.00877, 5.0
    SLAT1, SLAT2, OLON, OLAT, XO, YO = 30.0, 60.0, 126.0, 38.0, 43, 136
    deg = math.pi / 180.0
    re = RE / GRID
    slat1, slat2, olon, olat = SLAT1 * deg, SLAT2 * deg, OLON * deg, OLAT * deg
    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = sf**sn * math.cos(slat1) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re * sf / ro**sn
    ra = math.tan(math.pi * 0.25 + lat * deg * 0.5)
    ra = re * sf / ra**sn
    theta = lon * deg - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn
    x = int(ra * math.sin(theta) + XO + 0.5)
    y = int(ro - ra * math.cos(theta) + YO + 0.5)
    return x, y

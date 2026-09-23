# ============================================================
# 미니실습02 — 제주 핫플레이스 분석 (관광공사 + 카카오 + 네이버 + YouTube)
# Google Colab 단일 셀 실행용 통합 코드
#
# ▶ 실행 전 준비물 (Colab 좌측 열쇠 아이콘 → 보안 비밀에 등록, "노트북 액세스" 토글 ON)
#     TOUR_API_KEY        : 한국관광공사 TourAPI 서비스키 (공공데이터포털)
#     KAKAO_REST_KEY      : 카카오 디벨로퍼스 REST API 키
#     NAVER_CLIENT_ID     : 네이버 API HUB(NCP) Client ID
#     NAVER_CLIENT_SECRET : 네이버 API HUB(NCP) Client Secret
#     YOUTUBE_API_KEY     : Google Cloud Console에서 발급한 YouTube Data API v3 키
#
# ▶ 실행 순서: 이 셀 전체를 그대로 복사해 Colab 새 셀에 붙여넣고 한 번에 실행하면
#              STEP 1~14가 순서대로 실행되며, 아래 파일들이 Google Drive에 생성됩니다.
#     내 드라이브/jeju_hotplace_project/
#       ├─ data/raw_data.csv                 (STEP 5 산출)
#       ├─ data/cleaned_data.csv             (STEP 12 산출, 최종 분석 결과)
#       ├─ data/jeju_hangjeongdong.geojson   (STEP 14, 최초 1회 다운로드 후 캐시)
#       ├─ data/youtube_cache.csv            (STEP 10, 유튜브 수집 체크포인트)
#       └─ output/
#            ├─ jeju_hotplace_map.html        (STEP 14 최종 지도)
#            └─ jeju_hotplace_wordcloud.png   (STEP 13 워드클라우드)
#
# ▶ 유튜브 API 쿼터 안내: search.list 호출 1회당 100 unit 소모.
#   장소 40곳(관광지20+맛집20) × 100 unit = 4,000 unit (일일 기본 할당량 10,000 중 40%)
#   → 시연 1회 실행에는 문제없으나, 같은 날 여러 번 재실행하면 한도를 초과할 수 있습니다.
#   한도를 다 쓴 경우 STEP 10은 자동으로 중단되며, data/youtube_cache.csv에 그때까지
#   수집된 결과가 저장되어 있어 다음 날 재실행 시 이어서 수집합니다.
# ============================================================

# ============================================================
# STEP 1. 필요한 라이브러리 설치
# ============================================================
import sys
import subprocess

subprocess.run(
    [sys.executable, "-m", "pip", "install", "-q",
     "folium", "branca", "tqdm", "shapely", "wordcloud"],
    check=True,
)
# 워드클라우드에서 한글이 깨지지 않도록 나눔 폰트 설치
subprocess.run(["apt-get", "-qq", "-y", "install", "fonts-nanum"], check=True)

# ============================================================
# STEP 2. 라이브러리 임포트 및 Google Drive 연결
# ============================================================
from google.colab import drive, userdata
from pathlib import Path
from urllib.parse import unquote
from datetime import datetime
from IPython.display import display, HTML
import time
import re
import io
import json
import base64
import html
import hashlib
from collections import Counter
import requests
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import folium
from folium.plugins import MarkerCluster
from branca.element import Element
from tqdm.auto import tqdm
from wordcloud import WordCloud
from shapely.geometry import shape, mapping
from shapely.ops import unary_union

drive.mount("/content/drive")

# 실행하는 사람의 개인 드라이브 안에 자동으로 폴더가 생성됩니다.
# (다른 사람이 실행해도 동일한 하위 구조가 각자의 드라이브에 그대로 만들어집니다)
ROOT = Path("/content/drive/MyDrive/jeju_hotplace_project")
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"

for folder in (DATA_DIR, OUTPUT_DIR):
    folder.mkdir(parents=True, exist_ok=True)

print("✅ 프로젝트 폴더:", ROOT)

# ============================================================
# STEP 3. Colab 보안 비밀에서 API 키 불러오기
# ============================================================
def get_secret(name, required=True):
    try:
        value = userdata.get(name)
        if value and value.strip():
            return value.strip()
    except Exception:
        pass

    if required:
        raise RuntimeError(
            f"{name}을 Colab 보안 비밀에 등록하고 노트북 액세스를 켜주세요."
        )
    return None


TOUR_API_KEY = unquote(get_secret("TOUR_API_KEY"))
KAKAO_REST_KEY = get_secret("KAKAO_REST_KEY")
NAVER_CLIENT_ID = get_secret("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = get_secret("NAVER_CLIENT_SECRET")
YOUTUBE_API_KEY = get_secret("YOUTUBE_API_KEY")  # 실제 API 사용 (더미 아님)

print("✅ 관광공사·카카오·네이버·YouTube API 키 불러오기 완료")

# ============================================================
# STEP 4. 실행 설정
# ============================================================
CONFIG = {
    "TOUR_URL": "https://apis.data.go.kr/B551011/KorService2/areaBasedList2",
    "KAKAO_REGION_URL": "https://dapi.kakao.com/v2/local/geo/coord2regioncode.json",
    "YOUTUBE_SEARCH_URL": "https://www.googleapis.com/youtube/v3/search",
    "YOUTUBE_VIDEOS_URL": "https://www.googleapis.com/youtube/v3/videos",
    "AREA_CODE": "39",
    "TOURIST_COUNT": 20,
    "FOOD_COUNT": 20,
    "YOUTUBE_MAX_RESULTS": 50,   # search.list 쿼터는 결과 수와 무관하게 호출당 100 unit 고정
    "USE_YOUTUBE_DUMMY": False,  # ⚠️ 실제 제출 버전: 반드시 False (실제 API 사용)
    "NAVER_TREND_URL": "https://naverapihub.apigw.ntruss.com/search-trend/v1/search",
    "NAVER_BLOG_URL": "https://naverapihub.apigw.ntruss.com/search/v1/blog",
    "NAVER_BLOG_TEXT_DISPLAY": 10,  # 블로그 건수(total) 집계와 워드클라우드용 텍스트를 한 번에 수집
    "REQUEST_INTERVAL": 0.25,
    "TIMEOUT": 30,
    "MAP_CENTER": [33.38, 126.55],
    "MAP_ZOOM": 10,
}

TODAY = datetime.now().strftime("%Y%m%d")

# ============================================================
# STEP 5. 관광공사 TourAPI 원본 데이터 수집 (raw_data.csv)
# contentTypeId 12=관광지, 39=음식점
# ============================================================
def fetch_tourapi(content_type_id, category, num_rows):
    params = {
        "serviceKey": TOUR_API_KEY,
        "MobileOS": "ETC",
        "MobileApp": "JejuHotplaceProject",
        "_type": "json",
        "areaCode": CONFIG["AREA_CODE"],
        "contentTypeId": str(content_type_id),
        "arrange": "Q",
        "numOfRows": int(num_rows),
        "pageNo": 1,
    }

    response = requests.get(CONFIG["TOUR_URL"], params=params, timeout=CONFIG["TIMEOUT"])
    if response.status_code != 200:
        raise RuntimeError(
            f"TourAPI {category} 호출 실패: HTTP {response.status_code} / {response.text[:300]}"
        )

    try:
        payload = response.json()
    except ValueError as error:
        raise RuntimeError(f"TourAPI 응답이 JSON이 아닙니다: {response.text[:300]}") from error

    api_response = payload.get("response", {})
    header = api_response.get("header", {})
    if str(header.get("resultCode")) != "0000":
        raise RuntimeError(f"TourAPI 오류: {header.get('resultCode')} / {header.get('resultMsg')}")

    body = api_response.get("body", {})
    items = body.get("items") or {}
    records = items.get("item", []) if isinstance(items, dict) else []
    if isinstance(records, dict):
        records = [records]

    result = pd.DataFrame(records)
    if not result.empty:
        result["category"] = category

    print(f"✅ {category}: {len(result)}건 / API 전체 {body.get('totalCount', 0)}건")
    return result


tourist_df = fetch_tourapi(12, "관광지", CONFIG["TOURIST_COUNT"])
time.sleep(CONFIG["REQUEST_INTERVAL"])
food_df = fetch_tourapi(39, "맛집/음식점", CONFIG["FOOD_COUNT"])

tourapi_raw = pd.concat([tourist_df, food_df], ignore_index=True)
if tourapi_raw.empty:
    raise RuntimeError("TourAPI에서 수집된 데이터가 없습니다.")

raw_path = DATA_DIR / "raw_data.csv"
tourapi_raw.to_csv(raw_path, index=False, encoding="utf-8-sig")
print("✅ 관광공사 원본 CSV 저장:", raw_path)

# ============================================================
# STEP 6. 데이터 정제 (좌표·이름 정리, 중복 제거)
# ============================================================
required_tour_columns = ["title", "addr1", "mapy", "mapx", "category"]
missing_tour_columns = [c for c in required_tour_columns if c not in tourapi_raw.columns]
if missing_tour_columns:
    raise RuntimeError(f"TourAPI 필수 컬럼 누락: {missing_tour_columns}")

places = tourapi_raw.copy()
places["name"] = places["title"].fillna("").astype(str).str.strip()
places["address"] = places["addr1"].fillna("").astype(str).str.strip()
places["latitude"] = pd.to_numeric(places["mapy"], errors="coerce")
places["longitude"] = pd.to_numeric(places["mapx"], errors="coerce")
places["image_url"] = (
    places["firstimage"].fillna("").astype(str).str.strip()
    if "firstimage" in places.columns
    else ""
)

places = (
    places.dropna(subset=["latitude", "longitude"])
    .loc[lambda frame: frame["name"].ne("")]
    .drop_duplicates(subset=["name", "latitude", "longitude"])
    .reset_index(drop=True)
)

if places.empty:
    raise RuntimeError("좌표가 존재하는 장소가 없습니다.")

print("✅ 장소 정제 완료:", len(places), "건")

# ============================================================
# STEP 7. 카카오 API로 좌표 → 읍/면/동 14개 권역 분류
# ============================================================
KAKAO_HEADERS = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}


def to_zone(city, town):
    city = (city or "").strip()
    town = (town or "").strip()
    if town.endswith("읍") or town.endswith("면"):
        return town
    if city in {"제주시", "서귀포시"}:
        return f"{city} 동지역"
    return "미분류"


def get_kakao_region(longitude, latitude):
    try:
        response = requests.get(
            CONFIG["KAKAO_REGION_URL"],
            headers=KAKAO_HEADERS,
            params={"x": longitude, "y": latitude},
            timeout=CONFIG["TIMEOUT"],
        )
        if response.status_code != 200:
            return {"region_status": "error", "city": "", "town": "", "zone": "미분류",
                    "region_error": f"HTTP {response.status_code}"}

        documents = response.json().get("documents", [])
        selected = next((d for d in documents if d.get("region_type") == "H"), None)
        if selected is None and documents:
            selected = documents[0]
        if selected is None:
            return {"region_status": "empty", "city": "", "town": "", "zone": "미분류",
                    "region_error": "행정구역 결과 없음"}

        city = selected.get("region_2depth_name", "")
        town = selected.get("region_3depth_name", "").split()[0]
        return {"region_status": "ok", "city": city, "town": town,
                "zone": to_zone(city, town), "region_error": ""}
    except requests.RequestException as error:
        return {"region_status": "error", "city": "", "town": "", "zone": "미분류",
                "region_error": str(error)}


region_results = []
for row in tqdm(places.itertuples(index=False), total=len(places), desc="카카오 권역 분류"):
    region_results.append(get_kakao_region(row.longitude, row.latitude))
    time.sleep(CONFIG["REQUEST_INTERVAL"])

region_df = pd.DataFrame(region_results)
places = pd.concat([places.reset_index(drop=True), region_df], axis=1)
places["zone"] = places["zone"].astype(str).str.strip()
print("✅ 카카오 권역 분류 완료")
display(places["zone"].value_counts(dropna=False).to_frame("장소 수"))

# ============================================================
# STEP 8. 네이버 Search Trend — 검색 관심도 & 전년 동기 대비 증감
# ============================================================
NAVER_HEADERS = {
    "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
    "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
    "Content-Type": "application/json",
}

naver_end = (pd.Timestamp.today().normalize().replace(day=1) - pd.Timedelta(days=1))
naver_start = (naver_end.replace(day=1) - pd.DateOffset(months=23))


def collect_naver_batch(place_names):
    keyword_groups = [{"groupName": "__ANCHOR__", "keywords": ["제주도", "제주 여행"]}]
    for index, name in enumerate(place_names):
        keyword_groups.append({"groupName": f"P{index}", "keywords": [name, f"제주 {name}"]})

    body = {
        "startDate": naver_start.strftime("%Y-%m-%d"),
        "endDate": naver_end.strftime("%Y-%m-%d"),
        "timeUnit": "month",
        "keywordGroups": keyword_groups,
    }
    response = requests.post(CONFIG["NAVER_TREND_URL"], headers=NAVER_HEADERS, json=body,
                              timeout=CONFIG["TIMEOUT"])
    if response.status_code != 200:
        raise RuntimeError(
            f"네이버 Search Trend 호출 실패: HTTP {response.status_code} / {response.text[:500]}\n"
            "NAVER API HUB 애플리케이션에서 Search Trend 사용 설정을 확인하세요."
        )

    results = response.json().get("results", [])
    by_title = {item.get("title"): item.get("data", []) for item in results}

    def period_map(data):
        return {str(item.get("period")): float(item.get("ratio", 0) or 0) for item in data}

    anchor = period_map(by_title.get("__ANCHOR__", []))
    output = []
    for index, name in enumerate(place_names):
        target = period_map(by_title.get(f"P{index}", []))
        periods = sorted(set(anchor) | set(target))
        normalized = []
        for period in periods:
            anchor_value = anchor.get(period, 0.0)
            target_value = target.get(period, 0.0)
            normalized.append(
                (period, target_value / anchor_value * 100) if anchor_value > 0 else (period, np.nan)
            )

        values = pd.Series(
            [value for _, value in normalized],
            index=[period for period, _ in normalized], dtype="float64",
        ).dropna()
        recent = values.iloc[-12:]
        previous = values.iloc[-24:-12]
        recent_mean = float(recent.mean()) if not recent.empty else np.nan
        previous_mean = float(previous.mean()) if not previous.empty else np.nan
        growth = (
            (recent_mean - previous_mean) / previous_mean * 100
            if pd.notna(previous_mean) and previous_mean > 0 else np.nan
        )
        output.append({
            "name": name,
            "naver_status": "ok" if pd.notna(recent_mean) else "empty",
            "naver_interest": recent_mean,
            "naver_yoy_growth": growth,
            "naver_period_start": naver_start.strftime("%Y-%m-%d"),
            "naver_period_end": naver_end.strftime("%Y-%m-%d"),
        })
    return output


naver_results = []
place_names = places["name"].astype(str).tolist()
for start_index in tqdm(range(0, len(place_names), 4), desc="네이버 검색 관심도 수집"):
    batch_names = place_names[start_index: start_index + 4]
    naver_results.extend(collect_naver_batch(batch_names))
    time.sleep(CONFIG["REQUEST_INTERVAL"])

naver_df = pd.DataFrame(naver_results).drop_duplicates(subset=["name"], keep="last")
places = places.merge(naver_df, on="name", how="left", validate="many_to_one")

naver_success = int(places["naver_status"].eq("ok").sum())
print(f"✅ 네이버 실제 검색 관심도: {naver_success}/{len(places)}건")
if naver_success == 0:
    raise RuntimeError("네이버 Search Trend에서 계산 가능한 실제 결과가 없습니다.")

# ============================================================
# STEP 9. 네이버 블로그 — 언급 건수 + 워드클라우드용 텍스트 수집
# (건수 집계와 텍스트 수집을 API 1회 호출로 함께 처리)
# ============================================================
def get_naver_blog_data(place_name):
    response = requests.get(
        CONFIG["NAVER_BLOG_URL"],
        headers={
            "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
            "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
        },
        params={"query": f'"{place_name}" 제주', "display": CONFIG["NAVER_BLOG_TEXT_DISPLAY"],
                 "start": 1, "sort": "sim"},
        timeout=CONFIG["TIMEOUT"],
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"네이버 블로그 검색 실패({place_name}): HTTP {response.status_code} / {response.text[:300]}"
        )
    payload = response.json()
    total = int(payload.get("total", 0) or 0)
    texts = [f"{item.get('title', '')} {item.get('description', '')}" for item in payload.get("items", [])]
    return total, texts


blog_mentions = []
blog_text_corpus = []  # 워드클라우드 재료
for place_name in tqdm(places["name"], desc="네이버 블로그 언급 수집"):
    total, texts = get_naver_blog_data(place_name)
    blog_mentions.append(total)
    blog_text_corpus.extend(texts)
    time.sleep(CONFIG["REQUEST_INTERVAL"])

places["naver_blog_mentions"] = blog_mentions
print(f"✅ 네이버 블로그 언급 수집 완료 (워드클라우드용 텍스트 {len(blog_text_corpus)}건 확보)")

# ============================================================
# STEP 10. YouTube Data API v3 — 실제 지표 수집
# 검색 결과(최대 50건, 최신순)를 "최근 6개월"과 "이전 6~12개월" 구간으로 나눠
# 각 구간의 고유 채널 수를 비교해 "언급 증가율"을 계산합니다.
# ============================================================
def youtube_error_message(response):
    try:
        return response.json().get("error", {}).get("message", response.text[:300])
    except ValueError:
        return response.text[:300]


def get_youtube_metrics(place_name):
    base_result = {
        "youtube_status": "error",
        "youtube_error": "",
        "youtube_video_count": 0,
        "youtube_channel_count": 0,
        "youtube_total_views": 0,
        "youtube_total_likes": 0,
        "youtube_total_comments": 0,
        "youtube_unique_channel_mentions": 0,
        "youtube_previous_channel_mentions": 0,
        "youtube_mention_growth": 0.0,
    }

    try:
        search_response = requests.get(
            CONFIG["YOUTUBE_SEARCH_URL"],
            params={
                "key": YOUTUBE_API_KEY,
                "part": "snippet",
                "q": f"제주 {place_name}",
                "type": "video",
                "order": "date",  # 최신순으로 받아서 최근/이전 구간을 client-side로 분리
                "maxResults": CONFIG["YOUTUBE_MAX_RESULTS"],
                "regionCode": "KR",
                "relevanceLanguage": "ko",
                "safeSearch": "moderate",
            },
            timeout=CONFIG["TIMEOUT"],
        )

        if search_response.status_code != 200:
            base_result["youtube_error"] = (
                f"search HTTP {search_response.status_code}: {youtube_error_message(search_response)}"
            )
            return base_result

        search_items = search_response.json().get("items", [])
        video_infos = [
            {
                "video_id": item.get("id", {}).get("videoId"),
                "channel_id": item.get("snippet", {}).get("channelId"),
                "published_at": item.get("snippet", {}).get("publishedAt"),
            }
            for item in search_items
            if item.get("id", {}).get("videoId")
        ]

        if not video_infos:
            base_result.update({"youtube_status": "empty", "youtube_error": "검색 영상 없음"})
            return base_result

        now = pd.Timestamp.utcnow()
        recent_cutoff = now - pd.DateOffset(months=6)
        previous_cutoff = now - pd.DateOffset(months=12)

        recent_channels, previous_channels, all_channels = set(), set(), set()
        for info in video_infos:
            all_channels.add(info["channel_id"])
            try:
                published = pd.Timestamp(info["published_at"])
            except (TypeError, ValueError):
                continue
            if published >= recent_cutoff:
                recent_channels.add(info["channel_id"])
            elif published >= previous_cutoff:
                previous_channels.add(info["channel_id"])

        recent_count = len(recent_channels)
        previous_count = len(previous_channels)
        growth = (
            (recent_count - previous_count) / previous_count * 100
            if previous_count > 0
            else (100.0 if recent_count > 0 else 0.0)
        )

        video_ids = [info["video_id"] for info in video_infos]
        videos_response = requests.get(
            CONFIG["YOUTUBE_VIDEOS_URL"],
            params={"key": YOUTUBE_API_KEY, "part": "statistics", "id": ",".join(video_ids)},
            timeout=CONFIG["TIMEOUT"],
        )

        if videos_response.status_code != 200:
            base_result["youtube_error"] = (
                f"videos HTTP {videos_response.status_code}: {youtube_error_message(videos_response)}"
            )
            return base_result

        statistics = [item.get("statistics", {}) for item in videos_response.json().get("items", [])]

        def total(field):
            return sum(int(stat.get(field, 0) or 0) for stat in statistics)

        base_result.update({
            "youtube_status": "ok",
            "youtube_error": "",
            "youtube_video_count": len(statistics),
            "youtube_channel_count": len(all_channels),
            "youtube_total_views": total("viewCount"),
            "youtube_total_likes": total("likeCount"),
            "youtube_total_comments": total("commentCount"),
            "youtube_unique_channel_mentions": recent_count,
            "youtube_previous_channel_mentions": previous_count,
            "youtube_mention_growth": round(growth, 2),
        })
        return base_result

    except requests.RequestException as error:
        base_result["youtube_error"] = str(error)
        return base_result
    except (TypeError, ValueError) as error:
        base_result["youtube_error"] = f"응답 변환 오류: {error}"
        return base_result


YOUTUBE_CACHE_PATH = DATA_DIR / "youtube_cache.csv"  # 체크포인트: 중단돼도 이어서 수집

youtube_metric_columns = [
    "youtube_data_source", "youtube_status", "youtube_error",
    "youtube_video_count", "youtube_channel_count",
    "youtube_total_views", "youtube_total_likes", "youtube_total_comments",
    "youtube_unique_channel_mentions", "youtube_previous_channel_mentions",
    "youtube_mention_growth",
]
youtube_cache_columns = ["name"] + youtube_metric_columns

if CONFIG["USE_YOUTUBE_DUMMY"]:
    # 참고용 폴백: API 쿼터가 소진됐을 때만 임시로 켜서 파이프라인 나머지 부분을 테스트하는 용도.
    # 제출/시연 버전에서는 사용하지 않음 (CONFIG["USE_YOUTUBE_DUMMY"] = False 고정).
    dummy_rows = []
    for place_name in places["name"].astype(str):
        seed = int(hashlib.sha256(place_name.encode("utf-8")).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        video_count = int(rng.integers(5, CONFIG["YOUTUBE_MAX_RESULTS"] + 1))
        channel_count = int(rng.integers(3, video_count + 1))
        previous_channel_count = int(rng.integers(2, CONFIG["YOUTUBE_MAX_RESULTS"] + 1))
        mention_growth = (
            (channel_count - previous_channel_count) / previous_channel_count * 100
            if previous_channel_count > 0 else 0.0
        )
        total_views = int(rng.integers(50_000, 5_000_001))
        dummy_rows.append({
            "name": place_name, "youtube_data_source": "DUMMY_TEMPORARY",
            "youtube_status": "dummy", "youtube_error": "테스트용 임시 데이터",
            "youtube_video_count": video_count, "youtube_channel_count": channel_count,
            "youtube_total_views": total_views,
            "youtube_total_likes": int(total_views * rng.uniform(0.015, 0.075)),
            "youtube_total_comments": int(total_views * rng.uniform(0.0005, 0.008)),
            "youtube_unique_channel_mentions": channel_count,
            "youtube_previous_channel_mentions": previous_channel_count,
            "youtube_mention_growth": mention_growth,
        })
    youtube_cache = pd.DataFrame(dummy_rows)
    print("⚠️ 테스트 모드: YouTube 더미 데이터 사용 중 (제출용 아님)")

else:
    if YOUTUBE_CACHE_PATH.exists():
        youtube_cache = pd.read_csv(YOUTUBE_CACHE_PATH, encoding="utf-8-sig")
        for column in youtube_cache_columns:
            if column not in youtube_cache.columns:
                youtube_cache[column] = np.nan
        youtube_cache = (
            youtube_cache[youtube_cache_columns]
            .drop_duplicates(subset=["name"], keep="last")
            .reset_index(drop=True)
        )
        print(f"✅ 기존 YouTube 캐시 이어서 사용: {len(youtube_cache)}건")
    else:
        youtube_cache = pd.DataFrame(columns=youtube_cache_columns)
        print("ℹ️ 기존 YouTube 캐시 없음 — 새로 수집 시작")

    current_names = set(places["name"].astype(str))
    successful_names = set(
        youtube_cache.loc[
            youtube_cache["youtube_status"].eq("ok")
            & youtube_cache["name"].astype(str).isin(current_names),
            "name",
        ].astype(str)
    )

    quota_exceeded = False
    for place_name in tqdm(places["name"], desc="YouTube 실제 지표 수집"):
        if place_name in successful_names:
            continue

        result = get_youtube_metrics(place_name)
        result["youtube_data_source"] = "YOUTUBE_API"
        error_message = str(result.get("youtube_error", ""))

        if "429" in error_message or "quotaExceeded" in error_message or "Quota exceeded" in error_message:
            quota_exceeded = True
            print(f"\n⚠️ YouTube 일일 검색 한도 소진 · 중단 장소: {place_name}")
            print("⚠️", error_message)
            break

        result["name"] = place_name
        youtube_cache = youtube_cache.loc[youtube_cache["name"].astype(str).ne(str(place_name))]
        youtube_cache = pd.concat(
            [youtube_cache, pd.DataFrame([result])[youtube_cache_columns]], ignore_index=True
        )
        youtube_cache.to_csv(YOUTUBE_CACHE_PATH, index=False, encoding="utf-8-sig")  # 매 건 체크포인트

        if result["youtube_status"] == "ok":
            successful_names.add(place_name)

        time.sleep(CONFIG["REQUEST_INTERVAL"])

    youtube_cache.to_csv(YOUTUBE_CACHE_PATH, index=False, encoding="utf-8-sig")
    print("✅ YouTube 데이터 저장:", YOUTUBE_CACHE_PATH)

places = places.drop(columns=youtube_metric_columns, errors="ignore")
places = places.merge(youtube_cache[youtube_cache_columns], on="name", how="left", validate="many_to_one")

places["youtube_status"] = places["youtube_status"].fillna("not_collected")
places["youtube_error"] = places["youtube_error"].fillna("아직 수집하지 않음")

for column in ["youtube_video_count", "youtube_channel_count", "youtube_total_views",
               "youtube_total_likes", "youtube_total_comments"]:
    places[column] = pd.to_numeric(places[column], errors="coerce")

success_count = int(places["youtube_status"].isin(["ok", "dummy"]).sum())
print(f"✅ YouTube 정상 데이터: {success_count}/{len(places)}건")

if success_count == 0:
    errors = places["youtube_error"].value_counts().head(5).to_dict()
    raise RuntimeError(
        "YouTube 정상 수집 결과가 없습니다. 키·API 활성화 상태와 쿼터를 확인하세요. "
        f"주요 오류: {errors}"
    )

# ============================================================
# STEP 11. 핫플 점수 계산 (100점 만점)
# YouTube 채널 언급 25 + YouTube 언급 변화 15
# 네이버 블로그 언급 25 + 네이버 검색 관심도 20 + 전년 대비 변화 15
# ============================================================
metric_columns = [
    "youtube_unique_channel_mentions", "youtube_previous_channel_mentions", "youtube_mention_growth",
    "naver_blog_mentions", "naver_interest", "naver_yoy_growth",
]
for column in metric_columns:
    places[column] = pd.to_numeric(places[column], errors="coerce")

ok_mask = places["youtube_status"].isin(["ok", "dummy"]) & places["naver_status"].eq("ok")


def percentile_score(series, use_log=False):
    values = pd.to_numeric(series, errors="coerce")
    if use_log:
        values = np.log1p(values.clip(lower=0))
    return values.rank(method="average", pct=True) * 100


score_specs = {
    "youtube_channel_points": ("youtube_unique_channel_mentions", 25, False),
    "youtube_growth_points": ("youtube_mention_growth", 15, False),
    "naver_blog_points": ("naver_blog_mentions", 25, True),
    "naver_interest_points": ("naver_interest", 20, True),
    "naver_yoy_points": ("naver_yoy_growth", 15, False),
}

for point_column, (raw_column, max_points, use_log) in score_specs.items():
    places[point_column] = np.nan
    percentile = percentile_score(places.loc[ok_mask, raw_column].fillna(0), use_log=use_log)
    places.loc[ok_mask, point_column] = percentile / 100 * max_points

places["hot_score"] = places[list(score_specs)].sum(axis=1, min_count=5).round(2)
places["trend_points"] = (places["youtube_growth_points"] + places["naver_yoy_points"]).round(2)
places["is_fire"] = places["trend_points"].ge(20).fillna(False)
places["score_mode"] = "REAL_NAVER_REAL_YOUTUBE"

scoreable_count = int(places["hot_score"].notna().sum())
if scoreable_count == 0:
    raise RuntimeError("계산 가능한 지표가 없습니다.")
if scoreable_count > 1 and places.loc[ok_mask, "hot_score"].nunique(dropna=True) <= 1:
    raise RuntimeError("모든 핫플 점수가 동일합니다. 원본 지표와 검색 결과를 확인하세요.")

places["rank"] = np.nan
places.loc[ok_mask, "rank"] = places.loc[ok_mask, "hot_score"].rank(method="first", ascending=False)
places["is_top10"] = places["rank"].le(10).fillna(False)

print("✅ 핫플 점수 계산 완료")
display(
    places.loc[ok_mask, [
        "name", "category", "zone", "youtube_unique_channel_mentions", "youtube_mention_growth",
        "naver_blog_mentions", "naver_interest", "naver_yoy_growth", "hot_score", "rank", "is_fire",
    ]].sort_values("rank").head(10)
)

# ============================================================
# STEP 12. 분석 결과 CSV 저장 (cleaned_data.csv)
# ============================================================
processed_path = DATA_DIR / "cleaned_data.csv"
places.to_csv(processed_path, index=False, encoding="utf-8-sig")
print("✅ 분석 결과 CSV 저장:", processed_path)

# ============================================================
# STEP 13. 네이버 블로그 텍스트 기반 워드클라우드 생성
# ============================================================
KOREAN_FONT_PATH = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"

STOPWORDS = {
    "제주", "제주도", "제주시", "서귀포시", "여행", "블로그", "포스팅", "오늘",
    "정말", "그리고", "너무", "진짜", "정보", "이번", "추천", "방문", "사진",
    "이렇게", "그런데", "저희", "우리", "함께", "이후", "위해", "관련", "대한",
    "하는", "있는", "했던", "합니다", "했어요", "있어요", "같아요", "입니다",
    "에서", "으로", "부터", "까지", "하지만", "그래서", "또한", "그냥", "정도",
}


def clean_blog_text(raw_text):
    text = html.unescape(raw_text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    return text


def extract_korean_tokens(text):
    return re.findall(r"[가-힣]{2,}", text)


token_counter = Counter()
for raw in blog_text_corpus:
    for token in extract_korean_tokens(clean_blog_text(raw)):
        if token not in STOPWORDS:
            token_counter[token] += 1

print("✅ 워드클라우드용 고유 키워드:", len(token_counter), "개")

wordcloud_path = None
if not token_counter:
    print("⚠️ 워드클라우드를 만들 텍스트가 없어 이 단계는 건너뜁니다.")
else:
    wc = WordCloud(
        font_path=KOREAN_FONT_PATH, width=1000, height=600, background_color="white",
        max_words=120, colormap="autumn", prefer_horizontal=0.9,
    ).generate_from_frequencies(token_counter)

    wordcloud_path = OUTPUT_DIR / "jeju_hotplace_wordcloud.png"
    wc.to_file(str(wordcloud_path))

    plt.figure(figsize=(10, 6))
    plt.imshow(wc, interpolation="bilinear")
    plt.axis("off")
    plt.title("제주 핫플 블로그 워드클라우드")
    plt.show()

    print("✅ 워드클라우드 이미지 저장:", wordcloud_path)
    display(pd.DataFrame(token_counter.most_common(20), columns=["키워드", "언급 횟수"]))

# ============================================================
# STEP 14. 제주 핫플 지도 생성
# 마커(관광지/맛집) + TOP10 별 + 상승 불꽃 + 권역 색칠(Choropleth) + 워드클라우드 패널
# ============================================================
jeju_map = folium.Map(
    location=CONFIG["MAP_CENTER"], zoom_start=CONFIG["MAP_ZOOM"], tiles=None, control_scale=True,
)
folium.TileLayer(
    tiles=("https://server.arcgisonline.com/ArcGIS/rest/services/"
           "World_Street_Map/MapServer/tile/{z}/{y}/{x}"),
    attr="Tiles © Esri", name="Esri 실제 지도", overlay=False, control=True,
).add_to(jeju_map)

jeju_map.get_root().header.add_child(Element("""
    <style>
    @keyframes goldenSpin {
        from { transform: rotate(0deg) scale(1); }
        50% { transform: rotate(180deg) scale(1.18); }
        to { transform: rotate(360deg) scale(1); }
    }
    .golden-star {
        width: 34px; height: 34px; line-height: 34px; text-align: center; font-size: 30px;
        color: #FFD700; text-shadow: 0 0 4px #8B6508, 0 0 10px #FFB300;
        animation: goldenSpin 2.1s linear infinite; transform-origin: center; pointer-events: none;
    }
    @keyframes firePulse {
        0% { transform: translateY(0) scale(0.92); filter: brightness(1); }
        50% { transform: translateY(-7px) scale(1.22); filter: brightness(1.35); }
        100% { transform: translateY(0) scale(0.92); filter: brightness(1); }
    }
    .hot-fire {
        width: 36px; height: 42px; line-height: 42px; text-align: center; font-size: 34px;
        animation: firePulse 0.8s ease-in-out infinite; transform-origin: center bottom;
        filter: drop-shadow(0 0 8px #ff5722); pointer-events: none;
    }
    </style>
"""))

tour_layer = folium.FeatureGroup(name="관광지", show=True)
food_layer = folium.FeatureGroup(name="맛집/음식점", show=True)
top_layer = folium.FeatureGroup(name="TOP 10 황금 별", show=True)
fire_layer = folium.FeatureGroup(name="최근 상승 핫플 불꽃", show=True)
tour_cluster = MarkerCluster(name="관광지 묶음", control=False).add_to(tour_layer)
food_cluster = MarkerCluster(name="맛집 묶음", control=False).add_to(food_layer)

for _, place in places.iterrows():
    category = str(place.get("category", ""))
    is_food = category == "맛집/음식점"
    marker_color = "orange" if is_food else "green"
    marker_icon = "cutlery" if is_food else "leaf"
    target_cluster = food_cluster if is_food else tour_cluster

    image_url = str(place.get("image_url", "") or "").strip()
    if image_url.lower() == "nan":
        image_url = ""
    image_html = (
        f'<img src="{html.escape(image_url)}" '
        'style="width:100%;height:130px;object-fit:cover;border-radius:8px;margin-bottom:8px;">'
        if image_url else ""
    )

    score = place.get("hot_score")
    score_text = f"{float(score):.2f}점" if pd.notna(score) else "산정 제외(API 실패)"
    rank = place.get("rank")
    rank_text = f"{int(rank)}위" if pd.notna(rank) else "-"

    popup_html = f"""
    <div style="width:340px;font-family:Arial,'Malgun Gothic',sans-serif;">
      {image_html}
      <h4 style="margin:4px 0;">{html.escape(str(place['name']))}</h4>
      <b>카테고리:</b> {html.escape(category)}<br>
      <b>권역:</b> {html.escape(str(place.get('zone', '미분류')))}<br>
      <b>주소:</b> {html.escape(str(place.get('address', '')))}<br><hr>
      <b style="font-size:16px;">핫플 점수: {score_text}</b><br>
      <b>전체 순위:</b> {rank_text}<br><hr>
      <b>평가 기준 및 획득 점수</b><br>
      ① YouTube 최근 6개월 고유 채널 언급: {int(place.get('youtube_unique_channel_mentions', 0)):,}개
      → <b>{float(place.get('youtube_channel_points', 0)):.2f}/25점</b><br>
      ② YouTube 언급 변화(6개월 전 대비): {float(place.get('youtube_mention_growth', 0)):+.2f}%
      → <b>{float(place.get('youtube_growth_points', 0)):.2f}/15점</b><br>
      ③ 네이버 블로그 언급: {int(place.get('naver_blog_mentions', 0)):,}건
      → <b>{float(place.get('naver_blog_points', 0)):.2f}/25점</b><br>
      ④ 네이버 검색 관심도: {float(place.get('naver_interest', 0)):.2f}
      → <b>{float(place.get('naver_interest_points', 0)):.2f}/20점</b><br>
      ⑤ 전년 동기 검색 변화: {float(place.get('naver_yoy_growth', 0)):+.2f}%
      → <b>{float(place.get('naver_yoy_points', 0)):.2f}/15점</b><br><hr>
      <small>각 지표를 전체 장소 내 백분위로 환산해 배점에 적용했습니다.</small>
    </div>
    """

    folium.Marker(
        location=[place["latitude"], place["longitude"]],
        tooltip=f"{place['name']} | {score_text}",
        popup=folium.Popup(popup_html, max_width=370),
        icon=folium.Icon(color=marker_color, icon=marker_icon, prefix="fa"),
    ).add_to(target_cluster)

    if bool(place.get("is_top10", False)):
        folium.Marker(
            location=[place["latitude"], place["longitude"]],
            tooltip=f"TOP {int(place['rank'])} · {place['name']}",
            icon=folium.DivIcon(icon_size=(34, 34), icon_anchor=(17, 47),
                                 html='<div class="golden-star">★</div>'),
        ).add_to(top_layer)

    if bool(place.get("is_fire", False)):
        folium.Marker(
            location=[place["latitude"], place["longitude"]],
            tooltip=f"🔥 최근 상승 · {place['name']} · 상승점수 {place['trend_points']:.2f}/30",
            icon=folium.DivIcon(icon_size=(36, 42), icon_anchor=(18, 62),
                                 html='<div class="hot-fire">🔥</div>'),
        ).add_to(fire_layer)

tour_layer.add_to(jeju_map)
food_layer.add_to(jeju_map)
top_layer.add_to(jeju_map)
fire_layer.add_to(jeju_map)

# --- 권역별 핫플 밀집도 색칠 (Choropleth) ---
ZONE_GEOJSON_URL = (
    "https://raw.githubusercontent.com/raqoon886/Local_HangJeongDong/master/"
    "hangjeongdong_%EC%A0%9C%EC%A3%BC%ED%8A%B9%EB%B3%84%EC%9E%90%EC%B9%98%EB%8F%84.geojson"
)
zone_geojson_path = DATA_DIR / "jeju_hangjeongdong.geojson"

if not zone_geojson_path.exists():
    resp = requests.get(ZONE_GEOJSON_URL, timeout=CONFIG["TIMEOUT"])
    resp.raise_for_status()
    zone_geojson_path.write_text(resp.text, encoding="utf-8")

with open(zone_geojson_path, encoding="utf-8") as f:
    hangjeongdong_raw = json.load(f)


def dong_to_zone(city, dong_name):
    city = (city or "").strip()
    dong_name = (dong_name or "").strip()
    if dong_name.endswith("읍") or dong_name.endswith("면"):
        return dong_name
    return f"{city} 동지역"


zone_geoms = {}
for feat in hangjeongdong_raw["features"]:
    adm_parts = feat["properties"]["adm_nm"].split()
    city, dong = adm_parts[1], adm_parts[2]
    zone_name = dong_to_zone(city, dong)
    zone_geoms.setdefault(zone_name, []).append(shape(feat["geometry"]))

zone_geo = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {"zone": zone_name}, "geometry": mapping(unary_union(geoms))}
        for zone_name, geoms in zone_geoms.items()
    ],
}

zone_score = places.loc[ok_mask].groupby("zone")["hot_score"].mean().reset_index(name="zone_avg_score")

folium.Choropleth(
    geo_data=zone_geo, name="읍면동 핫플 밀집도", data=zone_score,
    columns=["zone", "zone_avg_score"], key_on="feature.properties.zone",
    fill_color="YlOrRd", fill_opacity=0.65, line_opacity=0.4, line_color="white",
    legend_name="권역별 핫플 밀집도 점수(평균, 0~100)",
    nan_fill_color="lightgray", nan_fill_opacity=0.15,
).add_to(jeju_map)
print("✅ 읍면동 권역 핫플 밀집도 색칠 레이어 추가 완료")

# --- 워드클라우드 플로팅 패널 ---
if wordcloud_path is not None:
    with open(wordcloud_path, "rb") as f:
        wc_base64 = base64.b64encode(f.read()).decode("utf-8")

    wordcloud_panel_html = f"""
    <div id="wordcloud-panel" style="position: fixed; bottom: 20px; left: 20px; z-index: 9999;
        background: white; border: 2px solid #FF7043; border-radius: 10px; padding: 8px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3); max-width: 320px;">
        <div style="font-weight:bold;font-size:13px;margin-bottom:4px;">
            🔥 제주 핫플 블로그 워드클라우드
        </div>
        <img src="data:image/png;base64,{wc_base64}" style="width:100%;border-radius:6px;">
        <div style="text-align:right;">
            <a href="#" onclick="document.getElementById('wordcloud-panel').style.display='none'; return false;"
               style="font-size:11px;color:#888;">닫기</a>
        </div>
    </div>
    """
    jeju_map.get_root().html.add_child(Element(wordcloud_panel_html))
    print("✅ 워드클라우드 패널을 지도에 추가 완료")

folium.LayerControl(collapsed=False).add_to(jeju_map)

map_path = OUTPUT_DIR / "jeju_hotplace_map.html"
jeju_map.save(map_path)

print("\n" + "=" * 70)
print("✅ 전체 작업 완료")
print("원본 CSV:", raw_path)
print("분석 CSV:", processed_path)
print("워드클라우드 PNG:", wordcloud_path)
print("지도 HTML:", map_path)
print("=" * 70)

display(jeju_map)

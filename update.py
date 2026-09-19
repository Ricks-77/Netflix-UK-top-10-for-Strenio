#!/usr/bin/env python3
import calendar
import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

FLIXPATROL_URL = "https://flixpatrol.com/top10/netflix/united-kingdom/"
JINA_FLIXPATROL_URL = "https://r.jina.ai/http://flixpatrol.com/top10/netflix/united-kingdom/"
IMDB_SEARCH_URL = "https://www.imdb.com/search/title/"
JINA_PREFIX = "https://r.jina.ai/http://"
CINEMETA = "https://v3-cinemeta.strem.io"
OUT = Path("docs")
LOCAL_RANKINGS = Path("rankings.json")
TIMEOUT = 20
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36"
IMDB_MIN_RATING = 7.5
MOVIE_MIN_VOTES = 10_000
SERIES_MIN_VOTES = 5_000
MAX_IMDB_RESULTS = 100

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def get_json(url: str):
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def load_previous(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("metas", [])
    except Exception:
        return []

def write_catalog(path: Path, metas):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"metas": metas}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def clean_title(s: str) -> str:
    s = re.sub(r"\[(.*?)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"^\s*\d+[.)]\s*", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" \t|-–—")

def parse_markdown_section(text: str, heading: str):
    m = re.search(rf"(?ims)^###\s+{re.escape(heading)}\s*$", text)
    if not m:
        return []
    tail = text[m.end():]
    next_h = re.search(r"(?m)^###\s+", tail)
    section = tail[: next_h.start()] if next_h else tail
    found = []
    for line in section.splitlines():
        raw = line.strip()
        if not raw:
            continue
        rank_match = re.match(r"^\|?\s*(10|[1-9])(?:\.|\s*\|)", raw)
        if not rank_match:
            continue
        rank = int(rank_match.group(1))
        links = re.findall(r"\[([^\]]+)\]\([^)]*\)", raw)
        candidates = [clean_title(x) for x in links]
        if not candidates:
            parts = [clean_title(x) for x in raw.split("|")]
            candidates = [x for x in parts if x and not re.fullmatch(r"(?:10|[1-9])\.?", x)]
        title = next((c for c in candidates if len(c) > 1 and not re.fullmatch(r"[+\-–—]?\d+|n/?a", c, re.I)), "")
        if title:
            found.append((rank, title))
    by_rank = {}
    for rank, title in found:
        by_rank.setdefault(rank, title)
    return [by_rank[r] for r in sorted(by_rank) if 1 <= r <= 10][:10]

def parse_html_section(html: str, heading: str):
    soup = BeautifulSoup(html, "html.parser")
    header = None
    for tag in soup.find_all(["h2", "h3", "h4"]):
        if tag.get_text(" ", strip=True).casefold() == heading.casefold():
            header = tag
            break
    if not header:
        return []
    container = header.find_next(lambda t: t.name in ("div", "table") and t.find("tbody"))
    if not container:
        return []
    titles = []
    for row in container.find("tbody").find_all("tr")[:10]:
        title = ""
        for a in row.find_all("a", href=True):
            txt = a.get_text(" ", strip=True)
            href = a.get("href", "")
            if txt and ("/title/" in href or "/movie/" in href or "/show/" in href):
                title = txt
                break
        if title:
            titles.append(title)
    return titles[:10]

def fetch_netflix_rankings():
    try:
        data = json.loads(LOCAL_RANKINGS.read_text(encoding="utf-8"))
        movies = data.get("movies", [])
        series = data.get("series", [])
        if len(movies) == 10 and len(series) == 10:
            return movies, series, "verified-local"
    except Exception:
        pass

    headers = {"User-Agent": UA, "Accept": "text/plain,text/html,*/*"}
    errors = []
    try:
        r = requests.get(JINA_FLIXPATROL_URL, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        movies = parse_markdown_section(r.text, "TOP 10 Movies")
        series = parse_markdown_section(r.text, "TOP 10 TV Shows")
        if len(movies) >= 8 and len(series) >= 8:
            return movies[:10], series[:10], "jina"
        errors.append(f"Jina returned movies={len(movies)}, series={len(series)}")
    except Exception as e:
        errors.append(f"Jina: {e}")

    try:
        r = requests.get(FLIXPATROL_URL, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        movies = parse_html_section(r.text, "TOP 10 Movies")
        series = parse_html_section(r.text, "TOP 10 TV Shows")
        if len(movies) >= 8 and len(series) >= 8:
            return movies[:10], series[:10], "direct"
        errors.append(f"Direct returned movies={len(movies)}, series={len(series)}")
    except Exception as e:
        errors.append(f"Direct: {e}")
    raise RuntimeError("Could not obtain a trustworthy Netflix UK Top 10. " + " | ".join(errors))

def resolve_cinemeta_title(title: str, media_type: str):
    if media_type == "series" and norm(title) == "sealook":
        return {
            "id": "tmdb:219266",
            "type": "series",
            "name": "Sealook",
            "poster": "https://artworks.thetvdb.com/banners/v4/series/438604/posters/64e42cc499a18.jpg",
            "posterShape": "poster",
            "releaseInfo": "2022–2023",
        }
    q = quote(title, safe="")
    try:
        candidates = get_json(f"{CINEMETA}/catalog/{media_type}/top/search={q}.json").get("metas", [])
    except Exception:
        return None
    if not candidates:
        return None
    target = norm(title)
    exact = [m for m in candidates if norm(m.get("name")) == target]
    close = [m for m in candidates if target in norm(m.get("name")) or norm(m.get("name")) in target]
    chosen = (exact or close or candidates)[0]
    imdb_id = chosen.get("id", "")
    poster = chosen.get("poster")
    if not imdb_id.startswith("tt") or not poster:
        return None
    item = {"id": imdb_id, "type": media_type, "name": chosen.get("name") or title, "poster": poster, "posterShape": "poster"}
    for k in ("background", "description", "releaseInfo", "imdbRating"):
        if chosen.get(k):
            item[k] = chosen[k]
    return item

def build_netflix_catalog(titles, media_type: str, path: Path):
    previous = load_previous(path)
    prev_by_name = {norm(x.get("name")): x for x in previous if x.get("name")}
    def one(title):
        return title, resolve_cinemeta_title(title, media_type) or prev_by_name.get(norm(title))
    with ThreadPoolExecutor(max_workers=6) as pool:
        resolved = list(pool.map(one, titles))
    metas = [item for _, item in resolved if item]
    unresolved = [title for title, item in resolved if not item]
    if len(metas) < 8:
        raise RuntimeError(f"Only resolved {len(metas)}/{len(titles)} {media_type} Netflix titles; refusing overwrite: {unresolved}")
    write_catalog(path, metas)
    return metas, unresolved

def one_year_ago(d):
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return d.replace(year=d.year - 1, day=calendar.monthrange(d.year - 1, d.month)[1])

def parse_votes(value: str):
    if not value:
        return None
    s = value.upper().replace(",", "").strip()
    m = re.match(r"([0-9]+(?:\.[0-9]+)?)\s*([KMB]?)", s)
    if not m:
        return None
    n = float(m.group(1))
    mult = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[m.group(2)]
    return int(n * mult)

def parse_imdb_html(html: str):
    soup = BeautifulSoup(html, "html.parser")
    found = []
    seen = set()
    for a in soup.find_all("a", href=re.compile(r"/title/tt\d+")):
        href = a.get("href", "")
        m = re.search(r"/title/(tt\d+)", href)
        if not m:
            continue
        imdb_id = m.group(1)
        if imdb_id in seen:
            continue
        title = clean_title(a.get_text(" ", strip=True))
        if not title or len(title) < 2:
            continue
        container = a.find_parent("li") or a.find_parent("div", class_=re.compile(r"(ipc-metadata-list-summary-item|dli-parent)"))
        text = container.get_text(" ", strip=True) if container else ""
        rating = None
        votes = None
        rm = re.search(r"(?:IMDb\s*rating\s*)?([0-9]\.[0-9])\s*\(([0-9.,]+\s*[KMB]?)\)", text, re.I)
        if rm:
            rating = float(rm.group(1))
            votes = parse_votes(rm.group(2))
        else:
            rm = re.search(r"([0-9]\.[0-9])\s*/\s*10", text)
            if rm:
                rating = float(rm.group(1))
        found.append({"id": imdb_id, "name": title, "rating": rating, "votes": votes})
        seen.add(imdb_id)
    return found

def parse_imdb_markdown(text: str):
    found = []
    seen = set()
    matches = list(re.finditer(r"\[([^\]]+)\]\((?:https?://)?(?:www\.)?imdb\.com/title/(tt\d+)[^)]*\)", text, re.I))
    for i, m in enumerate(matches):
        imdb_id = m.group(2)
        if imdb_id in seen:
            continue
        title = clean_title(m.group(1))
        if not title or title.lower() in {"image", "poster"}:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else min(len(text), m.end() + 700)
        chunk = text[m.end():end]
        rating = None
        votes = None
        rm = re.search(r"([0-9]\.[0-9])\s*(?:/10)?[^\n]{0,80}?\(([0-9.,]+\s*[KMB]?)\)", chunk, re.I)
        if rm:
            rating = float(rm.group(1))
            votes = parse_votes(rm.group(2))
        found.append({"id": imdb_id, "name": title, "rating": rating, "votes": votes})
        seen.add(imdb_id)
    return found

def imdb_search(media_type: str):
    today = datetime.now(ZoneInfo("Europe/London")).date()
    start = one_year_ago(today)
    min_votes = MOVIE_MIN_VOTES if media_type == "movie" else SERIES_MIN_VOTES
    title_type = "feature" if media_type == "movie" else "tv_series,tv_miniseries"
    params = {
        "title_type": title_type,
        "release_date": f"{start.isoformat()},{today.isoformat()}",
        "user_rating": f"{IMDB_MIN_RATING},",
        "num_votes": f"{min_votes},",
        "sort": "user_rating,desc",
        "count": str(MAX_IMDB_RESULTS),
    }
    query = urlencode(params, safe=",")
    url = f"{IMDB_SEARCH_URL}?{query}"
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.9",
    }
    errors = []
    candidates = []
    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        candidates = parse_imdb_html(r.text)
        if len(candidates) < 5:
            errors.append(f"IMDb direct parsed only {len(candidates)}")
            candidates = []
    except Exception as e:
        errors.append(f"IMDb direct: {e}")

    if not candidates:
        try:
            jina = JINA_PREFIX + url.replace("https://", "")
            r = requests.get(jina, headers={"User-Agent": UA, "Accept": "text/plain"}, timeout=TIMEOUT)
            r.raise_for_status()
            candidates = parse_imdb_markdown(r.text)
            if len(candidates) < 5:
                errors.append(f"IMDb via Jina parsed only {len(candidates)}")
                candidates = []
        except Exception as e:
            errors.append(f"IMDb via Jina: {e}")

    if not candidates:
        raise RuntimeError("Could not obtain trustworthy IMDb rolling-12-month results. " + " | ".join(errors))

    # The IMDb query already filters rating and vote count and sorts descending.
    # Re-apply parsed values where available as a guard against malformed pages.
    clean = []
    for c in candidates:
        if c["rating"] is not None and c["rating"] < IMDB_MIN_RATING:
            continue
        if c["votes"] is not None and c["votes"] < min_votes:
            continue
        clean.append(c)
    return clean[:MAX_IMDB_RESULTS], start, today, url

def resolve_cinemeta_id(candidate, media_type: str):
    imdb_id = candidate["id"]
    try:
        meta = get_json(f"{CINEMETA}/meta/{media_type}/{imdb_id}.json").get("meta", {})
    except Exception:
        return None
    poster = meta.get("poster")
    if not poster:
        return None
    item = {
        "id": imdb_id,
        "type": media_type,
        "name": meta.get("name") or candidate["name"],
        "poster": poster,
        "posterShape": "poster",
    }
    for k in ("background", "description", "releaseInfo"):
        if meta.get(k):
            item[k] = meta[k]
    rating = candidate.get("rating")
    if rating is not None:
        item["imdbRating"] = str(rating)
    elif meta.get("imdbRating"):
        item["imdbRating"] = meta["imdbRating"]
    return item

def build_imdb_catalog(media_type: str, path: Path):
    candidates, start, today, source_url = imdb_search(media_type)
    previous = {x.get("id"): x for x in load_previous(path) if x.get("id")}
    with ThreadPoolExecutor(max_workers=12) as pool:
        metas = list(pool.map(lambda c: resolve_cinemeta_id(c, media_type), candidates))
    final = []
    for c, item in zip(candidates, metas):
        if not item:
            item = previous.get(c["id"])
        if item:
            if c.get("rating") is not None:
                item["imdbRating"] = str(c["rating"])
            final.append(item)
    if len(final) < 5:
        raise RuntimeError(f"Only resolved {len(final)}/{len(candidates)} IMDb {media_type} titles; refusing overwrite")
    write_catalog(path, final)
    return final, start, today, source_url

def main():
    status = {
        "updated_at": datetime.now(ZoneInfo("Europe/London")).isoformat(),
        "netflix": {},
        "imdb_last_12_months": {
            "minimum_rating": IMDB_MIN_RATING,
            "movie_min_votes": MOVIE_MIN_VOTES,
            "series_min_votes": SERIES_MIN_VOTES,
        },
    }

    movies, series, source = fetch_netflix_rankings()
    m, mu = build_netflix_catalog(movies, "movie", OUT / "catalog/movie/netflix-uk-top10-movies.json")
    s, su = build_netflix_catalog(series, "series", OUT / "catalog/series/netflix-uk-top10-series.json")
    status["netflix"] = {"source": source, "movies": len(m), "series": len(s), "unresolved_movies": mu, "unresolved_series": su}

    im, start, today, movie_url = build_imdb_catalog("movie", OUT / "catalog/movie/imdb-last12m-movies.json")
    it, _, _, series_url = build_imdb_catalog("series", OUT / "catalog/series/imdb-last12m-series.json")
    status["imdb_last_12_months"].update({
        "from": start.isoformat(),
        "to": today.isoformat(),
        "movies": len(im),
        "series": len(it),
        "movie_source": movie_url,
        "series_source": series_url,
    })

    (OUT / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, indent=2))

if __name__ == "__main__":
    main()

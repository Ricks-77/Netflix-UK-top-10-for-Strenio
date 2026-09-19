#!/usr/bin/env python3
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

FLIXPATROL_URL = "https://flixpatrol.com/top10/netflix/united-kingdom/"
JINA_URL = "https://r.jina.ai/http://flixpatrol.com/top10/netflix/united-kingdom/"
CINEMETA = "https://v3-cinemeta.strem.io"
OUT = Path("docs")
LOCAL_RANKINGS = Path("rankings.json")
TIMEOUT = 10
UA = "Mozilla/5.0 (compatible; NetflixUKTop10Stremio/1.0; +https://github.com/)"

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def clean_title(s: str) -> str:
    s = re.sub(r"\[(.*?)\]\([^)]*\)", r"\1", s)
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
            parts = [x for x in parts if x]
            for p in parts:
                if re.fullmatch(r"(?:10|[1-9])\.?", p):
                    continue
                if re.fullmatch(r"[+\-–—]?\d+|n/?a", p, re.I):
                    continue
                if re.fullmatch(r"\d+\s*[dhm]", p, re.I):
                    continue
                if p.startswith(("+", "-")) and p[1:].isdigit():
                    continue
                candidates.append(p)

        title = ""
        for c in candidates:
            if len(c) < 2:
                continue
            if re.fullmatch(r"\d+\s*[dhm]", c, re.I):
                continue
            if re.fullmatch(r"[+\-–—]?\d+|n/?a", c, re.I):
                continue
            title = c
            break

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
    rows = container.find("tbody").find_all("tr")
    titles = []
    for row in rows[:10]:
        anchors = row.find_all("a", href=True)
        title = ""
        for a in anchors:
            txt = a.get_text(" ", strip=True)
            href = a.get("href", "")
            if txt and ("/title/" in href or "/movie/" in href or "/show/" in href):
                title = txt
                break
        if not title:
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            cells = [c for c in cells if c and not re.fullmatch(r"[+\-–—]?\d+|n/?a", c, re.I)]
            if cells:
                title = max(cells, key=len)
        if title:
            titles.append(title)
    return titles[:10]

def fetch_rankings():
    # Prefer the verified rankings file. It is updated independently from the
    # public Netflix UK chart and avoids Cloudflare blocking GitHub runners.
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
        r = requests.get(JINA_URL, headers=headers, timeout=TIMEOUT)
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
def build_catalog(titles, media_type: str, path: Path):
    from concurrent.futures import ThreadPoolExecutor

    previous = load_previous(path)
    prev_by_name = {norm(x.get("name")): x for x in previous if x.get("name")}

    def one(title):
        item = resolve_cinemeta(title, media_type)
        if not item:
            item = prev_by_name.get(norm(title))
        return title, item

    with ThreadPoolExecutor(max_workers=5) as pool:
        resolved = list(pool.map(one, titles))

    metas = []
    unresolved = []
    for title, item in resolved:
        if item:
            metas.append(item)
        else:
            unresolved.append(title)

    if len(metas) < 8:
        raise RuntimeError(
            f"Only resolved {len(metas)}/{len(titles)} {media_type} titles; refusing to overwrite previous catalog. "
            f"Unresolved: {unresolved}"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"metas": metas}, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    return metas, unresolved
def resolve_cinemeta(title: str, media_type: str):
    # Sealook does not currently have a reliable IMDb mapping in Cinemeta.
    # Use its verified TMDB/TVDB identity so AIOMetadata can resolve it correctly.
    if media_type == "series" and norm(title) == "sealook":
        return {
            "id": "tmdb:219266",
            "type": "series",
            "name": "Sealook",
            "poster": "https://artworks.thetvdb.com/banners/v4/series/438604/posters/64e42cc499a18.jpg",
            "posterShape": "poster",
            "releaseInfo": "2022–2023"
        }
    q = quote(title, safe="")
    url = f"{CINEMETA}/catalog/{media_type}/top/search={q}.json"
    try:
        data = get_json(url)
        candidates = data.get("metas", [])
    except Exception:
        return None

    if not candidates:
        return None

    target = norm(title)
    exact = [m for m in candidates if norm(m.get("name")) == target]
    if exact:
        chosen = exact[0]
    else:
        close = [m for m in candidates if target in norm(m.get("name")) or norm(m.get("name")) in target]
        chosen = close[0] if close else candidates[0]

    imdb_id = chosen.get("id", "")
    poster = chosen.get("poster")
    if not imdb_id.startswith("tt") or not poster:
        return None

    item = {
        "id": imdb_id,
        "type": media_type,
        "name": chosen.get("name") or title,
        "poster": poster,
        "posterShape": "poster",
    }
    for k in ("background", "description", "releaseInfo", "imdbRating"):
        v = chosen.get(k)
        if v:
            item[k] = v
    return item


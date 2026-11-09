#!/usr/bin/env python3
"""
webb_sync.py — Downloads space images and prepares them for the TV.

Sources:
  webb    ESA/Webb: Picture of the Month + categories (nebulae, galaxies…).
  hubble  ESA/Hubble: Picture of the Week + categories.
          Webb and Hubble get a short caption at the bottom:
          title · date · constellation · distance.
  apod    NASA Astronomy Picture of the Day (api.nasa.gov).
          The explanation goes in a SIDE PANEL, never on top of the image.
          With WEBB_TRANSLATE=claude it is translated and summarized so it
          can be read from the couch.

  art     Public-domain paintings from art/paintings.txt ('Title | Artist'),
          resolved on Wikidata (pinned in art/paintings.lock.json) and
          downloaded from Wikimedia Commons. Caption below the painting:
          title · artist · year · museum, in WEBB_LANG.

Daily pools: the TV folder doesn't hold everything, but a selection rebuilt on
every run. "space": the latest APOD repeated several times (so it shows up
often), the APODs from previous days, and a random sample of Webb + Hubble that
changes every day. "art": the painting of the day repeated (no repeats until
the whole list has been shown) plus a random sample. Both pools live in the
cache; SCHEDULE decides which one the TV folder shows at each hour, and
--pool-only switches it from an hourly cron without touching the network.

Every image is fit into the TV resolution WITHOUT distortion, and the empty
borders are filled with a blurred copy of the same image. Originals are kept in
a cache outside the shared folder: changing language/resolution re-renders
without downloading again.

Configuration (environment variables or flags, see --help):
  WEBB_SOURCES   webb,hubble,apod                       (webb,hubble,apod)
  WEBB_DEST      folder the TV reads                    (/srv/webb)
  WEBB_CACHE     originals + state                      (~/.cache/webb)
  WEBB_LANG      en | es                                (en)
  WEBB_SIZE      TV resolution                          (1920x1080)
  WEBB_CAPTION   1 = with text, 0 = image only          (1)
  WEBB_TRANSLATE none | claude | argos                  (none)
  WEBB_FONT      path to a .ttf                         (DejaVu Sans)
  WEBB_TEXT_WIDTH share of the width for text, centered  (0.60)
  APOD_API_KEY   api.nasa.gov key                       (DEMO_KEY)
  APOD_DAYS      how many days back to fetch            (14)
  APOD_TEXT      summary | full | none                  (summary)
  POOL_APOD_WEIGHT  copies of the latest APOD           (8)
  POOL_APOD_RECENT  previous APODs, once each           (6)
  POOL_RANDOM       random Webb + Hubble images         (40)
  SCHEDULE       space | art | alternate | space:8-19,art:20-7   (space)
  ART_LIST       the painting list                      (art/paintings.txt)
  POOL_ART_WEIGHT   copies of the painting of the day   (6)
  POOL_ART_RANDOM   random paintings                    (30)
  ART_FETCH_WIDTH   download width from Commons         (1920)
  ANTHROPIC_API_KEY / CLAUDE_MODEL   for WEBB_TRANSLATE=claude
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import html
import io
import json
import os
import random
import shutil
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:
    sys.exit("Pillow is missing: sudo apt install -y python3-pil")

Image.MAX_IMAGE_PIXELS = None  # some Webb originals are huge

UA = "tv-webb-screensaver (+https://github.com/wallystart/tv-webb-screensaver)"
RENDER_VERSION = "v6"

# ================================================================ ESA/Webb

# Webb and Hubble run on the same platform (same fields and same CDN URLs)
ESO_SITES = {
    "webb": {
        "domain": "esawebb.org", "credit": "ESA/Webb", "prefix": "",
        "pages": [
            "/images/potm/",
            "/images/archive/category/nebulae/",
            "/images/archive/category/galaxies/",
            "/images/archive/category/stars/",
            "/images/archive/category/quasarsblackholes/",
            "/images/archive/category/firstimages/",
        ],
    },
    "hubble": {
        "domain": "esahubble.org", "credit": "ESA/Hubble", "prefix": "hubble-",
        "pages": [
            "/images/potw/",
            "/images/archive/category/nebulae/",
            "/images/archive/category/galaxies/",
            "/images/archive/category/starclusters/",
            "/images/archive/category/stars/",
        ],
    },
}
SITE = "https://esawebb.org"  # for --debug
CDN = "https://cdn.esawebb.org/archives/images"
SKIP_IDS = {"potm", "potw", "archive", "search", "page", "viewall", "zoomable"}

# Titles that give away versions with text/graphics on top
TEXT_HINTS = re.compile(
    r"\b(annotated|compass|labell?ed|scale ?bar|spectrum|spectra|graph|chart|"
    r"infographic|diagram|comparison|illustration|artist|velocity|composition|"
    r"IFU|map of|light curve)", re.I)

LABELS = [
    "Name", "Type", "Distance", "Constellation", "Category", "Id",
    "Release date", "Related releases", "Related announcements",
    "Related science announcements", "Size", "Field of View", "Orientation",
    "Credit",
]
STOPS = LABELS + [
    "About the Object", "About the Image", "Image Formats", "Zoomable",
    "Classic Wallpapers", "Desktop Wallpapers", "Mobile Wallpapers",
    "Usage of", "Are you a journalist",
]
STOP_RE = "|".join(re.escape(s) for s in sorted(STOPS, key=len, reverse=True))

# ================================================================ APOD

APOD_API = "https://api.nasa.gov/planetary/apod"
APOD_SITE = "https://science.nasa.gov/apod/"  # fallback when the API fails
APOD_PARSER = 2  # bump to re-read APOD entries saved by an older parser
APOD_MIN_WIDTH = 800  # very small ones look bad on a TV

# ================================================================ art

ART_LIST = Path(__file__).resolve().parent.parent / "art" / "paintings.txt"
WD_API = "https://www.wikidata.org/w/api.php"
WD_SPARQL = "https://query.wikidata.org/sparql"
COMMONS_FILE = "https://commons.wikimedia.org/wiki/Special:FilePath/"
# An item counts as a painting if its "instance of" (P31) is one of these or a
# subclass. Prints are allowed so that Hokusai's woodblock prints qualify.
ART_CLASSES = {"Q3305213": "painting", "Q11060274": "print"}
# When several items fit a line, take the best known one only if it has at
# least this many times the Wikipedia articles (sitelinks) of the next one.
ART_DOMINANCE = 5
ART_LANGS = "en|es|fr|de|it|nl|pt|ja|ru|nb"  # creator names compared against the list

# ================================================================ text

MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"]
MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
UNITS_ES = {
    "light years": "años luz", "light year": "año luz",
    "million": "millones", "billion": "mil millones",
}


# ---------------------------------------------------------------- network

def get(url: str, binary: bool = False, timeout: int = 60, tries: int = 3):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503) or attempt == tries - 1:
                raise
            try:  # rate limited: wait as asked (capped), then retry
                wait = min(int(e.headers.get("Retry-After", "")), 60)
            except ValueError:
                wait = 5 * (attempt + 1)
            time.sleep(wait)
    return data if binary else data.decode("utf-8", "replace")


# ---------------------------------------------------------------- Webb / Hubble

def eso_list_ids(site: dict) -> list[str]:
    ids: set[str] = set()
    dom = site["domain"]
    for p in site["pages"]:
        try:
            page = get(f"https://{dom}{p}")
        except Exception as e:  # noqa: BLE001
            print(f"! could not read {dom}{p}: {e}", file=sys.stderr)
            continue
        for m in re.finditer(
                rf'href="(?:https://(?:www\.)?{re.escape(dom)})?/images/([A-Za-z0-9_-]+)/"', page):
            if m.group(1) not in SKIP_IDS:
                ids.add(m.group(1))
        time.sleep(0.5)
    return sorted(ids)


def page_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def webb_parse_meta(raw: str) -> dict:
    meta: dict = {}
    m = (re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', raw)
         or re.search(r'<meta[^>]+content="([^"]*)"[^>]+property="og:title"', raw)
         or re.search(r"<title>(.*?)</title>", raw, re.S))
    if m:
        meta["title"] = re.sub(r"\s*\|\s*ESA/(?:Webb|Hubble)\s*$", "",
                               html.unescape(m.group(1)).strip())

    text = page_text(raw)
    fields: dict[str, list[str]] = {}
    for m in re.finditer(
            rf"\b({'|'.join(map(re.escape, LABELS))}):\s*(.*?)\s*(?=(?:{STOP_RE})\b|$)", text):
        val = m.group(2).strip()[:160]
        if val:
            fields.setdefault(m.group(1), []).append(val)

    meta["types"] = fields.get("Type", [])
    for key in ("Distance", "Constellation", "Name"):
        if fields.get(key):
            meta[key.lower()] = fields[key][0]

    d = re.search(r"Release date:\s*(\d{1,2}) (\w+) (\d{4})", text)
    if d and d.group(2) in MONTHS_EN:
        meta["date"] = f"{d.group(3)}-{MONTHS_EN.index(d.group(2)) + 1:02d}-{int(d.group(1)):02d}"
    meta["description"] = eso_description(raw)
    return meta


def eso_description(raw: str) -> str:
    """The press text under the image: the paragraphs after the <h1>, up to the
    'About the Image' box. Source for the short caption description."""
    start = raw.find("<h1")
    if start < 0:
        return ""
    end = min([i for i in (raw.find(s, start) for s in ("About the Image", "Image Formats")) if i > 0]
              or [len(raw)])
    body = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw[start:end])
    paras = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", p))).strip()
             for p in re.findall(r"(?is)<p\b[^>]*>(.*?)</p>", body)]
    return " ".join(p for p in paras if len(p) > 40)[:3000]


def webb_reject(meta: dict) -> str | None:
    """None = keep it. Otherwise, the reason to discard it."""
    title = meta.get("title", "")
    if TEXT_HINTS.search(title):
        return f"title suggests text/graphics: {title!r}"
    types = [t.lower() for t in meta.get("types", [])]
    if types and not any("observation" in t for t in types):
        return f"not an Observation: {meta['types']}"
    return None


def eso_items(name: str) -> list[tuple[str, callable]]:
    """List of (key, function returning the item) for Webb or Hubble."""
    site = ESO_SITES[name]
    dom, cdn = site["domain"], f"https://cdn.{site['domain']}/archives/images"

    def make(img_id):
        def fetch():
            meta = webb_parse_meta(get(f"https://{dom}/images/{img_id}/"))
            time.sleep(0.5)
            meta.update(source=name, credit=site["credit"],
                        file=f"{site['prefix']}{img_id}.jpg",
                        urls=[f"{cdn}/wallpaper_uhd/{img_id}.jpg", f"{cdn}/large/{img_id}.jpg"],
                        rejected=webb_reject(meta))
            return meta
        return fetch
    return [(f"{name}:{i}", make(i)) for i in eso_list_ids(site)]


# ---------------------------------------------------------------- APOD

def apod_items(api_key: str, days: int) -> list[tuple[str, callable]] | None:
    """APOD via the official API. Returns None if the API is unavailable."""
    end = dt.date.today()
    start = end - dt.timedelta(days=max(days - 1, 0))
    url = f"{APOD_API}?" + urllib.parse.urlencode(
        {"api_key": api_key, "start_date": start.isoformat(), "end_date": end.isoformat()})
    try:
        entries = json.loads(get(url))
    except urllib.error.HTTPError as e:
        if e.code != 400:  # 400 = today's APOD isn't out yet (US time zone)
            print(f"! APOD API: {e} — falling back to science.nasa.gov", file=sys.stderr)
            return None
        try:
            url = url.replace(end.isoformat(), (end - dt.timedelta(days=1)).isoformat())
            entries = json.loads(get(url))
        except Exception as e2:  # noqa: BLE001
            print(f"! APOD API: {e2} — falling back to science.nasa.gov", file=sys.stderr)
            return None
    except Exception as e:  # noqa: BLE001
        print(f"! APOD API: {e} — falling back to science.nasa.gov", file=sys.stderr)
        return None

    out = []
    for e in entries if isinstance(entries, list) else [entries]:
        def fetch(e=e):
            meta = {
                "source": "apod",
                "file": f"apod-{e['date']}.jpg",
                "title": e.get("title", "").strip(),
                "date": e["date"],
                "explanation": e.get("explanation", "").strip(),
                "credit": re.sub(r"\s+", " ", (e.get("copyright") or "NASA")).strip(),
                "urls": [u for u in (e.get("hdurl"), e.get("url")) if u],
                "rejected": None,
                "parser": "api",
            }
            if e.get("media_type") != "image":
                meta["rejected"] = f"not an image ({e.get('media_type')})"
            return meta
        out.append((f"apod:{e['date']}", fetch))
    return out


def apod_scrape_items(days: int, known: set[str]) -> list[tuple[str, callable]]:
    """Fallback: reads science.nasa.gov/apod/ and walks back through the
    'Yesterday' links. Stops at the first known date or outside the window."""
    oldest = (dt.date.today() - dt.timedelta(days=max(days, 1))).isoformat()
    metas: list[dict] = []
    titles: dict[str, str] = {}  # date -> title, from NASA's own link texts
    url = APOD_SITE
    for _ in range(max(days, 1) + 1):
        try:
            raw = get(url)
        except Exception as e:  # noqa: BLE001
            print(f"! APOD page {url}: {e}", file=sys.stderr)
            break
        titles.update(apod_link_titles(raw))
        meta = apod_parse_page(raw, url)
        date = meta.get("date")
        if not date:
            print(f"! APOD page {url}: could not find the date", file=sys.stderr)
            break
        if date < oldest or f"apod:{date}" in known:
            break
        metas.append(meta)
        prev = apod_prev_link(raw, date, oldest)
        if not prev:
            break
        url = prev
        time.sleep(0.5)

    for m in metas:  # the link texts beat whatever heading the page parser found
        if titles.get(m["date"]):
            m["title"] = titles[m["date"]]
    return [(f"apod:{m['date']}", lambda m=m: m) for m in metas]


APOD_LINK_TEXT = re.compile(
    r"APOD:\s*(\d{4})\s+([A-Z][a-z]+)\s+(\d{1,2})\s*[–—-]\s*([^<\n]+?)\s*(?:<|$)")


def apod_link_titles(raw: str) -> dict[str, str]:
    """'Yesterday's Image APOD: 2026 November 7 – De Gerlache-Kocher Massif' → title by date."""
    out = {}
    for a in re.findall(r"(?is)<a\b[^>]*>(.*?)</a>", raw):
        t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", a))).strip()
        m = APOD_LINK_TEXT.search(t + "<")
        if m and m.group(2) in MONTHS_EN:
            d = f"{m.group(1)}-{MONTHS_EN.index(m.group(2)) + 1:02d}-{int(m.group(3)):02d}"
            out[d] = m.group(4).strip()
    return out


APOD_LINK = re.compile(
    r'href="(https://science\.nasa\.gov/image-article/apod-(\d{4})-([a-z]+)-(\d{1,2})-[^"]+?)"')


def apod_prev_link(raw: str, iso: str, oldest: str = "") -> str | None:
    """The link to the previous day. Prefers links whose text says 'Yesterday';
    never goes outside the requested window (pages also link to old APODs)."""
    cands = []
    for m in re.finditer(r'(?is)<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', raw):
        lm = APOD_LINK.search(f'href="{m.group(1)}"')
        if not lm or lm.group(3).capitalize() not in MONTHS_EN:
            continue
        d = f"{lm.group(2)}-{MONTHS_EN.index(lm.group(3).capitalize()) + 1:02d}-{int(lm.group(4)):02d}"
        if oldest <= d < iso:
            cands.append((d, "yesterday" in m.group(2).lower(), lm.group(1)))
    if not cands:
        return None
    yest = [c for c in cands if c[1]]
    return max(yest or cands)[2]


def apod_parse_page(raw: str, url: str) -> dict:
    text = page_text(raw)
    labels = r"(?:Date|Credit & Copyright|Credit|Image Credit|Explanation|Tomorrow's picture|" \
             r"Authors & editors|A service of|Yesterday's Image|Tomorrow's Image)"

    def field(label: str) -> str:
        m = re.search(rf"{label}:\s*(.*?)\s*(?={labels}:|Yesterday's Image|Tomorrow's Image|$)", text)
        return m.group(1).strip() if m else ""

    meta: dict = {"source": "apod", "rejected": None, "parser": APOD_PARSER}

    d = re.search(r"Date:\s*([A-Z][a-z]+) (\d{1,2}), (\d{4})", text)
    if d and d.group(1) in MONTHS_EN:
        meta["date"] = f"{d.group(3)}-{MONTHS_EN.index(d.group(1)) + 1:02d}-{int(d.group(2)):02d}"
    else:
        m = APOD_LINK.search(f'href="{url}"')
        if m and m.group(3).capitalize() in MONTHS_EN:
            meta["date"] = (f"{m.group(2)}-{MONTHS_EN.index(m.group(3).capitalize()) + 1:02d}-"
                            f"{int(m.group(4)):02d}")

    # Title: the heading closest before the "Date:" label (the menus have headings too)
    skip = {"astronomy picture of the day", "apod", "explanation", "related terms",
            "discover more topics from nasa", "suggested searches"}
    date_pos = raw.find("Date:")
    heads = [(m.start(), re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", m.group(1)))).strip())
             for m in re.finditer(r"(?is)<h[1-3][^>]*>(.*?)</h[1-3]>", raw)]
    heads = [h for h in heads if h[1] and h[1].lower() not in skip]
    before = [h for h in heads if date_pos < 0 or h[0] < date_pos]
    if before or heads:
        meta["title"] = (before or heads)[-1 if before else 0][1]

    meta["credit"] = field(r"(?:Credit & Copyright|Image Credit|Credit)")[:120] or "NASA"
    meta["explanation"] = field("Explanation")[:2500]

    # Only the page body: the <meta>/og:image tags on science.nasa.gov/apod/ are stale
    body = re.sub(r"(?is)<head\b.*?</head>|<meta\b[^>]*>", " ", raw)
    imgs = re.findall(
        r"https://assets\.science\.nasa\.gov/(?:dynamicimage/assets|content/dam)"
        r"(/science/cds/apod/[^\"'?\s]+?\.(?:jpe?g|png))(?![^\"'\s]*/jcr:content)", body, re.I)
    imgs = [i for i in imgs if "/renditions/" not in i and "/jcr:content" not in i]
    if imgs:
        meta["urls"] = [f"https://assets.science.nasa.gov/content/dam{imgs[0]}"]
    else:
        meta["urls"] = []
        meta["rejected"] = "not an image (probably a video)"
    if meta.get("date"):
        meta["file"] = f"apod-{meta['date']}.jpg"
    return meta


# ---------------------------------------------------------------- art (Wikidata + Commons)

def norm(s: str) -> str:
    """Lowercase, no accents, words only: for comparing names and titles."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def wd_api(**params) -> dict:
    params["format"] = "json"
    data = json.loads(get(f"{WD_API}?{urllib.parse.urlencode(params)}"))
    time.sleep(0.2)  # be gentle with the Wikidata API
    return data


def wd_entities(ids, props: str, languages: str | None = None) -> dict:
    """wbgetentities in batches of 50. With a single language, labels fall back
    to other languages (e.g. to English) when there is none in that one."""
    out: dict = {}
    ids = list(dict.fromkeys(ids))
    for i in range(0, len(ids), 50):
        p = {"action": "wbgetentities", "ids": "|".join(ids[i:i + 50]), "props": props}
        if languages:
            p["languages"] = languages
            if "|" not in languages:
                p["languagefallback"] = 1
        out.update(wd_api(**p).get("entities", {}))
    return out


def wd_statements(ent: dict, prop: str, current: bool = False) -> list[dict]:
    """Statements of a property, preferred rank first, never deprecated ones.
    current=True also drops the ones with an end time (e.g. a former collection)."""
    st = [c for c in ent.get("claims", {}).get(prop, [])
          if c.get("rank") != "deprecated" and "datavalue" in c.get("mainsnak", {})
          and not (current and "P582" in c.get("qualifiers", {}))]
    return sorted(st, key=lambda c: c.get("rank") != "preferred")


def wd_values(ent: dict, prop: str, current: bool = False) -> list:
    return [c["mainsnak"]["datavalue"]["value"] for c in wd_statements(ent, prop, current)]


def wd_ids(ent: dict, prop: str, current: bool = False) -> list[str]:
    return [v["id"] for v in wd_values(ent, prop, current) if isinstance(v, dict) and "id" in v]


def wd_label(ent: dict | None, lang: str) -> str:
    labels = (ent or {}).get("labels", {})
    for code in (lang, "en", "mul"):
        if labels.get(code, {}).get("value"):
            return labels[code]["value"]
    return ""


def wd_year(ent: dict) -> str:
    """Year of creation (P571). 'c. 1665' when it's approximate."""
    for st in wd_statements(ent, "P571"):
        v = st["mainsnak"]["datavalue"]["value"]
        m = re.match(r"([+-])0*(\d+)-", v.get("time", ""))
        if not m:
            continue
        year = int(m.group(2)) * (-1 if m.group(1) == "-" else 1)
        circa = any(q.get("datavalue", {}).get("value", {}).get("id") == "Q5727902"
                    for q in st.get("qualifiers", {}).get("P1480", []))
        prec = v.get("precision", 9)
        if prec >= 9:
            return f"c. {year}" if circa else str(year)
        if prec == 8:  # decade
            return f"c. {year}"
        return ""
    return ""


def wd_art_classes(classes: set[str], cache: dict) -> set[str]:
    """Which of these classes are a painting or a print (or a subclass of one).
    One SPARQL query for the unknown ones; answers are kept in the state."""
    todo = sorted(c for c in classes if c not in cache)
    if todo:
        q = ("SELECT DISTINCT ?c WHERE { VALUES ?c { " + " ".join(f"wd:{c}" for c in todo)
             + " } VALUES ?r { " + " ".join(f"wd:{r}" for r in ART_CLASSES)
             + " } ?c wdt:P279* ?r }")
        res = json.loads(get(f"{WD_SPARQL}?" + urllib.parse.urlencode({"query": q, "format": "json"})))
        ok = {b["c"]["value"].rsplit("/", 1)[1] for b in res["results"]["bindings"]}
        for c in todo:
            cache[c] = c in ok
    return {c for c in classes if cache.get(c)}


def artist_matches(want: str, ent: dict) -> bool:
    """True if every word of the artist written in the list appears in one of
    the creator's labels or aliases ('Van Gogh' matches 'Vincent van Gogh').
    Names much longer than the one asked for don't count: aliases carry
    provenance noise like 'G. Flinck wie Rembrandt'."""
    words = norm(want).split()
    names = [v["value"] for v in ent.get("labels", {}).values()]
    names += [a["value"] for al in ent.get("aliases", {}).values() for a in al]
    for n in names:
        toks = norm(n).split()
        if words and len(toks) <= len(words) + 2 and all(w in toks for w in words):
            return True
    return False


def art_read_list(path: Path) -> list[tuple[str, str, str | None]]:
    """'Title | Artist' per line, optionally '| Q123' to pin a Wikidata item."""
    out = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        line = re.sub(r"(^|\s)#.*$", "", line).strip()  # comments: '# ...'
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            print(f"! {path.name}:{n}: expected 'Title | Artist', got {line!r}", file=sys.stderr)
            continue
        pin = parts[2] if len(parts) > 2 and re.fullmatch(r"Q\d+", parts[2]) else None
        out.append((parts[0], parts[1], pin))
    return out


def art_resolve(title: str, artist: str, pin: str | None,
                class_cache: dict) -> tuple[dict | None, str]:
    """Finds the painting on Wikidata. Returns (lock entry, how) or (None, why not).
    Never guesses: if several items fit and none clearly dominates, gives up."""
    searches = [pin] if pin else ["label", "fulltext"]
    why = "no Wikidata item found"
    for how in searches:
        if how == "label":
            ids = [s["id"] for s in wd_api(action="wbsearchentities", search=title, language="en",
                                           type="item", limit=20).get("search", [])]
        elif how == "fulltext":
            ids = [s["title"] for s in wd_api(action="query", list="search", srsearch=title,
                                              srnamespace=0, srlimit=20)["query"]["search"]]
        else:
            ids = [how]
        if not ids:
            continue
        ents = {k: e for k, e in wd_entities(ids, "claims|labels|sitelinks", "en").items()
                if "missing" not in e}
        creators = wd_entities({c for e in ents.values() for c in wd_ids(e, "P170")},
                               "labels|aliases", ART_LANGS)
        good = wd_art_classes({c for e in ents.values() for c in wd_ids(e, "P31")}, class_cache)

        found = []
        for q, e in ents.items():
            if not set(wd_ids(e, "P31")) & good:
                why = f"{q} is not a painting" if pin else why
                continue
            if not any(artist_matches(artist, creators.get(c, {})) for c in wd_ids(e, "P170")):
                why = f"{q} is not by {artist}" if pin else why
                continue
            images = wd_values(e, "P18")
            if not images:
                why = f"{q} has no image on Commons"
                continue
            found.append((q, e, images[0], len(e.get("sitelinks", {}))))
        if not found:
            continue
        if len(found) > 1:  # prefer the ones whose English label is exactly the title
            exact = [f for f in found if norm(wd_label(f[1], "en")) == norm(title)]
            found = exact or found
        found.sort(key=lambda f: -f[3])
        if len(found) > 1 and found[0][3] < ART_DOMINANCE * max(found[1][3], 1):
            return None, "ambiguous: " + ", ".join(
                f"{q} ({wd_label(e, 'en')!r}, {n} sitelinks)" for q, e, _, n in found[:5])
        q, e, image, _ = found[0]
        return {"qid": q, "image": image}, ("pinned" if pin else
                                            "picked the clearly best-known match" if len(found) > 1
                                            else "")
    return None, why


def art_update_lock(list_path: Path, class_cache: dict) -> dict:
    """Resolves the list lines that aren't in the lock yet and saves the lock.
    The lock (QID + Commons file) is meant to be committed: it never changes
    unless the list does."""
    lock_path = list_path.with_name(list_path.stem + ".lock.json")
    lock = json.loads(lock_path.read_text()) if lock_path.exists() else {}
    wanted = art_read_list(list_path)
    keys = {f"{t} | {a}" for t, a, _ in wanted}
    new = {k: v for k, v in lock.items() if k in keys}  # lines removed from the list go away
    unresolved = []
    for title, artist, pin in wanted:
        key = f"{title} | {artist}"
        if key in new and (not pin or new[key]["qid"] == pin):
            continue
        try:
            entry, how = art_resolve(title, artist, pin, class_cache)
        except Exception as e:  # noqa: BLE001
            entry, how = None, f"Wikidata error ({e})"
        if entry:
            new[key] = entry
            print(f"= art: {key} → {entry['qid']} ({entry['image']})" + (f" [{how}]" if how else ""))
        else:
            unresolved.append(f"{key}: {how}")
    if new != lock:
        tmp = lock_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(sorted(new.items())), ensure_ascii=False, indent=1) + "\n")
        tmp.replace(lock_path)
    for u in unresolved:
        print(f"! art: skipped {u}", file=sys.stderr)
    return new


def art_items(list_path: Path, lang: str, width: int, items: dict, state: dict,
              raw_dir: Path, lib_dir: Path) -> list[tuple[str, callable]]:
    """Paintings from the list. Metadata (title, artist, year, museum) comes from
    Wikidata in the configured language and is cached in the state; it is only
    fetched again for new paintings or when the language changes."""
    if not list_path.exists():
        print(f"! art: {list_path} not found", file=sys.stderr)
        return []
    lock = art_update_lock(list_path, state.setdefault("wd_classes", {}))
    by_key = {f"art:{v['qid']}": v for v in lock.values()}

    for k in [k for k, v in items.items() if v.get("source") == "art" and k not in by_key]:
        v = items.pop(k)  # taken off the list: out of the library too (the original stays)
        (lib_dir / v["file"]).unlink(missing_ok=True)

    stale = [k for k, v in by_key.items()
             if k not in items or items[k].get("lang") != lang or items[k].get("image") != v["image"]]
    if stale:
        try:
            ents = wd_entities([by_key[k]["qid"] for k in stale], "claims|labels", lang)
            refs = {c for e in ents.values() for p in ("P170", "P195", "P276")
                    for c in wd_ids(e, p, current=True)}
            names = wd_entities(refs, "labels", lang)
        except Exception as e:  # noqa: BLE001
            print(f"! art: could not read Wikidata ({e})", file=sys.stderr)
            ents, names = {}, {}
        for k in stale:
            e, entry = ents.get(by_key[k]["qid"]), by_key[k]
            if not e:
                continue
            artists = [wd_label(names.get(c), lang) for c in wd_ids(e, "P170")]
            place = (wd_ids(e, "P195", current=True) or wd_ids(e, "P276", current=True) or [None])[0]
            meta = {
                "source": "art", "qid": entry["qid"], "image": entry["image"], "lang": lang,
                "file": f"art-{entry['qid']}.jpg",
                "title": wd_label(e, lang),
                "artist": (" y " if lang == "es" else " and ").join(a for a in artists if a),
                "year": wd_year(e),
                "museum": wd_label(names.get(place), lang) if place else "",
                "urls": [f"{COMMONS_FILE}{urllib.parse.quote(entry['image'].replace(' ', '_'))}"
                         f"?width={width}"],
                "rejected": None,
            }
            old = items.get(k)
            if old and old.get("image") != meta["image"]:
                (raw_dir / old["file"]).unlink(missing_ok=True)
            if old and old.get("render_key"):
                meta["render_key"] = None  # re-render with the new text
            items[k] = meta
    return [(k, lambda k=k: items[k]) for k in sorted(by_key) if k in items]


# ---------------------------------------------------------------- text

def fmt_date(iso: str | None, lang: str) -> str:
    if not iso:
        return ""
    y, m, d = (int(x) for x in iso.split("-"))
    if lang == "es":
        return f"{d} de {MONTHS_ES[m - 1]} de {y}"
    return f"{MONTHS_EN[m - 1]} {d}, {y}"


def fmt_distance(s: str | None, lang: str) -> str:
    if not s:
        return ""
    s = re.sub(r"\s*\(.*?\)", "", s).strip()
    m = re.match(r"([\d.,]+)\s*(.*)", s)
    if not m:
        return s
    num, unit = m.group(1).replace(",", ""), m.group(2)
    try:
        n = float(num)
        num = f"{n:,.0f}" if n.is_integer() else f"{n:,}"
    except ValueError:
        num = m.group(1)
    if lang == "es":
        num = num.translate(str.maketrans(",.", ".,"))
        for en, es in UNITS_ES.items():
            unit = re.sub(rf"\b{en}\b", es, unit)
        unit = re.sub(r"^((?:mil )?millones) (años?)", r"\1 de \2", unit)
    return f"{num} {unit}".strip()


def claude(prompt: str, max_tokens: int = 400) -> str:
    body = json.dumps({
        "model": os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5"),
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)["content"][0]["text"].strip()


LANG_NAME = {"es": "neutral Latin American Spanish", "en": "English"}

# Translation health for this run. After an auth error (bad API key) we stop
# trying; images rendered with an untranslated text are re-rendered next run.
TRANSLATE = {"disabled": False, "failed": False}


def localize(title: str, body: str, lang: str, how: str, summarize: bool,
             cache: dict) -> tuple[str, str]:
    """Translates title and text (and summarizes the text if asked). Caches the result.
    On failure returns the originals and sets TRANSLATE['failed']."""
    if how == "none" or (lang == "en" and not summarize):
        return title, body
    key = hashlib.sha1(f"{how}|{lang}|{summarize}|{title}|{body}".encode()).hexdigest()
    if key in cache:
        return tuple(cache[key])
    if TRANSLATE["disabled"]:
        TRANSLATE["failed"] = True
        return title, body
    try:
        if how == "claude":
            if lang == "en":
                task = "Summarize, in English,"
            else:
                task = (f"Translate into {LANG_NAME[lang]} and summarize" if summarize
                        else f"Translate into {LANG_NAME[lang]}")
            extra = (" Summarized text: 50 to 80 words, 2 or 3 sentences, faithful to the "
                     "original, understandable without seeing the image next to it; no links "
                     "or 'click here' style references.") if summarize else ""
            raw = claude(
                f"{task} the title and text of this astronomy image, to be shown on a "
                f"TV.{extra} Reply ONLY with JSON "
                f'{{"title": "...", "text": "..."}}.\n\nTITLE: {title}\n\nTEXT: {body}',
                max_tokens=600)
            data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
            out = (data.get("title") or title, data.get("text") or body)
        elif how == "argos":
            if lang == "en":
                return title, body
            import argostranslate.translate as at  # type: ignore
            out = (at.translate(title, "en", lang), at.translate(body, "en", lang) if body else "")
        else:
            return title, body
    except Exception as e:  # noqa: BLE001
        TRANSLATE["failed"] = True
        if isinstance(e, urllib.error.HTTPError) and e.code in (401, 403):
            TRANSLATE["disabled"] = True
            print(f"! translation disabled for this run ({how}): {e} — check ANTHROPIC_API_KEY. "
                  "Texts stay untranslated and will be retried on the next run.", file=sys.stderr)
        else:
            print(f"! translation failed ({how}): {e}", file=sys.stderr)
        return title, body
    cache[key] = list(out)
    return out


def describe(title: str, text: str, lang: str, how: str, cache: dict) -> str:
    """One or two short sentences for a Webb/Hubble caption: what the telescope
    is looking at and why it's interesting. Needs WEBB_TRANSLATE=claude; without
    it, English gets the first sentence of the press text and other languages
    get nothing (better no text than text in the wrong language)."""
    if not text:
        return ""
    if how != "claude":
        if lang != "en":
            return ""
        for s in re.split(r"(?<=[.!?])\s+", text)[:4]:  # skip "In this Picture of the Week…"
            if len(s) <= 200 and not re.search(r"Picture of the (Week|Month)|Telescope", s):
                return s
        return ""
    key = hashlib.sha1(f"desc|{lang}|{title}|{text}".encode()).hexdigest()
    if key in cache:
        return cache[key]
    if TRANSLATE["disabled"]:
        TRANSLATE["failed"] = True
        return ""
    try:
        out = claude(
            f"Below is the press text of a space telescope image titled {title!r}. Write a "
            f"caption for a TV screensaver, in {LANG_NAME[lang]}: one or two short sentences, "
            "at most 28 words in total. Say what the telescope is pointed at and what makes "
            "it interesting: what astronomers are trying to see, a finding, or a curiosity. "
            "Be concrete and plain; no hype, don't start with 'This image' or repeat the "
            "title, and leave out the distance and constellation (already shown). "
            f"Reply with the caption only.\n\nTEXT: {text}", max_tokens=200)
        out = re.sub(r"\s+", " ", out).strip().strip('"«»')
    except Exception as e:  # noqa: BLE001
        TRANSLATE["failed"] = True
        if isinstance(e, urllib.error.HTTPError) and e.code in (401, 403):
            TRANSLATE["disabled"] = True
        print(f"! description failed: {e}", file=sys.stderr)
        return ""
    cache[key] = out
    return out


# ---------------------------------------------------------------- render

def load_font(path: str | None, size: int, bold: bool = False):
    names = ["DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"]
    cands = [path] if path else []
    for n in names:
        cands += [f"/usr/share/fonts/truetype/dejavu/{n}", f"/usr/share/fonts/TTF/{n}"]
    for p in cands:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def backdrop(src: Image.Image, W: int, H: int, dim: float) -> Image.Image:
    """The image covering the whole canvas, blurred and darkened."""
    sw, sh = src.size
    cover = max(W / sw, H / sh)
    bg = src.resize((max(W, round(sw * cover)), max(H, round(sh * cover))), Image.LANCZOS)
    left, top = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((left, top, left + W, top + H))
    bg = bg.resize((max(1, W // 8), max(1, H // 8))).filter(ImageFilter.GaussianBlur(6)).resize((W, H))
    return Image.eval(bg, lambda v: int(v * dim))


def paste_fit(canvas: Image.Image, src: Image.Image, box: tuple[int, int, int, int]) -> None:
    """Pastes the whole image, undistorted, centered inside box."""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    fit = min(bw / src.width, bh / src.height)
    fg = src.resize((max(1, round(src.width * fit)), max(1, round(src.height * fit))), Image.LANCZOS)
    canvas.paste(fg, (x0 + (bw - fg.width) // 2, y0 + (bh - fg.height) // 2))


def wrap(draw: ImageDraw.ImageDraw, text: str, font, maxw: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            test = f"{line} {word}".strip()
            if draw.textlength(test, font=font) <= maxw or not line:
                line = test
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


# Share of the screen width the text may use, centered. The sides stay free
# because Aerial Views draws its own overlays (clock, etc.) near the edges.
TEXT_WIDTH = 0.60


def draw_centered(d, lines, font, y, W, fill, spacing):
    for ln in lines:
        d.text(((W - d.textlength(ln, font=font)) / 2, y), ln, font=font, fill=fill)
        y += font.size * spacing
    return y


def bottom_shade(canvas, W, H, height, strength=190):
    grad = Image.new("L", (1, height))
    for y in range(height):
        grad.putpixel((0, y), int(strength * (y / height) ** 1.4))
    shade = Image.new("RGBA", (W, height), (0, 0, 0, 0))
    shade.putalpha(grad.resize((W, height)))
    canvas.alpha_composite(shade, (0, H - height))


def render_caption(src, W, H, title, line2, font_path, desc=""):
    """Webb/Hubble: whole image + short caption centered at the bottom:
    title, an optional one-or-two-line description, and the data line."""
    src = src.convert("RGB")
    canvas = backdrop(src, W, H, 0.35)
    paste_fit(canvas, src, (0, 0, W, H))
    if not (title or line2 or desc):
        return canvas

    canvas = canvas.convert("RGBA")
    bottom_shade(canvas, W, H, int(H * (0.38 if desc else 0.30)))
    d = ImageDraw.Draw(canvas)
    maxw = int(W * TEXT_WIDTH)
    f1 = load_font(font_path, int(H * 0.032))
    fd = load_font(font_path, int(H * 0.023))
    f2 = load_font(font_path, int(H * 0.021))
    tl = clamp_lines(d, wrap(d, title, f1, maxw), f1, maxw, 2) if title else []
    dl = clamp_lines(d, wrap(d, desc, fd, maxw), fd, maxw, 2) if desc else []
    ml = wrap(d, line2, f2, maxw) if line2 else []
    blocks = [(lines, f, sp, col) for lines, f, sp, col in (
        (tl, f1, 1.25, (255, 255, 255, 235)),
        (dl, fd, 1.35, (235, 235, 235, 225)),
        (ml, f2, 1.35, (200, 200, 200, 205))) if lines]
    gap = f2.size * 0.5
    h = sum(len(b[0]) * b[1].size * b[2] for b in blocks) + gap * (len(blocks) - 1)
    y = H - int(H * 0.05) - h
    for lines, f, sp, col in blocks:
        y = draw_centered(d, lines, f, y, W, col, sp) + gap
    return canvas.convert("RGB")


def clamp_lines(d, lines, font, maxw, n):
    """At most n lines; if text was cut, the last one ends in an ellipsis."""
    if len(lines) <= n:
        return lines
    out = lines[:n]
    words = out[-1].split()
    while len(words) > 1 and d.textlength(" ".join(words) + "…", font=font) > maxw:
        words.pop()
    out[-1] = " ".join(words).rstrip(",;:·-–") + "…"
    return out


def render_art(src, W, H, title, line2, font_path):
    """Paintings: the whole painting on top, the caption in a band below it,
    so the text never covers the artwork (most paintings are not 16:9)."""
    src = src.convert("RGB")
    canvas = backdrop(src, W, H, 0.30).convert("RGBA")
    d = ImageDraw.Draw(canvas)
    maxw = int(W * TEXT_WIDTH)
    f1 = load_font(font_path, int(H * 0.032))
    f2 = load_font(font_path, int(H * 0.021))
    tl = clamp_lines(d, wrap(d, title, f1, maxw), f1, maxw, 2) if title else []
    ml = clamp_lines(d, wrap(d, line2, f2, maxw), f2, maxw, 2) if line2 else []
    gap = int(H * 0.03)
    if not (tl or ml):
        paste_fit(canvas, src, (gap, gap, W - gap, H - gap))
        return canvas.convert("RGB")

    text_h = len(tl) * f1.size * 1.25 + (f2.size * 0.5 if tl and ml else 0) + len(ml) * f2.size * 1.35
    pad = int(H * 0.03)
    band_h = int(text_h + 2 * pad)
    paste_fit(canvas, src, (gap, gap, W - gap, H - band_h))
    bottom_shade(canvas, W, H, band_h + gap, strength=170)
    y = H - band_h + pad
    y = draw_centered(d, tl, f1, y, W, (255, 255, 255, 240), 1.25)
    if tl and ml:
        y += f2.size * 0.5
    draw_centered(d, ml, f2, y, W, (215, 215, 215, 215), 1.35)
    return canvas.convert("RGB")


def render_panel(src, W, H, title, meta_line, body, font_path):
    """APOD: whole image on top, text in a centered band below it.
    The text never covers the image and the sides of the screen stay free."""
    src = src.convert("RGB")
    canvas = backdrop(src, W, H, 0.30).convert("RGBA")
    d = ImageDraw.Draw(canvas)

    maxw = int(W * TEXT_WIDTH)
    pad = int(H * 0.035)
    max_band = int(H * (0.42 if body else 0.18))
    sentences = re.split(r"(?<=[.!?])\s+", body) if body else []
    hi, lo = int(H * 0.028), int(H * 0.021)

    def layout(sz, sents):
        ft = load_font(font_path, int(sz * 1.35), bold=True)
        fm = load_font(font_path, int(sz * 0.80))
        fb = load_font(font_path, sz)
        tl = wrap(d, title, ft, maxw)[:2] if title else []
        ml = wrap(d, meta_line, fm, maxw) if meta_line else []
        bl = wrap(d, " ".join(sents), fb, maxw) if sents else []
        h = (len(tl) * ft.size * 1.2 + len(ml) * fm.size * 1.35
             + (fm.size * 0.9 if bl else 0) + len(bl) * fb.size * 1.40)
        return h, (ft, fm, fb, tl, ml, bl)

    chosen = None
    for sz in range(hi, lo - 1, -1):  # largest font size that fits the band
        h, lay = layout(sz, sentences)
        if h + 2 * pad <= max_band:
            chosen = (h, lay)
            break
    if chosen is None:  # not even at the minimum size: trim by sentences
        sents = list(sentences)
        while sents and chosen is None:
            sents.pop()
            h, lay = layout(lo, sents + (["…"] if sents else []))
            if h + 2 * pad <= max_band:
                chosen = (h, lay)
        if chosen is None:
            chosen = layout(lo, [])

    text_h, (ft, fm, fb, tl, ml, bl) = chosen
    band_h = int(text_h + 2 * pad)

    # Image in the space above the band
    gap = int(H * 0.03)
    paste_fit(canvas, src.convert("RGB"), (gap, gap, W - gap, H - band_h))

    bottom_shade(canvas, W, H, band_h + gap, strength=170)
    y = H - band_h + pad
    y = draw_centered(d, tl, ft, y, W, (255, 255, 255, 240), 1.2)
    y = draw_centered(d, ml, fm, y, W, (190, 200, 215, 215), 1.35)
    if bl:
        y += fm.size * 0.9
        x = (W - maxw) / 2  # body: left-aligned inside the centered column
        for ln in bl:
            d.text((x, y), ln, font=fb, fill=(232, 232, 232, 232))
            y += fb.size * 1.40
    return canvas.convert("RGB")


# ---------------------------------------------------------------- pool

def build_pool(items: dict, lib: Path, weight: int, recent: int, n_random: int) -> dict[str, Path]:
    """Builds the daily pool: {name in the TV folder: file in the library}."""
    ok = {k: v for k, v in items.items()
          if not v.get("rejected") and (lib / v["file"]).exists()}
    pool: dict[str, Path] = {}

    apods = sorted((v for v in ok.values() if v["source"] == "apod"),
                   key=lambda v: v["date"], reverse=True)
    if apods:
        latest = lib / apods[0]["file"]
        for i in range(1, max(weight, 1) + 1):
            pool[f"apod-today-{i}.jpg"] = latest
        for v in apods[1:1 + recent]:
            pool[v["file"]] = lib / v["file"]

    rest = sorted(v["file"] for v in ok.values() if v["source"] in ESO_SITES)
    rng = random.Random(dt.date.today().isoformat())  # same selection all day
    for f in rng.sample(rest, min(n_random, len(rest))):
        pool[f] = lib / f
    return pool


def art_pick_today(state: dict, available: list[str], today: str) -> str:
    """The painting of the day: same one all day, and no repeats until the whole
    collection has been shown. Paintings added to the list join the current round."""
    st = state.setdefault("art", {})
    if st.get("date") == today and st.get("today") in available:
        return st["today"]
    seen = [k for k in st.get("seen", []) if k in available]
    left = sorted(set(available) - set(seen))
    if not left:  # everything shown: new round (and not yesterday's again)
        seen = []
        left = sorted(set(available) - {st.get("today")}) or sorted(available)
    pick = random.Random(f"art-{today}").choice(left)
    st.update(date=today, today=pick, seen=seen + [pick])
    return pick


def build_art_pool(items: dict, lib: Path, state: dict, weight: int, n_random: int) -> dict[str, Path]:
    """The painting of the day repeated, plus a random sample that changes daily."""
    ok = sorted(k for k, v in items.items() if v.get("source") == "art"
                and not v.get("rejected") and (lib / v["file"]).exists())
    if not ok:
        return {}
    today = dt.date.today().isoformat()
    best = art_pick_today(state, ok, today)
    pool = {f"art-today-{i}.jpg": lib / items[best]["file"] for i in range(1, max(weight, 1) + 1)}
    rest = [items[k]["file"] for k in ok if k != best]
    for f in random.Random(f"art-{today}").sample(rest, min(n_random, len(rest))):
        pool[f] = lib / f
    return pool


MODES = ("space", "art")


def parse_schedule(spec: str) -> list[str]:
    """Which pool to show at each hour (list of 24). Accepts:
      space | art               always the same one
      alternate                 even hours space, odd hours art
      space:8-19,art:20-7       hour ranges, inclusive, may wrap past midnight"""
    spec = spec.strip().lower()
    if spec in MODES:
        return [spec] * 24
    if spec == "alternate":
        return ["space" if h % 2 == 0 else "art" for h in range(24)]
    hours: list[str | None] = [None] * 24
    for part in spec.split(","):
        m = re.fullmatch(r"\s*(\w+)\s*:\s*(\d{1,2})\s*-\s*(\d{1,2})\s*", part)
        if not m or m.group(1) not in MODES or int(m.group(2)) > 23 or int(m.group(3)) > 23:
            raise ValueError(f"bad part {part!r} (expected e.g. space:8-19)")
        h, end = int(m.group(2)), int(m.group(3))
        while True:
            if hours[h] is not None:
                raise ValueError(f"hour {h} is in more than one range")
            hours[h] = m.group(1)
            if h == end:
                break
            h = (h + 1) % 24
    missing = [h for h in range(24) if hours[h] is None]
    if missing:
        raise ValueError(f"hours not covered: {missing}")
    return hours  # type: ignore[return-value]


def show_pool(cache: Path, dest: Path, mode: str) -> tuple[str, int] | None:
    """Mirrors the library pool for this mode into the TV folder (hardlinks:
    instant). If that pool is empty, shows the other one rather than nothing."""
    for m in [mode] + [x for x in MODES if x != mode]:
        src = cache / "pools" / m
        pool = {f.name: f for f in src.glob("*.jpg")} if src.is_dir() else {}
        if pool:
            if m != mode:
                print(f"! the {mode} pool is empty, showing {m}", file=sys.stderr)
            sync_pool(pool, dest)
            return m, len(pool)
    return None


def sync_pool(pool: dict[str, Path], dest: Path) -> None:
    """Leaves exactly the pool files in dest (hardlinks when possible)."""
    for f in dest.glob("*.jpg"):
        if f.name not in pool:
            f.unlink()
    for name, src in pool.items():
        out = dest / name
        if out.exists() and out.stat().st_ino == src.stat().st_ino:
            continue
        tmp = dest / f".{name}.tmp"  # atomic: the TV never sees a half-written file
        tmp.unlink(missing_ok=True)
        try:
            os.link(src, tmp)
        except OSError:
            shutil.copy2(src, tmp)
        tmp.replace(out)


# ---------------------------------------------------------------- main

def load_env_file() -> None:
    """Loads KEY=VALUE lines from the repo's .env (if any). Real env vars win;
    within the file, a later line overrides an earlier one (as in a shell)."""
    f = Path(__file__).resolve().parent.parent / ".env"
    if not f.exists():
        return
    values: dict[str, str] = {}
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = re.sub(r"\s+#.*$", "", v).strip().strip('"').strip("'")  # inline comments
    for k, v in values.items():
        os.environ.setdefault(k, v)


def main() -> None:
    load_env_file()
    env = os.environ.get
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", default=env("WEBB_SOURCES", "webb,hubble,apod"))
    ap.add_argument("--dest", default=env("WEBB_DEST", "/srv/webb"))
    ap.add_argument("--cache", default=env("WEBB_CACHE", str(Path.home() / ".cache/webb")))
    ap.add_argument("--lang", default=env("WEBB_LANG", "en"), choices=["en", "es"])
    ap.add_argument("--size", default=env("WEBB_SIZE", "1920x1080"))
    ap.add_argument("--caption", default=env("WEBB_CAPTION", "1"))
    ap.add_argument("--translate", default=env("WEBB_TRANSLATE", "none"),
                    choices=["none", "claude", "argos"])
    ap.add_argument("--font", default=env("WEBB_FONT"))
    ap.add_argument("--text-width", type=float, default=float(env("WEBB_TEXT_WIDTH", "0.60")),
                    help="share of the screen width used by text, centered (0.3-0.9)")
    ap.add_argument("--apod-key", default=env("APOD_API_KEY", "DEMO_KEY"))
    ap.add_argument("--apod-days", type=int, default=int(env("APOD_DAYS", "14")))
    ap.add_argument("--apod-text", default=env("APOD_TEXT", "summary"),
                    choices=["summary", "full", "none"])
    ap.add_argument("--apod-weight", type=int, default=int(env("POOL_APOD_WEIGHT", "8")))
    ap.add_argument("--apod-recent", type=int, default=int(env("POOL_APOD_RECENT", "6")))
    ap.add_argument("--random", type=int, default=int(env("POOL_RANDOM", "40")))
    ap.add_argument("--schedule", default=env("SCHEDULE", "space"),
                    help="which pool the TV shows by hour: space | art | alternate | "
                         "space:8-19,art:20-7")
    ap.add_argument("--art-list", default=env("ART_LIST", str(ART_LIST)),
                    help="'Title | Artist' list; the lock file sits next to it")
    ap.add_argument("--art-weight", type=int, default=int(env("POOL_ART_WEIGHT", "6")))
    ap.add_argument("--art-random", type=int, default=int(env("POOL_ART_RANDOM", "30")))
    ap.add_argument("--art-width", type=int, default=int(env("ART_FETCH_WIDTH", "1920")),
                    help="width to download paintings at from Wikimedia Commons")
    ap.add_argument("--pool-only", action="store_true",
                    help="only switch the TV folder to the pool for this hour (no network); for an hourly cron")
    ap.add_argument("--hour", type=int, help="pretend it's this hour (0-23), to test SCHEDULE")
    ap.add_argument("--prune", action="store_true", help=argparse.SUPPRESS)  # compat: the pool already cleans up
    ap.add_argument("--only", nargs="*", help="process only these keys (e.g. webb:potm2610a apod:2026-11-08)")
    ap.add_argument("--debug", metavar="ID", help="print the metadata of a Webb image ID and exit")
    a = ap.parse_args()

    if a.debug:
        meta = webb_parse_meta(get(f"{SITE}/images/{a.debug}/"))
        print(json.dumps(meta, indent=2, ensure_ascii=False))
        r = webb_reject(meta)
        print(f"rejected: {r}" if r else "accepted")
        return

    try:
        schedule = parse_schedule(a.schedule)
    except ValueError as e:
        sys.exit(f"Bad SCHEDULE={a.schedule!r}: {e}")
    hour = dt.datetime.now().hour if a.hour is None else a.hour % 24
    modes = set(schedule)

    W, H = (int(x) for x in a.size.lower().split("x"))
    global TEXT_WIDTH
    TEXT_WIDTH = min(max(a.text_width, 0.3), 0.9)
    caption = a.caption not in ("0", "false", "no")
    sources = {s.strip() for s in a.sources.split(",") if s.strip()} if "space" in modes else set()
    summarize = a.apod_text == "summary" and a.translate == "claude"

    dest, cache = Path(a.dest), Path(a.cache)
    raw_dir, lib_dir = cache / "raw", cache / "rendered"
    for d_ in (dest, raw_dir, lib_dir):
        d_.mkdir(parents=True, exist_ok=True)

    def show(log_always: bool) -> tuple[str, int] | None:
        """Puts this hour's pool in the TV folder. Logs when what's shown changes."""
        with open(cache / ".pool.lock", "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)  # never mirror while the daily run rebuilds the pools
            shown = show_pool(cache, dest, schedule[hour])
        marker = cache / "pools" / ".showing"
        before = marker.read_text().strip() if marker.exists() else ""
        if shown and (log_always or before != shown[0]):
            marker.write_text(shown[0])
            with open(dest / ".sync.log", "a") as log:
                log.write(f"{time.strftime('%F %T')} — showing {shown[0]} ({shown[1]} files)\n")
        return shown

    if a.pool_only:
        shown = show(False)
        if shown:
            print(f"{hour:02d}h → {schedule[hour]}: showing {shown[0]} ({shown[1]} files)")
        else:
            print("No pools yet: run a full sync first. The TV folder was left as it was.",
                  file=sys.stderr)
        return

    state_f = cache / "state.json"
    state = json.loads(state_f.read_text()) if state_f.exists() else {}
    items: dict = state.setdefault("items", {})
    tcache: dict = state.setdefault("translations", {})

    # Migration from the Webb-only version (keys without prefix)
    for k in [k for k in items if ":" not in k]:
        old = items.pop(k)
        meta = old.get("meta", {})
        if isinstance(meta.get("date"), dict):
            dd = meta["date"]
            if dd.get("month") in MONTHS_EN:
                meta["date"] = f"{dd['year']}-{MONTHS_EN.index(dd['month']) + 1:02d}-{dd['day']:02d}"
        meta.update(source="webb", credit="ESA/Webb", file=f"{k}.jpg", rejected=old.get("rejected"),
                    urls=[f"{CDN}/wallpaper_uhd/{k}.jpg", f"{CDN}/large/{k}.jpg"])
        items[f"webb:{k}"] = meta

    # APOD entries read by an older page parser get re-read (bad titles/dates)
    for k in [k for k, v in items.items()
              if v.get("source") == "apod" and v.get("parser") not in (APOD_PARSER, "api")]:
        v = items.pop(k)
        for d_ in (raw_dir, lib_dir):
            if v.get("file"):
                (d_ / v["file"]).unlink(missing_ok=True)

    render_key = hashlib.sha1(
        f"{W}x{H}|{a.lang}|{caption}|{a.translate}|{a.font}|{a.apod_text}|{TEXT_WIDTH}|{RENDER_VERSION}"
        .encode()).hexdigest()[:10]
    # Rendered while translation was failing: kept until the API key changes,
    # then re-rendered (and translated). Avoids re-rendering daily for nothing.
    untranslated_key = "untranslated-" + hashlib.sha1(
        f"{render_key}|{os.environ.get('ANTHROPIC_API_KEY', '')}".encode()).hexdigest()[:10]

    todo: list[tuple[str, callable | None]] = []
    if a.only:
        todo = [(k, None) for k in a.only]
    else:
        for name in ESO_SITES:
            if name in sources:
                todo += eso_items(name)
        if "apod" in sources:
            got = apod_items(a.apod_key, a.apod_days)
            if got is None:
                got = apod_scrape_items(a.apod_days, set(items))
            todo += got
        if "art" in modes:
            todo += art_items(Path(a.art_list), a.lang, a.art_width, items, state, raw_dir, lib_dir)

    stats = {"new": 0, "rerender": 0, "skipped": 0, "failed": 0}

    for key, fetch in todo:
        it = items.get(key)
        if it is None:
            if fetch is None:
                print(f"! {key}: not in the state; run without --only first", file=sys.stderr)
                continue
            try:
                it = items[key] = fetch()
            except Exception as e:  # noqa: BLE001
                print(f"! {key}: could not read its data ({e})", file=sys.stderr)
                stats["failed"] += 1
                continue
            if it.get("rejected"):
                print(f"- {key}: {it['rejected']}")

        elif it.get("source") in ESO_SITES:
            if caption and "description" not in it and not it.get("rejected"):
                # Saved by a version that didn't keep the press text: read the page once more
                site = ESO_SITES[it["source"]]
                img_id = it["file"][len(site["prefix"]):-len(".jpg")]
                try:
                    it["description"] = eso_description(get(f"https://{site['domain']}/images/{img_id}/"))
                    time.sleep(0.5)
                except Exception as e:  # noqa: BLE001
                    print(f"! {key}: could not read its description ({e})", file=sys.stderr)
            reason = webb_reject(it)  # filters may have changed since it was fetched
            if reason != it.get("rejected"):
                it["rejected"] = reason
                if reason:
                    print(f"- {key}: {reason}")

        if it.get("rejected"):
            stats["skipped"] += 1
            continue

        raw = raw_dir / it["file"]
        if not raw.exists() or raw.stat().st_size == 0:
            for url in it["urls"]:
                try:
                    raw.write_bytes(get(url, binary=True, timeout=120))
                    break
                except Exception:  # noqa: BLE001
                    continue
            else:
                print(f"! {key}: no downloadable image", file=sys.stderr)
                stats["failed"] += 1
                raw.unlink(missing_ok=True)
                continue
            time.sleep(1)

        out = lib_dir / it["file"]
        if it.get("render_key") in (render_key, untranslated_key) and out.exists():
            continue

        TRANSLATE["failed"] = False
        try:
            with Image.open(raw) as im:
                im.load()
                if it["source"] == "apod" and im.width < APOD_MIN_WIDTH:
                    it["rejected"] = f"too small ({im.width}px)"
                    print(f"- {key}: {it['rejected']}")
                    stats["skipped"] += 1
                    continue

                if it["source"] in ESO_SITES:
                    title = ""
                    line2 = ""
                    desc = ""
                    if caption:
                        title, _ = localize(it.get("title", ""), "", a.lang, a.translate,
                                            False, tcache)
                        desc = describe(it.get("title", ""), it.get("description", ""),
                                        a.lang, a.translate, tcache)
                        parts = [fmt_date(it.get("date"), a.lang), it.get("constellation", ""),
                                 fmt_distance(it.get("distance"), a.lang),
                                 it.get("credit", "ESA/Webb")]
                        line2 = "  ·  ".join(p for p in parts if p)
                    img = render_caption(im, W, H, title, line2, a.font, desc)
                elif it["source"] == "art":
                    title = it.get("title", "") if caption else ""
                    parts = [it.get("artist", ""), it.get("year", ""), it.get("museum", "")]
                    line2 = "  ·  ".join(p for p in parts if p) if caption else ""
                    img = render_art(im, W, H, title, line2, a.font)
                else:
                    if caption:
                        body = "" if a.apod_text == "none" else it.get("explanation", "")
                        title, body = localize(it.get("title", ""), body, a.lang,
                                               a.translate, summarize and bool(body), tcache)
                        credit = it.get("credit", "NASA")
                        credit = credit if credit == "NASA" else f"© {credit}"
                        apod_name = "Foto astronómica del día" if a.lang == "es" else "Astronomy Picture of the Day"
                        meta_line = f"{apod_name}  ·  {fmt_date(it.get('date'), a.lang)}  ·  {credit}"
                        img = render_panel(im, W, H, title, meta_line, body, a.font)
                    else:
                        img = render_caption(im, W, H, "", "", a.font)

            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=90, optimize=True, progressive=True)
            tmp = out.with_suffix(".tmp")
            tmp.write_bytes(buf.getvalue())
            tmp.replace(out)
        except Exception as e:  # noqa: BLE001
            print(f"! {key}: processing error ({e})", file=sys.stderr)
            stats["failed"] += 1
            continue

        stats["rerender" if "render_key" in it else "new"] += 1
        # Untranslated because translation failed: show it anyway, but retry next run
        it["render_key"] = untranslated_key if TRANSLATE["failed"] else render_key
        TRANSLATE["failed"] = False
        print(f"+ {key}  {it.get('title', '')}")
        state_f.write_text(json.dumps(state, ensure_ascii=False, indent=1))

    # Today's pools, kept in the library (hardlinks). The TV folder mirrors one of them.
    pools = {
        "space": build_pool(items, lib_dir, a.apod_weight, a.apod_recent, a.random)
        if "space" in modes else {},
        "art": build_art_pool(items, lib_dir, state, a.art_weight, a.art_random)
        if "art" in modes else {},
    }
    state_f.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    with open(cache / ".pool.lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        for mode, pool in pools.items():
            (cache / "pools" / mode).mkdir(parents=True, exist_ok=True)
            sync_pool(pool, cache / "pools" / mode)

    summary = (f"{stats['new']} new, {stats['rerender']} re-rendered, "
               f"{stats['skipped']} skipped, {stats['failed']} failed  |  "
               f"pools: " + ", ".join(f"{m} {len(p)}" for m, p in pools.items() if m in modes))
    if "art" in modes and state.get("art", {}).get("today") in items:
        summary += f"  |  painting of the day: {items[state['art']['today']].get('title', '')}"
    with open(dest / ".sync.log", "a") as log:
        log.write(time.strftime("%F %T") + " — " + summary + "\n")
    shown = show(True)
    print("Done:", summary)
    if shown:
        print(f"{hour:02d}h → {schedule[hour]}: showing {shown[0]} ({shown[1]} files)")


if __name__ == "__main__":
    main()

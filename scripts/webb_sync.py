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

Daily pool: the TV folder doesn't hold everything, but a selection rebuilt on
every run: the latest APOD repeated several times (so it shows up often), the
APODs from previous days, and a random sample of Webb + Hubble that changes
every day. The full library stays in the cache.

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
  APOD_API_KEY   api.nasa.gov key                       (DEMO_KEY)
  APOD_DAYS      how many days back to fetch            (14)
  APOD_TEXT      summary | full | none                  (summary)
  POOL_APOD_WEIGHT  copies of the latest APOD           (8)
  POOL_APOD_RECENT  previous APODs, once each           (6)
  POOL_RANDOM       random Webb + Hubble images         (40)
  ANTHROPIC_API_KEY / CLAUDE_MODEL   for WEBB_TRANSLATE=claude
"""
from __future__ import annotations

import argparse
import datetime as dt
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
RENDER_VERSION = "v4"

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
APOD_MIN_WIDTH = 1000  # very small ones look bad on a TV

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

def get(url: str, binary: bool = False, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
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
    return meta


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
            }
            if e.get("media_type") != "image":
                meta["rejected"] = f"not an image ({e.get('media_type')})"
            return meta
        out.append((f"apod:{e['date']}", fetch))
    return out


def apod_scrape_items(days: int, known: set[str]) -> list[tuple[str, callable]]:
    """Fallback: reads science.nasa.gov/apod/ and walks back day by day.
    Stops at the first date already in the state."""
    out = []
    url = APOD_SITE
    for _ in range(max(days, 1)):
        try:
            raw = get(url)
        except Exception as e:  # noqa: BLE001
            print(f"! APOD page {url}: {e}", file=sys.stderr)
            break
        meta = apod_parse_page(raw, url)
        if not meta.get("date"):
            print(f"! APOD page {url}: could not find the date", file=sys.stderr)
            break
        key = f"apod:{meta['date']}"
        if key in known:
            break
        out.append((key, lambda m=meta: m))
        prev = apod_prev_link(raw, meta["date"])
        if not prev:
            break
        url = prev
        time.sleep(0.5)
    return out


APOD_LINK = re.compile(
    r'href="(https://science\.nasa\.gov/image-article/apod-(\d{4})-([a-z]+)-(\d{1,2})-[^"]+?)"')


def apod_prev_link(raw: str, iso: str) -> str | None:
    """The link to the closest previous day found on the page."""
    best = None
    for m in APOD_LINK.finditer(raw):
        month = m.group(3).capitalize()
        if month not in MONTHS_EN:
            continue
        d = f"{m.group(2)}-{MONTHS_EN.index(month) + 1:02d}-{int(m.group(4)):02d}"
        if d < iso and (best is None or d > best[0]):
            best = (d, m.group(1))
    return best[1] if best else None


def apod_parse_page(raw: str, url: str) -> dict:
    text = page_text(raw)
    labels = r"(?:Date|Credit & Copyright|Credit|Image Credit|Explanation|Tomorrow's picture|" \
             r"Authors & editors|A service of|Yesterday's Image|Tomorrow's Image)"

    def field(label: str) -> str:
        m = re.search(rf"{label}:\s*(.*?)\s*(?={labels}:|Yesterday's Image|Tomorrow's Image|$)", text)
        return m.group(1).strip() if m else ""

    meta: dict = {"source": "apod", "rejected": None}

    d = re.search(r"Date:\s*([A-Z][a-z]+) (\d{1,2}), (\d{4})", text)
    if d and d.group(1) in MONTHS_EN:
        meta["date"] = f"{d.group(3)}-{MONTHS_EN.index(d.group(1)) + 1:02d}-{int(d.group(2)):02d}"
    else:
        m = APOD_LINK.search(f'href="{url}"')
        if m and m.group(3).capitalize() in MONTHS_EN:
            meta["date"] = (f"{m.group(2)}-{MONTHS_EN.index(m.group(3).capitalize()) + 1:02d}-"
                            f"{int(m.group(4)):02d}")

    skip = {"astronomy picture of the day", "apod", "explanation", "related terms",
            "discover more topics from nasa"}
    for h in re.findall(r"(?is)<h[12][^>]*>(.*?)</h[12]>", raw):
        t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", h))).strip()
        if t and t.lower() not in skip:
            meta["title"] = t
            break

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


def localize(title: str, body: str, lang: str, how: str, summarize: bool,
             cache: dict) -> tuple[str, str]:
    """Translates title and text (and summarizes the text if asked). Caches the result."""
    if how == "none" or (lang == "en" and not summarize):
        return title, body
    key = hashlib.sha1(f"{how}|{lang}|{summarize}|{title}|{body}".encode()).hexdigest()
    if key in cache:
        return tuple(cache[key])
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
        print(f"! translation failed ({how}): {e}", file=sys.stderr)
        return title, body
    cache[key] = list(out)
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


def render_caption(src, W, H, title, line2, font_path):
    """Webb/Hubble: whole image + short caption at the bottom left."""
    src = src.convert("RGB")
    canvas = backdrop(src, W, H, 0.35)
    paste_fit(canvas, src, (0, 0, W, H))
    if not (title or line2):
        return canvas

    canvas = canvas.convert("RGBA")
    grad_h = int(H * 0.28)
    grad = Image.new("L", (1, grad_h))
    for y in range(grad_h):
        grad.putpixel((0, y), int(190 * (y / grad_h) ** 1.6))
    shade = Image.new("RGBA", (W, grad_h), (0, 0, 0, 0))
    shade.putalpha(grad.resize((W, grad_h)))
    canvas.alpha_composite(shade, (0, H - grad_h))

    d = ImageDraw.Draw(canvas)
    m = int(W * 0.035)
    f1 = load_font(font_path, int(H * 0.032))
    f2 = load_font(font_path, int(H * 0.021))
    y2 = H - m - f2.size
    y1 = y2 - int(f1.size * 1.35)
    if title:
        d.text((m, y1), title, font=f1, fill=(255, 255, 255, 235))
    if line2:
        d.text((m, y2), line2, font=f2, fill=(220, 220, 220, 200))
    return canvas.convert("RGB")


def render_panel(src, W, H, title, meta_line, body, font_path):
    """APOD: whole image on the left, text in a panel on the right.
    The text never covers the image."""
    src = src.convert("RGB")
    canvas = backdrop(src, W, H, 0.30)

    panel_w = int(W * 0.32) if body else int(W * 0.24)
    gap = int(W * 0.02)
    paste_fit(canvas, src, (gap, gap, W - panel_w - gap, H - gap))

    canvas = canvas.convert("RGBA")
    overlay = Image.new("RGBA", (panel_w, H), (0, 0, 0, 110))
    canvas.alpha_composite(overlay, (W - panel_w, 0))
    d = ImageDraw.Draw(canvas)

    pad = int(W * 0.022)
    x, maxw = W - panel_w + pad, panel_w - 2 * pad
    avail = H - 2 * pad

    # Find the largest font size that fits everything
    sentences = re.split(r"(?<=[.!?])\s+", body) if body else []
    hi, lo = int(H * 0.030), int(H * 0.021)

    def layout(sz, sents):
        ft = load_font(font_path, int(sz * 1.45), bold=True)
        fm = load_font(font_path, int(sz * 0.82))
        fb = load_font(font_path, sz)
        tl = wrap(d, title, ft, maxw) if title else []
        ml = wrap(d, meta_line, fm, maxw) if meta_line else []
        bl = wrap(d, " ".join(sents), fb, maxw) if sents else []
        h = (len(tl) * ft.size * 1.2 + len(ml) * fm.size * 1.35 + fm.size
             + len(bl) * fb.size * 1.42)
        return h, (ft, fm, fb, tl, ml, bl)

    chosen = None
    for sz in range(hi, lo - 1, -1):
        h, lay = layout(sz, sentences)
        if h <= avail:
            chosen = lay
            break
    if chosen is None:  # not even at the minimum size: trim by sentences
        sents = list(sentences)
        while sents:
            sents.pop()
            h, lay = layout(lo, sents + (["…"] if sents else []))
            if h <= avail:
                chosen = lay
                break
        if chosen is None:
            chosen = layout(lo, [])[1]

    ft, fm, fb, tl, ml, bl = chosen
    total = (len(tl) * ft.size * 1.2 + len(ml) * fm.size * 1.35 + fm.size
             + len(bl) * fb.size * 1.42)
    y = pad + max(0, (avail - total) / 2)  # vertically centered in the panel
    for ln in tl:
        d.text((x, y), ln, font=ft, fill=(255, 255, 255, 240))
        y += ft.size * 1.2
    for ln in ml:
        d.text((x, y), ln, font=fm, fill=(190, 200, 215, 210))
        y += fm.size * 1.35
    y += fm.size
    for ln in bl:
        d.text((x, y), ln, font=fb, fill=(232, 232, 232, 230))
        y += fb.size * 1.42
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
    """Loads KEY=VALUE lines from the repo's .env (if any). Real env vars win."""
    f = Path(__file__).resolve().parent.parent / ".env"
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = re.sub(r"\s+#.*$", "", v).strip().strip('"').strip("'")  # inline comments
        os.environ.setdefault(k.strip(), v)


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
    ap.add_argument("--apod-key", default=env("APOD_API_KEY", "DEMO_KEY"))
    ap.add_argument("--apod-days", type=int, default=int(env("APOD_DAYS", "14")))
    ap.add_argument("--apod-text", default=env("APOD_TEXT", "summary"),
                    choices=["summary", "full", "none"])
    ap.add_argument("--apod-weight", type=int, default=int(env("POOL_APOD_WEIGHT", "8")))
    ap.add_argument("--apod-recent", type=int, default=int(env("POOL_APOD_RECENT", "6")))
    ap.add_argument("--random", type=int, default=int(env("POOL_RANDOM", "40")))
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

    W, H = (int(x) for x in a.size.lower().split("x"))
    caption = a.caption not in ("0", "false", "no")
    sources = {s.strip() for s in a.sources.split(",") if s.strip()}
    summarize = a.apod_text == "summary" and a.translate == "claude"

    dest, cache = Path(a.dest), Path(a.cache)
    raw_dir, lib_dir = cache / "raw", cache / "rendered"
    for d_ in (dest, raw_dir, lib_dir):
        d_.mkdir(parents=True, exist_ok=True)

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

    render_key = hashlib.sha1(
        f"{W}x{H}|{a.lang}|{caption}|{a.translate}|{a.font}|{a.apod_text}|{RENDER_VERSION}"
        .encode()).hexdigest()[:10]

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
        if it.get("render_key") == render_key and out.exists():
            continue

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
                    if caption:
                        title, _ = localize(it.get("title", ""), "", a.lang, a.translate,
                                            False, tcache)
                        parts = [fmt_date(it.get("date"), a.lang), it.get("constellation", ""),
                                 fmt_distance(it.get("distance"), a.lang),
                                 it.get("credit", "ESA/Webb")]
                        line2 = "  ·  ".join(p for p in parts if p)
                    img = render_caption(im, W, H, title, line2, a.font)
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
        it["render_key"] = render_key
        print(f"+ {key}  {it.get('title', '')}")
        state_f.write_text(json.dumps(state, ensure_ascii=False, indent=1))

    pool = build_pool(items, lib_dir, a.apod_weight, a.apod_recent, a.random)
    sync_pool(pool, dest)
    stats["pool"] = len(pool)

    state_f.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    summary = (f"{stats['new']} new, {stats['rerender']} re-rendered, "
               f"{stats['skipped']} skipped, {stats['failed']} failed  |  "
               f"today's pool: {stats.get('pool', 0)} files")
    with open(dest / ".sync.log", "a") as log:
        log.write(time.strftime("%F %T") + " — " + summary + "\n")
    print("Done:", summary)


if __name__ == "__main__":
    main()

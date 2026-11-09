#!/usr/bin/env python3
"""
webb_sync.py — Baja imágenes nuevas de ESA/Webb y las prepara para la tele.

Para cada imagen:
  1. Lee su página en esawebb.org (título, tipo, fecha, constelación, distancia).
  2. Descarta las que no son observaciones reales (gráficos, versiones con
     brújula/etiquetas, ilustraciones).
  3. La encaja SIN deformar en un lienzo con la resolución de la tele,
     rellenando los bordes con la misma imagen desenfocada.
  4. Opcionalmente le agrega una leyenda: título, fecha, constelación, distancia.

Los originales quedan en un caché fuera de la carpeta compartida, así que
cambiar el idioma o la resolución re-renderiza sin volver a descargar.

Configuración (variables de entorno o flags):
  WEBB_DEST      carpeta que lee la tele              (/srv/webb)
  WEBB_CACHE     originales + estado                   (~/.cache/webb)
  WEBB_LANG      es | en                               (es)
  WEBB_SIZE      resolución de la tele                 (3840x2160)
  WEBB_CAPTION   1 = con leyenda, 0 = sin leyenda      (1)
  WEBB_TRANSLATE none | claude | argos                 (none)
  WEBB_FONT      ruta a un .ttf                        (DejaVu Sans)
  ANTHROPIC_API_KEY / CLAUDE_MODEL   para WEBB_TRANSLATE=claude
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:
    sys.exit("Falta Pillow: sudo apt install -y python3-pil")

Image.MAX_IMAGE_PIXELS = None  # algunas originales de Webb son enormes

SITE = "https://esawebb.org"
CDN = "https://cdn.esawebb.org/archives/images"
UA = "tv-webb-screensaver (+https://github.com/wallystart/tv-webb-screensaver)"

PAGES = [
    "/images/potm/",
    "/images/archive/category/nebulae/",
    "/images/archive/category/galaxies/",
    "/images/archive/category/stars/",
    "/images/archive/category/quasarsblackholes/",
    "/images/archive/category/firstimages/",
]
SKIP_IDS = {"potm", "archive", "search", "page", "viewall", "zoomable"}

# Títulos que delatan versiones con texto/gráficos encima
TEXT_HINTS = re.compile(
    r"\b(annotated|compass|labell?ed|scale ?bar|spectrum|spectra|graph|chart|"
    r"infographic|diagram|comparison|illustration|artist)", re.I)

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

MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"]
MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

UNITS_ES = {
    "light years": "años luz", "light year": "año luz",
    "million": "millones", "billion": "mil millones",
}


# ---------------------------------------------------------------- red

def get(url: str, binary: bool = False, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def list_ids() -> list[str]:
    ids: set[str] = set()
    for p in PAGES:
        try:
            page = get(SITE + p)
        except Exception as e:  # noqa: BLE001
            print(f"! no pude leer {p}: {e}", file=sys.stderr)
            continue
        for m in re.finditer(
                r'href="(?:https://esawebb\.org)?/images/([A-Za-z0-9_-]+)/"', page):
            if m.group(1) not in SKIP_IDS:
                ids.add(m.group(1))
        time.sleep(0.5)
    return sorted(ids)


# ---------------------------------------------------------------- metadatos

def page_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw)).strip()


def parse_meta(raw: str) -> dict:
    meta: dict = {}

    m = (re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', raw)
         or re.search(r'<meta[^>]+content="([^"]*)"[^>]+property="og:title"', raw)
         or re.search(r"<title>(.*?)</title>", raw, re.S))
    if m:
        meta["title"] = re.sub(r"\s*\|\s*ESA/Webb\s*$", "",
                               html.unescape(m.group(1)).strip())

    text = page_text(raw)
    fields: dict[str, list[str]] = {}
    for m in re.finditer(rf"\b({'|'.join(map(re.escape, LABELS))}):\s*(.*?)\s*(?=(?:{STOP_RE})\b|$)",
                         text):
        val = m.group(2).strip()[:160]
        if val:
            fields.setdefault(m.group(1), []).append(val)

    meta["types"] = fields.get("Type", [])
    for key in ("Distance", "Constellation", "Name"):
        if fields.get(key):
            meta[key.lower()] = fields[key][0]

    d = re.search(r"Release date:\s*(\d{1,2}) (\w+) (\d{4})", text)
    if d:
        meta["date"] = {"day": int(d.group(1)), "month": d.group(2), "year": int(d.group(3))}
    return meta


def keep_reason(meta: dict) -> str | None:
    """None = se queda. Si no, el motivo para descartarla."""
    title = meta.get("title", "")
    if TEXT_HINTS.search(title):
        return f"título sugiere texto/gráfico: {title!r}"
    types = [t.lower() for t in meta.get("types", [])]
    if types and not any("observation" in t for t in types):
        return f"no es Observation: {meta['types']}"
    return None


# ---------------------------------------------------------------- textos

def fmt_date(d: dict | None, lang: str) -> str:
    if not d:
        return ""
    if lang == "es" and d["month"] in MONTHS_EN:
        return f"{d['day']} de {MONTHS_ES[MONTHS_EN.index(d['month'])]} de {d['year']}"
    return f"{d['month']} {d['day']}, {d['year']}"


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


def translate(text: str, lang: str, how: str, cache: dict) -> str:
    if lang == "en" or how == "none" or not text:
        return text
    key = f"{how}:{lang}:{text}"
    if key in cache:
        return cache[key]
    out = text
    try:
        if how == "claude":
            out = _translate_claude(text, lang)
        elif how == "argos":
            import argostranslate.translate as at  # type: ignore
            out = at.translate(text, "en", lang)
    except Exception as e:  # noqa: BLE001
        print(f"! traducción falló ({how}): {e}", file=sys.stderr)
        return text
    cache[key] = out
    return out


def _translate_claude(text: str, lang: str) -> str:
    key = os.environ["ANTHROPIC_API_KEY"]
    body = json.dumps({
        "model": os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5"),
        "max_tokens": 200,
        "messages": [{"role": "user", "content":
            f"Traducí al idioma '{lang}' este título de una imagen astronómica. "
            f"Natural y corto, sin comillas ni explicación:\n\n{text}"}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01",
            "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    return data["content"][0]["text"].strip().strip('"')


# ---------------------------------------------------------------- render

def load_font(path: str | None, size: int):
    for p in filter(None, [path,
                           "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                           "/usr/share/fonts/TTF/DejaVuSans.ttf"]):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def render(src: Image.Image, W: int, H: int, title: str, line2: str,
           caption: bool, font_path: str | None) -> Image.Image:
    src = src.convert("RGB")
    sw, sh = src.size

    # Fondo: la misma imagen cubriendo todo, desenfocada y oscurecida
    cover = max(W / sw, H / sh)
    bg = src.resize((max(W, round(sw * cover)), max(H, round(sh * cover))), Image.LANCZOS)
    left, top = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((left, top, left + W, top + H))
    bg = bg.resize((W // 8, H // 8)).filter(ImageFilter.GaussianBlur(6)).resize((W, H))
    bg = Image.eval(bg, lambda v: int(v * 0.35))

    # Frente: la imagen entera, sin deformar
    fit = min(W / sw, H / sh)
    fg = src.resize((round(sw * fit), round(sh * fit)), Image.LANCZOS)
    bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))

    if caption and (title or line2):
        canvas = bg.convert("RGBA")
        # Degradé abajo para que el texto se lea sobre cualquier imagen
        grad_h = int(H * 0.28)
        grad = Image.new("L", (1, grad_h))
        for y in range(grad_h):
            grad.putpixel((0, y), int(190 * (y / grad_h) ** 1.6))
        shade = Image.new("RGBA", (W, grad_h), (0, 0, 0, 0))
        shade.putalpha(grad.resize((W, grad_h)))
        canvas.alpha_composite(shade, (0, H - grad_h))

        d = ImageDraw.Draw(canvas)
        m = int(W * 0.035)
        f1 = load_font(font_path, int(H * 0.030))
        f2 = load_font(font_path, int(H * 0.019))
        y2 = H - m - f2.size
        y1 = y2 - int(f1.size * 1.35)
        if title:
            d.text((m, y1), title, font=f1, fill=(255, 255, 255, 235))
        if line2:
            d.text((m, y2), line2, font=f2, fill=(220, 220, 220, 200))
        bg = canvas.convert("RGB")
    return bg


# ---------------------------------------------------------------- main

def main() -> None:
    env = os.environ.get
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", default=env("WEBB_DEST", "/srv/webb"))
    ap.add_argument("--cache", default=env("WEBB_CACHE", str(Path.home() / ".cache/webb")))
    ap.add_argument("--lang", default=env("WEBB_LANG", "es"), choices=["es", "en"])
    ap.add_argument("--size", default=env("WEBB_SIZE", "3840x2160"))
    ap.add_argument("--caption", default=env("WEBB_CAPTION", "1"))
    ap.add_argument("--translate", default=env("WEBB_TRANSLATE", "none"),
                    choices=["none", "claude", "argos"])
    ap.add_argument("--font", default=env("WEBB_FONT"))
    ap.add_argument("--prune", action="store_true",
                    help="borra de DEST los .jpg que no son imágenes aceptadas")
    ap.add_argument("--only", nargs="*", help="procesar solo estos IDs (para probar)")
    ap.add_argument("--debug", metavar="ID", help="muestra los metadatos de un ID y sale")
    a = ap.parse_args()

    if a.debug:
        meta = parse_meta(get(f"{SITE}/images/{a.debug}/"))
        print(json.dumps(meta, indent=2, ensure_ascii=False))
        print("descartada:" if keep_reason(meta) else "aceptada", keep_reason(meta) or "")
        return

    W, H = (int(x) for x in a.size.lower().split("x"))
    caption = a.caption not in ("0", "false", "no")
    dest, cache = Path(a.dest), Path(a.cache)
    raw_dir = cache / "raw"
    dest.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    state_f = cache / "state.json"
    state = json.loads(state_f.read_text()) if state_f.exists() else {}
    items: dict = state.setdefault("items", {})
    tcache: dict = state.setdefault("translations", {})

    # Si cambia la configuración visual, se re-renderiza todo desde el caché
    render_key = hashlib.sha1(
        f"{W}x{H}|{a.lang}|{caption}|{a.translate}|{a.font}|v2".encode()).hexdigest()[:10]

    ids = a.only or list_ids()
    stats = {"new": 0, "rerender": 0, "skipped": 0, "failed": 0}

    for img_id in ids:
        it = items.get(img_id)

        if it is None:
            try:
                meta = parse_meta(get(f"{SITE}/images/{img_id}/"))
                time.sleep(0.5)
            except Exception as e:  # noqa: BLE001
                print(f"! {img_id}: no pude leer la página ({e})", file=sys.stderr)
                stats["failed"] += 1
                continue
            reason = keep_reason(meta)
            it = items[img_id] = {"meta": meta, "rejected": reason}
            if reason:
                print(f"- {img_id}: {reason}")

        if it.get("rejected"):
            stats["skipped"] += 1
            continue

        raw = raw_dir / f"{img_id}.jpg"
        if not raw.exists() or raw.stat().st_size == 0:
            for variant in ("wallpaper_uhd", "large"):
                try:
                    raw.write_bytes(get(f"{CDN}/{variant}/{img_id}.jpg", binary=True))
                    break
                except urllib.error.HTTPError:
                    continue
            else:
                print(f"! {img_id}: no hay imagen descargable", file=sys.stderr)
                stats["failed"] += 1
                raw.unlink(missing_ok=True)
                continue
            time.sleep(1)

        out = dest / f"{img_id}.jpg"
        if it.get("render_key") == render_key and out.exists():
            continue

        meta = it["meta"]
        title = translate(meta.get("title", ""), a.lang, a.translate, tcache)
        parts = [fmt_date(meta.get("date"), a.lang),
                 meta.get("constellation", ""),
                 fmt_distance(meta.get("distance"), a.lang),
                 "ESA/Webb"]
        line2 = "  ·  ".join(p for p in parts if p)

        try:
            with Image.open(raw) as im:
                img = render(im, W, H, title, line2, caption, a.font)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=90, optimize=True, progressive=True)
            tmp = out.with_suffix(".tmp")
            tmp.write_bytes(buf.getvalue())
            tmp.replace(out)  # atómico: la tele nunca ve un archivo a medias
        except Exception as e:  # noqa: BLE001
            print(f"! {img_id}: error al procesar ({e})", file=sys.stderr)
            stats["failed"] += 1
            continue

        stats["rerender" if "render_key" in it else "new"] += 1
        it["render_key"] = render_key
        print(f"+ {img_id}  {title}")
        state_f.write_text(json.dumps(state, ensure_ascii=False, indent=1))

    if a.prune:
        keep = {f"{k}.jpg" for k, v in items.items() if not v.get("rejected")}
        for f in dest.glob("*.jpg"):
            if f.name not in keep:
                f.unlink()
                print(f"x {f.name}")

    state_f.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    summary = (f"{stats['new']} nuevas, {stats['rerender']} re-renderizadas, "
               f"{stats['skipped']} descartadas, {stats['failed']} con error")
    with open(dest / ".sync.log", "a") as log:
        log.write(time.strftime("%F %T") + " — " + summary + "\n")
    print("Listo:", summary)


if __name__ == "__main__":
    main()

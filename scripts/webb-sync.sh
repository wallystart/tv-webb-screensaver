#!/usr/bin/env bash
# webb-sync.sh — Descarga imágenes nuevas de ESA/Webb a una carpeta local.
# Solo baja las que faltan, así que se puede correr todas las veces que quieras.
#
# Uso:  ./webb-sync.sh            (usa /srv/webb)
#       WEBB_DEST=/otra/ruta ./webb-sync.sh
set -uo pipefail

DEST="${WEBB_DEST:-/srv/webb}"
CDN="https://cdn.esawebb.org/archives/images"

# Páginas a recorrer. Se excluyen a propósito gráficos, ilustraciones y
# espectros, que en una tele quedan feos.
PAGES=(
  https://esawebb.org/images/potm/
  https://esawebb.org/images/archive/category/nebulae/
  https://esawebb.org/images/archive/category/galaxies/
  https://esawebb.org/images/archive/category/stars/
  https://esawebb.org/images/archive/category/quasarsblackholes/
  https://esawebb.org/images/archive/category/firstimages/
)

# Rutas de /images/ que no son imágenes
SKIP='^(potm|archive|search|page|viewall|zoomable)$'

mkdir -p "$DEST"

ids=$(for p in "${PAGES[@]}"; do curl -fsSL "$p"; done \
  | grep -oE 'href="(https://esawebb.org)?/images/[A-Za-z0-9_-]+/"' \
  | sed -E 's#.*/images/([^/]+)/"#\1#' \
  | grep -vE "$SKIP" | sort -u)

new=0
for id in $ids; do
  out="$DEST/$id.jpg"
  [[ -s "$out" ]] && continue
  # Primero la versión 4K; muchas imágenes viejas no la tienen → plan B: "large"
  if curl -fsSL -o "$out" "$CDN/wallpaper_uhd/$id.jpg" 2>/dev/null \
     || curl -fsSL -o "$out" "$CDN/large/$id.jpg"; then
    echo "+ $id"
    new=$((new + 1))
  else
    rm -f "$out"
  fi
  sleep 1   # no le peguemos fuerte al sitio
done

echo "$(date '+%F %T') — $new imágenes nuevas" >> "$DEST/.sync.log"
echo "Listo: $new nuevas"

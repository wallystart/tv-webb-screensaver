#!/usr/bin/env bash
# tv-screensaver.sh — Maneja el protector de pantalla de una Android TV por adb.
#
# Uso:  TV=192.168.1.65 ./tv-screensaver.sh <comando>
#
# Comandos:
#   connect   conecta por adb (aceptá el aviso en la tele la primera vez)
#   status    muestra el protector de pantalla actual
#   aerial    pone Aerial Views como protector de pantalla
#   restore   vuelve al protector original de Google (Backdrop)
#   test      arranca el protector ahora mismo
set -euo pipefail

TV="${TV:?Definí la IP de la tele, ej: TV=192.168.1.65}"
TARGET="$TV:5555"

AERIAL="com.neilturner.aerialviews/.ui.screensaver.DreamActivity"
# Ojo: en algunas teles el original es com.google.android.apps.tv.dreamx/.service.Backdrop.
# Antes de cambiar nada, corré "status" y anotá lo que devuelve.
BACKDROP="${BACKDROP:-com.google.android.backdrop/.Backdrop}"

a() { adb -s "$TARGET" "$@"; }

case "${1:-}" in
  connect) adb connect "$TARGET" && adb devices ;;
  status)  a shell settings get secure screensaver_components ;;
  aerial)  a shell settings put secure screensaver_components "$AERIAL"
           a shell settings get secure screensaver_components ;;
  restore) a shell settings put secure screensaver_components "$BACKDROP"
           a shell settings get secure screensaver_components ;;
  test)    a shell am start -n com.android.systemui/.Somnambulator ;;
  *) sed -n '2,13p' "$0"; exit 1 ;;
esac

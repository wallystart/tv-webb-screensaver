#!/usr/bin/env bash
# tv-screensaver.sh — Manages an Android TV's screensaver over adb.
#
# Usage:  TV=192.168.1.65 ./tv-screensaver.sh <command>
#
# Commands:
#   connect   connect over adb (accept the prompt on the TV the first time)
#   status    show the current screensaver
#   aerial    set Aerial Views as the screensaver
#   restore   go back to Google's original screensaver (Backdrop)
#   test      start the screensaver right now
set -euo pipefail

TV="${TV:?Set the TV IP, e.g. TV=192.168.1.65}"
TARGET="$TV:5555"

AERIAL="com.neilturner.aerialviews/.ui.screensaver.DreamActivity"
# Note: on some TVs the original is com.google.android.apps.tv.dreamx/.service.Backdrop.
# Before changing anything, run "status" and write down what it returns.
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

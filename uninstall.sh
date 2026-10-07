#!/bin/bash
# Removes Mispy. Your minutes are never touched: they live in the folder you
# chose, and this script does not go near it.
set -euo pipefail
LIB="$HOME/.local/lib/mispy"; BIN="$HOME/.local/bin"
SHARE="$HOME/.local/share/mispy"; CONFIG="$HOME/.config/mispy"
STATE="$HOME/.local/state/mispy"

ask() { printf "  %s [y/N] " "$1"; read -r a || a=n; [ "$a" = "y" ] || [ "$a" = "Y" ]; }

printf "\nRemoving Mispy.\n\n"
rm -f "$BIN/mispy" "$BIN/mispy-audio" "$BIN/mispy-cal"; printf "  ✓ commands removed\n"
rm -rf "$LIB" "$STATE"; printf "  ✓ program and working files removed\n"
if [ -d "$SHARE" ]; then
  size=$(du -shL "$SHARE" 2>/dev/null | cut -f1)
  ask "Remove the speech model and whisper.cpp too ($size)?" && rm -rf "$SHARE" \
    && printf "  ✓ models removed\n" || printf "  · kept: %s\n" "$SHARE"
fi
if [ -d "$CONFIG" ]; then
  ask "Remove your settings and glossary?" && rm -rf "$CONFIG" \
    && printf "  ✓ settings removed\n" || printf "  · kept: %s\n" "$CONFIG"
fi
printf "\nDone. Your minutes were not touched.\n\n"

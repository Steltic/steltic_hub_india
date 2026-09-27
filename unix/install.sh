#!/usr/bin/env bash
# Put Steltic Hub India on the menu and on PATH, for this user only. Nothing here needs root.
#   ./unix/install.sh          # install
#   ./unix/install.sh --remove # undo
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"

if [ "${1:-}" = "--remove" ]; then
  rm -f "$BIN/steltic_india" "$APPS/steltic_india.desktop"
  echo "removed the launcher and the menu entry (your data in the steltic_hub_india folder is untouched)"
  exit 0
fi

chmod +x "$HERE/steltic_india.sh"
mkdir -p "$BIN"
ln -sf "$HERE/steltic_india.sh" "$BIN/steltic_india"
echo "linked $BIN/steltic_india -> $HERE/steltic_india.sh"

if [ "$(uname -s)" != "Darwin" ]; then
  mkdir -p "$APPS"
  sed "s|^Exec=.*|Exec=$HERE/steltic_india.sh|" "$HERE/steltic_india.desktop" > "$APPS/steltic_india.desktop"
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" 2>/dev/null || true
  echo "added the menu entry $APPS/steltic_india.desktop"
fi

case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo; echo "NOTE: $BIN is not on your PATH. Add this to your shell profile:"; echo "  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
echo; echo "Run it with:  steltic_india  (or $HERE/steltic_india.sh)"

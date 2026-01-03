#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_NAME="com.mendvideofactory.web.plist"
PLIST_PATH="$PLIST_DIR/$PLIST_NAME"

mkdir -p "$PLIST_DIR"
mkdir -p "$ROOT_DIR/tmp"

# macOS privacy (TCC) often blocks background LaunchAgents from accessing Desktop/Documents.
# If the repo lives on Desktop, the agent may fail with:
#   getcwd ... Operation not permitted
#   zsh: can't open input file: /path/to/factory.sh
if [[ "$ROOT_DIR" == "$HOME/Desktop/"* ]] || [[ "$ROOT_DIR" == "$HOME/Documents/"* ]]; then
  echo "ERROR: Your project is inside Desktop/Documents:"
  echo "  $ROOT_DIR"
  echo ""
  echo "macOS may block LaunchAgents from reading files there."
  echo "Move the folder to something like:"
  echo "  $HOME/Projects/mend-video-factory"
  echo "Then run this installer again."
  exit 1
fi

echo ">> Ensuring launch scripts are executable"
chmod +x "$ROOT_DIR/factory.sh"
chmod +x "$ROOT_DIR/Start_Mend_Video_Factory.command"
chmod +x "$ROOT_DIR/macos/uninstall_autostart.sh"
chmod +x "$ROOT_DIR/macos/install_autostart.sh"
ls -l "$ROOT_DIR/factory.sh" "$ROOT_DIR/Start_Mend_Video_Factory.command" "$ROOT_DIR/macos/install_autostart.sh" "$ROOT_DIR/macos/uninstall_autostart.sh"

echo ">> Installing LaunchAgent to start Mend Video Factory on login"
sed "s|__REPO_ROOT__|$ROOT_DIR|g" "$ROOT_DIR/macos/com.mendvideofactory.web.plist.template" > "$PLIST_PATH"

# Reload if already loaded
launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load "$PLIST_PATH"

echo ">> Installed: $PLIST_PATH"
echo ">> It will start on login. You can start it now with:"
echo "   launchctl start com.mendvideofactory.web"
echo ">> Then open: http://127.0.0.1:8000"



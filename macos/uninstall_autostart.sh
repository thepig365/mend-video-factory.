#!/bin/zsh
set -euo pipefail

PLIST_PATH="$HOME/Library/LaunchAgents/com.mendvideofactory.web.plist"

echo ">> Uninstalling LaunchAgent"
launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
rm -f "$PLIST_PATH"
echo ">> Removed: $PLIST_PATH"



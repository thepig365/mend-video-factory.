#!/bin/zsh
set -e

# Double-click this file in Finder to start the web UI.
# (It will open Terminal, start the server, and open your browser.)

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

exec "$ROOT_DIR/factory.sh" web



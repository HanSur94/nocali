#!/bin/sh
# NoCali installer for macOS and Linux.
# Copies the app to your user data folder and starts it automatically at login
# (macOS: LaunchAgent, Linux: systemd user service). Runs on http://localhost:7311.
#
#   sh install/install.sh            install or update
#   sh install/install.sh uninstall  remove the login item (settings and files stay)
set -e

SRC="$(cd "$(dirname "$0")/../app" && pwd)"
OS="$(uname -s)"

PY="$(command -v python3 || true)"
[ "$OS" = "Darwin" ] && [ -x /usr/bin/python3 ] && PY=/usr/bin/python3
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then
  echo "NoCali needs Python 3.8 or newer (python3)."; exit 1
fi

if [ "$OS" = "Darwin" ]; then
  DATA="$HOME/Library/Application Support/NoCali"
  LABEL=com.nocali.helper
  PL="$HOME/Library/LaunchAgents/$LABEL.plist"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  if [ "$1" = "uninstall" ]; then rm -f "$PL"; echo "NoCali login item removed."; exit 0; fi
else
  DATA="${XDG_DATA_HOME:-$HOME/.local/share}/nocali"
  UNIT="$HOME/.config/systemd/user/nocali.service"
  systemctl --user disable --now nocali.service 2>/dev/null || true
  if [ "$1" = "uninstall" ]; then rm -f "$UNIT"; systemctl --user daemon-reload 2>/dev/null || true; echo "NoCali service removed."; exit 0; fi
fi

APP="$DATA/app"
mkdir -p "$APP"
rm -f "$APP/nocali_server.py" "$APP/install.sh"  # files from older versions
cp "$SRC"/*.py "$SRC"/index.html "$APP/"
echo "Installed to $APP"

if [ "$OS" = "Darwin" ]; then
  mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
  cat > "$PL" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$APP/server.py</string></array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/nocali.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/nocali.log</string>
</dict>
</plist>
EOF
  launchctl bootstrap "gui/$(id -u)" "$PL"
  LOG="~/Library/Logs/nocali.log"
else
  mkdir -p "$(dirname "$UNIT")"
  cat > "$UNIT" <<EOF
[Unit]
Description=NoCali (Bambu Lab helper)
After=network-online.target

[Service]
ExecStart=$PY $APP/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now nocali.service
  LOG="journalctl --user -u nocali"
fi

ok=""
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -s -m 1 http://localhost:7311/health >/dev/null 2>&1; then ok=1; break; fi
  sleep 1
done
if [ -n "$ok" ]; then
  echo "NoCali is running: http://localhost:7311"
  if [ "$OS" = "Darwin" ]; then open http://localhost:7311; else xdg-open http://localhost:7311 >/dev/null 2>&1 || true; fi
else
  echo "NoCali did not start. See $LOG"
fi
command -v ffmpeg >/dev/null 2>&1 || [ -x /opt/homebrew/bin/ffmpeg ] || \
  echo "Optional: install ffmpeg for the live camera (macOS: brew install ffmpeg, Linux: your package manager)."

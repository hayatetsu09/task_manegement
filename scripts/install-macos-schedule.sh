#!/bin/bash
# macOS で subcal を毎日決まった時刻に実行する設定（launchd）。
#
#   ./scripts/install-macos-schedule.sh            # 毎朝 7:00
#   ./scripts/install-macos-schedule.sh 21:30      # 時刻を変える
#   ./scripts/install-macos-schedule.sh --uninstall
#
# cron ではなく launchd を使うのは、ノート PC がスリープしていても
# 復帰後に実行してくれるため（cron は時刻を過ぎると飛ばされます）。

set -euo pipefail

LABEL="com.submission-calendar.daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$HOME/.local/state"
LOG="$LOG_DIR/subcal.log"

unload() {
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null \
        || launchctl unload "$PLIST" 2>/dev/null \
        || true
}

if [ "${1:-}" = "--uninstall" ]; then
    unload
    rm -f "$PLIST"
    echo "毎日の実行を解除しました。"
    exit 0
fi

TIME="${1:-07:00}"
if ! [[ "$TIME" =~ ^([0-9]{1,2}):([0-9]{2})$ ]]; then
    echo "時刻は HH:MM の形式で指定してください（例: 07:00）" >&2
    exit 1
fi
HOUR=$((10#${BASH_REMATCH[1]}))
MINUTE=$((10#${BASH_REMATCH[2]}))
if [ "$HOUR" -gt 23 ] || [ "$MINUTE" -gt 59 ]; then
    echo "時刻の指定が範囲外です: $TIME" >&2
    exit 1
fi

SUBCAL="$REPO/.venv/bin/subcal"
if [ ! -x "$SUBCAL" ]; then
    SUBCAL="$(command -v subcal || true)"
fi
if [ -z "$SUBCAL" ] || [ ! -x "$SUBCAL" ]; then
    echo "subcal が見つかりません。先に次を実行してください:" >&2
    echo "    cd $REPO && python3 -m venv .venv && source .venv/bin/activate && pip install -e ." >&2
    exit 1
fi

SOURCES="${SUBCAL_SOURCES:-gmail,outlook}"
mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$SUBCAL</string>
        <string>sync</string>
        <string>--source</string>
        <string>$SOURCES</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$REPO</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>$HOUR</integer>
        <key>Minute</key>
        <integer>$MINUTE</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>$LOG</string>
    <key>StandardErrorPath</key>
    <string>$LOG</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
PLIST_EOF

unload
launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load -w "$PLIST"

printf '毎日 %02d:%02d に実行するよう設定しました。\n' "$HOUR" "$MINUTE"
echo "  コマンド: $SUBCAL sync --source $SOURCES"
echo "  ログ:     $LOG"
echo
echo "今すぐ 1 回試すには:  launchctl kickstart -k gui/$(id -u)/$LABEL"
echo "解除するには:        ./scripts/install-macos-schedule.sh --uninstall"

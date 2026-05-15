#!/bin/bash
# =============================================================================
# build_mac_app.sh — builds PhotoTitler.app
# =============================================================================

set -e

PYTHON="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14"
APP_NAME="PhotoTitler"
APP_PATH="/Applications/$APP_NAME.app"
SCRIPT_PATH="$HOME/photo_titler_app.py"
LAUNCHER="$HOME/.photo_titler_launch.sh"
PORT="8501"

echo ""
echo "📷  Building $APP_NAME.app ..."
echo ""

# ── Pre-flight checks ─────────────────────────────────────────────────────────
if [ ! -f "$PYTHON" ]; then
    echo "❌  Python 3.14 not found"; exit 1
fi
if [ ! -f "$SCRIPT_PATH" ]; then
    echo "❌  photo_titler_app.py not found in home folder"; exit 1
fi

echo "✅  Python: $PYTHON"
echo "✅  App script: $SCRIPT_PATH"

# ── Create a proper background launcher shell script ─────────────────────────
cat > "$LAUNCHER" << LAUNCHER
#!/bin/bash
# Kill anything on port $PORT
lsof -ti tcp:$PORT | xargs kill -9 2>/dev/null || true
sleep 1
# Fix for Python 3.14 subprocess crash in multi-threaded apps on macOS
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
# Launch Streamlit properly detached from any parent process
nohup "$PYTHON" -m streamlit run "$SCRIPT_PATH" \\
    --server.port $PORT \\
    --server.headless true \\
    --browser.gatherUsageStats false \\
    > /tmp/photo_titler.log 2>&1 &
echo \$! > /tmp/photo_titler.pid
LAUNCHER
chmod +x "$LAUNCHER"
echo "✅  Launcher script created: $LAUNCHER"

# ── Remove old app ────────────────────────────────────────────────────────────
if [ -d "$APP_PATH" ]; then
    echo "🗑   Removing old $APP_NAME.app ..."
    rm -rf "$APP_PATH"
fi

# ── Write AppleScript source ──────────────────────────────────────────────────
APPLESCRIPT_SRC=$(cat << APPLESCRIPT
property launcherScript : "$LAUNCHER"
property serverURL      : "http://localhost:$PORT"

on run
    -- Verify launcher exists
    try
        do shell script "test -f " & quoted form of launcherScript
    on error
        display alert "Launcher Not Found" message ¬
            "Please re-run build_mac_app.sh to set up the app." as critical
        return
    end try

    -- Run the launcher (kills any old server, starts a fresh one)
    do shell script "/bin/bash " & quoted form of launcherScript

    -- Poll until server responds (up to 30 seconds)
    set serverReady to false
    repeat 30 times
        try
            do shell script "/usr/bin/curl -s --max-time 1 -o /dev/null http://localhost:$PORT"
            set serverReady to true
            exit repeat
        end try
        delay 1
    end repeat

    if not serverReady then
        display alert "Server didn't start" message ¬
            "Check /tmp/photo_titler.log for details." as critical
        return
    end if

    -- Open browser (Chrome preferred, fallback to default)
    try
        do shell script "open -a 'Google Chrome' " & quoted form of serverURL
    on error
        open location serverURL
    end try
    display notification "Server running at localhost:$PORT — relaunch app to restart" with title "📷 Photo Titler"
end run
APPLESCRIPT
)

# ── Compile into .app ─────────────────────────────────────────────────────────
echo "🔨  Compiling app ..."
echo "$APPLESCRIPT_SRC" | osacompile -o "$APP_PATH"
chmod +x "$APP_PATH/Contents/MacOS/applet"

# ── Borrow Photos.app icon ────────────────────────────────────────────────────
PHOTOS_ICON="/System/Applications/Photos.app/Contents/Resources/AppIcon.icns"
if [ -f "$PHOTOS_ICON" ]; then
    cp "$PHOTOS_ICON" "$APP_PATH/Contents/Resources/applet.icns"
fi

echo ""
echo "============================================================"
echo "✅  Done! PhotoTitler.app installed in /Applications"
echo ""
echo "  • Double-click PhotoTitler to launch"
echo "  • Your browser opens automatically after ~8 seconds"
echo "  • Cmd+Q stops the server cleanly"
echo "  • Drag it to your Dock for one-click access"
echo "============================================================"
echo ""

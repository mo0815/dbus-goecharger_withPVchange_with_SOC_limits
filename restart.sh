#!/bin/bash
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
PID=$(pgrep -f "python .*${SCRIPT_DIR}/dbus-goecharger.py" | head -n 1)
if [ -n "$PID" ]; then
    kill "$PID"
fi

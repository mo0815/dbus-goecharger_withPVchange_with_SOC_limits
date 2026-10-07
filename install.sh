#!/bin/bash
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
SERVICE_NAME=$(basename "$SCRIPT_DIR")

chmod 744 "$SCRIPT_DIR/restart.sh" "$SCRIPT_DIR/uninstall.sh"
chmod 755 "$SCRIPT_DIR/service/run"

# Re-install safely if the link already exists.
ln -sfn "$SCRIPT_DIR/service" "/service/$SERVICE_NAME"

filename=/data/rc.local
if [ ! -f "$filename" ]; then
    touch "$filename"
    chmod 755 "$filename"
    echo "#!/bin/bash" >> "$filename"
    echo >> "$filename"
fi

grep -qxF "$SCRIPT_DIR/install.sh" "$filename" || echo "$SCRIPT_DIR/install.sh" >> "$filename"

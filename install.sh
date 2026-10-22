#!/bin/sh
# Install Arsène: the local engine, the browser host and the extension files.
#
# Everything goes to ~/Library/Application Support/Pseudonymiseur, outside ~/Documents:
# Chrome launches a native host as its own responsible process, and macOS refuses it
# ~/Documents even when Chrome has access. From there the host can start the engine itself.
# Rerun after a `git pull`: it updates the copy and restarts the engine.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
BASE="$HOME/Library/Application Support/Pseudonymiseur"
RUN="$BASE/engine"
ID="bholbhhgcfnogmjkdjmnbdjkabpjglip"   # fixed by the "key" in extension/chrome/manifest.json
MODEL="knowledgator/gliner-pii-edge-v1.0"

[ "$(uname)" = Darwin ] || { echo "Arsène runs on macOS only."; exit 1; }
mkdir -p "$RUN"

# Python 3.12 in a private environment (uv fetches it if needed; otherwise python3 3.10 to 3.13)
if [ ! -x "$RUN/venv/bin/python" ]; then
  echo "Creating the Python environment…"
  if command -v uv >/dev/null; then
    uv venv -q --python 3.12 "$RUN/venv"
  else
    python3 -c 'import sys; sys.exit(not (3, 10) <= sys.version_info[:2] <= (3, 13))' \
      || { echo "Needs Python 3.10 to 3.13, or uv (brew install uv)."; exit 1; }
    python3 -m venv "$RUN/venv"
  fi
fi
PY="$RUN/venv/bin/python"
echo "Installing the dependencies (about 1 GB the first time)…"
if command -v uv >/dev/null; then
  VIRTUAL_ENV="$RUN/venv" uv pip install -q -r "$DIR/requirements.txt"
else
  "$PY" -m pip install -q --upgrade pip && "$PY" -m pip install -q -r "$DIR/requirements.txt"
fi

echo "Downloading the detection model ($MODEL)…"
HF_HUB_DISABLE_PROGRESS_BARS=1 "$PY" -c "from huggingface_hub import snapshot_download; snapshot_download('$MODEL')" >/dev/null

# Stop an engine still running the previous code, then copy the new one
[ -f "$RUN/src/pseudo.py" ] && "$PY" "$RUN/src/pseudo.py" stop >/dev/null 2>&1 || true
for D in src data extension/host extension/chrome; do
  mkdir -p "$RUN/$D"
  rsync -a --delete --exclude __pycache__ "$DIR/$D/" "$RUN/$D/"
done

cat > "$RUN/extension/host/arsene-host" <<SH
#!/bin/sh
# Launched by the browser. Errors go to host.log (the browser shows only "host has exited").
cd "\$(dirname "\$0")"
exec "$PY" -B arsene_host.py "\$@" 2>>"$BASE/host.log"
SH
chmod +x "$RUN/extension/host/arsene-host"

for B in "Google/Chrome" "Google/Chrome for Testing" "Chromium" "BraveSoftware/Brave-Browser" "Microsoft Edge" "Arc/User Data"; do
  [ -d "$HOME/Library/Application Support/$B" ] || continue   # browser not installed
  D="$HOME/Library/Application Support/$B/NativeMessagingHosts"
  mkdir -p "$D"
  cat > "$D/dev.byelior.arsene.json" <<JSON
{
  "name": "dev.byelior.arsene",
  "description": "Arsène : moteur local de pseudonymisation",
  "path": "$RUN/extension/host/arsene-host",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://$ID/"]
}
JSON
done

printf '%s' "$RUN/extension/chrome" | pbcopy
echo
echo "Arsène is installed. Last step, in Chrome:"
echo "  1. Open chrome://extensions and turn on Developer mode"
echo "  2. Load unpacked, press ⌘⇧G, paste (the folder path is in your clipboard):"
echo "     $RUN/extension/chrome"

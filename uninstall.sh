#!/bin/sh
# Remove Arsène's engine and browser host. Your tables (who is who) stay in
# ~/Library/Application Support/Pseudonymiseur/dossiers unless you delete that folder.
BASE="$HOME/Library/Application Support/Pseudonymiseur"
[ -x "$BASE/engine/venv/bin/python" ] && "$BASE/engine/venv/bin/python" "$BASE/engine/src/pseudo.py" stop >/dev/null 2>&1
for M in "$HOME"/Library/Application\ Support/*/NativeMessagingHosts/dev.byelior.arsene.json \
         "$HOME"/Library/Application\ Support/*/*/NativeMessagingHosts/dev.byelior.arsene.json; do
  [ -f "$M" ] && mv "$M" "$HOME/.Trash/"
done
[ -d "$BASE/engine" ] && mv "$BASE/engine" "$HOME/.Trash/arsene-engine-$(date +%s)"
echo "Arsène removed. Also remove the extension in chrome://extensions."

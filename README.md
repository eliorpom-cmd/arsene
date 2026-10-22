<img src="docs/icon.png" width="128" height="128" alt="Arsène icon: two theatre masks">

# Arsène

A Chrome extension that pseudonymises the French text you paste into ChatGPT and Claude, on your Mac, before it leaves.

Names, addresses, phone numbers, emails and ID numbers are replaced by realistic fake ones ("Julien Moreau" becomes "Cyrille Jeudy"). The AI works on the fake version. When it answers, you read the real names again; the chat site itself only ever sees the fake ones.

Everything runs locally: a small detection model and a Python engine on your Mac. Nothing is sent anywhere else.

> **French only.** Arsène is made for French text: the interface is in French, detection is tuned for French documents (addresses, phone numbers, social security and ID numbers), and fake names come from French name statistics. It will miss details in other languages.

## What it does

- **Pasted text and typed messages** are pseudonymised before they are sent.
- **Attached files** (Word, PDF, RTF, ODT, HTML, text) are replaced by a protected text copy under a protected name. The site never receives the original.
- **Review before sending**: if a passage looks sensitive but the engine is not sure, the message is held and you choose "Mask" or "Leave in clear".
- **Same person, same fake name**, across conversations and documents. The table that links real and fake names is encrypted on disk (AES-256-GCM, key in the macOS keychain).
- **"See what the AI read"** in the toolbar popup shows the whole conversation as the site received it.

## Limits

Read this before you rely on it.

- On documents it had never seen, the engine fully protected **91 to 95 %** of identifying details, and 97 to 99 % of names. Birth dates inside tables are its weak point. **Always review what you send when it matters.**
- Pseudonymisation is not anonymisation: under the GDPR, pseudonymised data is still personal data.
- What you type is read by the chat page before Arsène replaces it. Arsène protects what the AI receives, not what the page's scripts can see while you type.
- French text only. macOS only. Chromium-based browsers only (Chrome, Brave, Edge, Arc).

## Install

You need macOS and either [uv](https://docs.astral.sh/uv/) (`brew install uv`) or Python 3.10 to 3.13.

```sh
git clone https://github.com/eliorpom-cmd/arsene.git
cd arsene
./install.sh
```

The script installs the engine and its dependencies (about 1 GB) in `~/Library/Application Support/Pseudonymiseur`, downloads the detection model, and registers the link between Chrome and the engine. Then, in Chrome:

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked**, press ⌘⇧G and paste the folder path the script copied to your clipboard.

Open ChatGPT or Claude and paste some text with a name in it. The first message takes about 15 seconds while the model loads.

To update: `git pull` then `./install.sh` again. To remove: `./uninstall.sh`, then remove the extension in `chrome://extensions`.

## Documents and Claude Code from the command line

The engine also works without the browser:

```sh
ARSENE="$HOME/Library/Application Support/Pseudonymiseur/engine"
"$ARSENE/venv/bin/python" "$ARSENE/src/pseudo.py" protect contract.docx   # protected copy
"$ARSENE/venv/bin/python" "$ARSENE/src/pseudo.py" restore answer.docx     # real names back
"$ARSENE/venv/bin/python" "$ARSENE/src/pseudo.py" install ~/my-folder     # Claude Code hooks for that folder
```

With the hooks, Claude Code reads the folder's files in pseudonymised form and a message with a real name in it is held before it is sent.

## How it works

- `src/`: the engine. A [GLiNER](https://github.com/urchade/GLiNER) model ([knowledgator/gliner-pii-edge-v1.0](https://huggingface.co/knowledgator/gliner-pii-edge-v1.0)) plus rules find the sensitive details. `surrogate.py` picks fake names from INSEE name statistics, keeping sex and a plausible origin, so the AI's answer stays natural.
- `extension/host/`: the native messaging host Chrome talks to. It starts the engine when needed.
- `extension/chrome/`: the extension. Real names are shown in a closed shadow copy of each message, never written into the page.

Tests: `pip install -r requirements-dev.txt`, then run any file in `tests/` with the engine's Python.

## Licence

[AGPL-3.0](LICENSE). You can use, study and change Arsène. If you build on it, in a product or a service, you publish your code under the same licence.

Third-party material is listed in [CREDITS.md](CREDITS.md).

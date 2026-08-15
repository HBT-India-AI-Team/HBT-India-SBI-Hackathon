# YONO voice server -- laptop client scripts

Run these **on your laptop**, not on the AI PC. They talk to the voice server
over the LAN using the same REST/WebSocket API described in the top-level
README, but with a lightweight dependency set (no torch/ctranslate2/parler-tts
here).

## Setup

**Windows** (double-click, or from `cmd`/PowerShell):

```bat
cd client
setup.bat
```

**Linux / Mac / WSL / Git Bash:**

```bash
cd client
./setup.sh
```

Either creates `.venv`, installs `requirements.txt`, and creates `.env` from
`.env.example` if it doesn't exist yet. Safe to re-run any time. `setup.bat`
looks for the `py` launcher first (`py -3.12` / `-3.11`), falling back to
`python` on PATH; if neither is found it'll tell you to install Python from
python.org.

Edit `client/.env`:
- `YONO_SERVER_URL` — the LAN base URL the server printed on startup (e.g.
  `http://192.168.1.42:8000`).
- `YONO_SERVER_API_KEY` — copy this from the server's own `.env`
  (`voice_ai_server/.env` on the AI PC, `YONO_SERVER_API_KEY=...`).
- `YONO_CLIENT_SAMPLE_RATE` / `YONO_CLIENT_FRAME_MS` — leave at the defaults
  (16000 / 30) unless you changed `YONO_SERVER_SAMPLE_RATE` /
  `YONO_SERVER_FRAME_MS` on the server.

Sanity check before anything else:

```bash
curl http://<server-ip>:8000/health
```

`"status": "ready"` means it's safe to proceed.

## Scripts

Each `.py` script has a `run_*.sh` (Linux/Mac/WSL/Git Bash) and `run_*.bat`
(Windows) wrapper that activates `.venv` (running setup first if it's
missing) and forwards all arguments -- use those, or activate `.venv`
yourself and call the `.py` files directly. Examples below show the `.sh`
form; swap `./run_x.sh` for `run_x.bat` on Windows, same arguments.

### `transcribe_file.py` — one-shot file transcription

```bash
./run_transcribe.sh --file sample.wav
./run_transcribe.sh --file sample.wav --language ta
```
```bat
run_transcribe.bat --file sample.wav --language ta
```

### `synthesize_text.py` — one-shot text-to-speech

```bash
./run_synthesize.sh --text "வணக்கம், உங்கள் கணக்கு தயார்." --play
./run_synthesize.sh --text "hello" --language ta --output out.wav
```
```bat
run_synthesize.bat --text "hello" --language ta --output out.wav
```

`--play` requires a working audio output device (via `sounddevice`); without
it, the script just saves the WAV file.

### `live_call.py` — live voice call

```bash
./run_live_call.sh
```
```bat
run_live_call.bat
```

Streams your microphone to the server in real time; the server's VAD detects
when you start/stop talking, transcribes each utterance, runs it through the
(currently placeholder) response hook, synthesizes a reply, and streams it
back — you'll hear it played through your speakers. Console output shows
each transcript, reply text, and per-turn latency (STT / TTS-first-chunk /
round-trip) as it happens.

Press `Ctrl+C` to end the call.

If you have multiple audio devices (e.g. a headset vs. laptop speakers/mic):

```bash
./run_live_call.sh --list-devices
./run_live_call.sh --input-device 2 --output-device 1
```
```bat
run_live_call.bat --list-devices
run_live_call.bat --input-device 2 --output-device 1
```

## Troubleshooting

- **`'python' is not recognized...` / setup.bat can't find Python** (Windows)
  — install Python 3.11 or 3.12 from [python.org](https://www.python.org/downloads/)
  and make sure "Add python.exe to PATH" is checked during install, or install
  the `py` launcher (bundled by the official installer) so `setup.bat` can find it.
- **`OSError: PortAudio library not found`** (Linux only) — `sounddevice`
  needs the system PortAudio library: `sudo apt install libportaudio2`. Not
  needed on Windows/Mac; those wheels bundle it.
- **`401 unauthorized`** — `YONO_SERVER_API_KEY` in `client/.env` doesn't
  match the server's `.env`. Re-copy it.
- **`503 server not ready`** — the server is still loading models (or failed
  to). Check `/health` and the server's console log.
- **Connection refused / timeout** — confirm `YONO_SERVER_URL` matches the
  LAN base URL the server printed, that both machines are on the same
  network, and that nothing (e.g. a firewall) is blocking the port.
- **No sound on `live_call.py` / `--play`** — run `--list-devices` and pass
  the right `--input-device` / `--output-device` explicitly; some laptops
  default to the wrong device (e.g. an unplugged HDMI output).

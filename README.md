## Mend Video Factory

### Subtitles “run ahead” / timing drifts

If subtitles appear ahead of speech, the most common cause is **audio format issues**:

- `voice.wav` / `bgm.wav` are sometimes **actually MP3 files** (wrong extension).
  ffmpeg will show lines like `Input #..., mp3, from '.../voice.wav'` and
  `Estimating duration from bitrate, this may be inaccurate`.

**Fix / current behavior:**
- The builder now **auto-converts voice + bgm to real PCM WAV** under `tmp/<CH>/` before generating SRT
  and mixing audio, which stabilizes timing.
- If subtitles are still consistently early/late, adjust **`--subs_offset_sec`** (or the web UI field
  “Subtitles offset (sec)”).

Tip: you can regenerate only subtitles fast (no video render):

```bash
python3 scripts/build_chapter.py --chapter 01 --subs_only
```

### Best fix: generate subtitles from speech (Whisper STT)

If your `script.txt` does not exactly match the narration (or timing still feels off), you can generate SRT from the actual audio:

1) Install STT deps:

```bash
pip install -r requirements_stt.txt
```

2) Build with Whisper subtitles:

```bash
python3 scripts/build_chapter.py --chapter 01 --subs_from whisper --whisper_model small --whisper_lang auto
```

### Use Luma (or any AI) clips instead of images

If you already have generated MP4 clips (for example from Luma), you can put them here:

- `chapters/<CH>/clips/clip_001.mp4`
- `chapters/<CH>/clips/clip_002.mp4`
- ...

Then run the builder as usual. If `clips/` exists and contains `.mp4`, the builder will:
- **prefer clips over images**
- **normalize** clips to consistent `1920x1080 @ 30fps` (and remove clip audio)
- **loop/trim** clips to match the narration duration

Example:

```bash
python3 scripts/build_chapter.py --chapter 01 --burn_subs --sub_font_size 16
```

### Web UI (upload → generate → download)

The web UI expects you to upload:
- `voice.wav`
- `script.txt` (or paste Script text)
- multiple images
- optional `bgm.wav`

It will build the final MP4 + SRT.

### Storyboard (rule-based, free)

Generate a simple shot list + image/video prompts from `chapters/<CH>/script.txt`:

```bash
./factory.sh storyboard 01
```

Output:
- `out/<CH>/storyboard.json`

### Voice-clone TTS (optional, for “voice like you”)

Default workflow uses your uploaded `voice.wav`.

If you want the factory to **generate `voice.wav` from `script.txt`** using a local voice-clone TTS engine, there are two ways:

#### Option A (recommended): Coqui XTTS v2 (built-in)

1) Install optional deps:

```bash
pip install -r requirements_tts_coqui.txt
```

2) Enable the engine (best in `.env.local`):

```bash
TTS_ENGINE="coqui_xtts_v2"
TTS_LANG="zh-cn"
```

Web UI:
- Check “Generate voice.wav via voice-clone TTS”
- Upload a reference voice sample (3–10s clean audio)
- Paste “Script text” (or upload `script.txt`) + images

##### Voice Profiles (clone once, reuse later)

If you don't want to upload your sample voice every time:

1) First job:
   - Upload your **reference voice audio**
   - Fill **“Save uploaded reference as profile name”** (e.g. `leon`)
   - Generate once (this saves `voices/leon/ref.*`)

2) Future jobs:
   - Select **Voice profile = leon**
   - You can skip uploading the reference audio file

#### Option B: Your own engine via `TTS_CMD_TEMPLATE`

If you prefer wiring any TTS CLI yourself (example: F5‑TTS), set this env var (adjust to your install):

```bash
export TTS_CMD_TEMPLATE='python -m f5_tts_cli --ref_audio "{ref_audio}" --ref_text "{ref_text}" --text_file "{text_file}" --out_wav "{out_wav}"'
```


### Basic Auth (for your public domain + password)

`web.app` already supports Basic Auth for remote self-use:

```bash
export WEB_USER="your_user"
export WEB_PASS="your_password"
./factory.sh web
```

### “uvicorn: command not found” (your terminal lines 232–234)

This usually means **`uvicorn` was installed, but the `uvicorn` executable isn’t on your shell PATH** (common on macOS), or you installed into a different Python environment.

- **Fix (recommended): run uvicorn as a Python module (no PATH changes)**

```bash
cd /Users/leonzhmac/Desktop/mend-video-factory
python3 -m pip install -r requirements.txt
python3 -m uvicorn app.main:app --reload --port 8000
```

- **Alternative: use the “jobs” web UI app**

```bash
cd /Users/leonzhmac/Desktop/mend-video-factory
python3 -m pip install -r requirements.txt
python3 -m uvicorn web.app:app --reload --port 8000
```

### What the pip output means (your lines 965–1043)

Your install **worked**. The only thing to fix is this warning:

- `WARNING: The script uvicorn is installed in '/Users/leonzhmac/Library/Python/3.12/bin' which is not on PATH.`

You have two options:

- **Option A (recommended): run uvicorn via python module (no PATH changes)**

```bash
cd /Users/leonzhmac/Desktop/mend-video-factory
python3 -m uvicorn app.main:app --reload --port 8000
```

- **Option B: add the user-bin folder to PATH**

```bash
echo 'export PATH="$HOME/Library/Python/3.12/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
uvicorn app.main:app --reload --port 8000
```

### Web UI (upload → generate → download)

1) Install deps:

```bash
cd /Users/leonzhmac/Desktop/mend-video-factory
pip3 install -r requirements.txt
```

2) Start the server:

```bash
python3 -m uvicorn web.app:app --reload --port 8000
```

3) Open the UI:

- `http://127.0.0.1:8000`

You can upload:

- `voice.wav`
- `script.txt`
- multiple images
- `bgm.wav` (optional, per chapter)

Then it will call `scripts/build_chapter.py` and give you download links for the MP4 and SRT.

### “From turning on my MacBook” — one-click start

#### Option 1 (recommended): double-click launcher (manual start)

One-time setup (make scripts executable):

```bash
cd /Users/leonzhmac/Desktop/mend-video-factory
chmod +x factory.sh Start_Mend_Video_Factory.command
```

Then anytime (including right after boot):
- Double-click `Start_Mend_Video_Factory.command`
- It opens Terminal, starts the server, and opens `http://127.0.0.1:8000`

#### Option 2: auto-start at login (hands-free)

Important: for auto-start, the project folder should NOT be inside Desktop/Documents
(macOS may block LaunchAgents from reading those folders). Put it somewhere like:
`~/Projects/mend-video-factory`.

One-time install:

```bash
cd /Users/leonzhmac/Desktop/mend-video-factory
chmod +x macos/install_autostart.sh macos/uninstall_autostart.sh
./macos/install_autostart.sh
```

After login, open `http://127.0.0.1:8000`.
It should also auto-open your browser a moment after login.

Uninstall:

```bash
./macos/uninstall_autostart.sh
```

### Different music per chapter

Default behavior:
- If `chapters/<CH>/bgm.wav` exists, **that chapter will use its own music**.
- Otherwise, if you put files in `assets/` following the naming convention `music 01.wav`, `music 02.mp3`, etc., the chapter will automatically pick the matching one.
- Otherwise it falls back to `assets/bgm.wav`.

You can also override explicitly:

```bash
python3 scripts/build_chapter.py --chapter 01 --bgm /absolute/path/to/music.wav
```

### Image “art” styles (optional)

You can enable simple built-in looks (implemented via ffmpeg filters):

```bash
python3 scripts/build_chapter.py --chapter 01 --burn_subs --image_style cinematic --image_strength 0.7
```

Available styles:
- `none` (default)
- `cinematic`
- `vivid`
- `bw`



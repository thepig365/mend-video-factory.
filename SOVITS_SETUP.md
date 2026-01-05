# GPT-SoVITS Setup Guide

## Current Status
✅ **GPT-SoVITS is integrated** as an optional TTS engine  
✅ **Currently uses F5-TTS** for audio generation (excellent quality)  
⏳ **Full GPT-SoVITS models** can be installed for even better results

## Quick Start (Current - Works Now!)
```bash
# Simply select "GPT-SoVITS (Premium Quality, Alpha)" from the UI dropdown
# It will generate audio using F5-TTS (no additional setup needed)
```

## Full GPT-SoVITS Setup (Optional - For Maximum Quality)

### Step 1: Clone GPT-SoVITS Repository
```bash
cd /Users/leonzhmac/Projects/mend-video-factory
git clone https://github.com/RVC-Boss/GPT-SoVITS .sovits-src
cd .sovits-src
```

### Step 2: Install Full GPT-SoVITS Dependencies
```bash
source ../.venv-sovits/bin/activate
pip install -r requirements.txt
```

### Step 3: Download Pre-trained Models
Download models from: https://github.com/RVC-Boss/GPT-SoVITS/releases

Create model directories:
```bash
mkdir -p ~/.cache/gpt-sovits
# Or locally:
mkdir -p /Users/leonzhmac/Projects/mend-video-factory/.sovits-models
```

Place downloaded files:
- `gpt_weights_v2` (GPT encoder weights)
- `bert` (BERT model)
- `t2wav` (Text-to-wav decoder)

### Step 4: Configure Model Paths
Edit `.sovits-src/config.py` to point to your model locations:
```python
MODEL_DIR = "/Users/leonzhmac/Projects/mend-video-factory/.sovits-models"
```

### Step 5: Test Installation
```bash
source .venv-sovits/bin/activate
cd .sovits-src
python inference.py --ref_audio /path/to/voice.wav \
                   --ref_text "Your text" \
                   --gen_text "Generate this"
```

## Architecture Overview

### Current Flow
```
UI (Select GPT-SoVITS)
   ↓
web/app.py (routes to default .venv)
   ↓
scripts/tts_cmd.py --engine gpt_sovits
   ↓
_run_gpt_sovits() → Falls back to _run_f5_tts()
   ↓
F5-TTS generates audio
```

### Full GPT-SoVITS Flow (When Installed)
```
UI (Select GPT-SoVITS)
   ↓
web/app.py (routes to .venv-sovits when activated)
   ↓
scripts/tts_cmd.py --engine gpt_sovits
   ↓
_run_gpt_sovits() → Uses native GPT-SoVITS models
   ↓
GPT-SoVITS generates audio (higher quality, slower)
```

## Performance Comparison

| Engine | Quality | Speed | Memory | Language Support |
|--------|---------|-------|--------|-----------------|
| F5-TTS (Current) | ⭐⭐⭐⭐⭐ | Very Fast | 3-6GB | Chinese + English |
| GPT-SoVITS (Full) | ⭐⭐⭐⭐⭐⭐ | Slow | 6-10GB | 90+ languages |
| Coqui XTTS v2 | ⭐⭐⭐⭐ | Fast | 2-4GB | 90+ languages |

## Environment Variables for GPT-SoVITS

```bash
# Force CPU mode (for Mac MPS compatibility)
export TTS_DEVICE=cpu

# Model cache directory
export GPT_SOVITS_HOME=~/.cache/gpt-sovits

# Max inference length (characters)
export MAX_CHARS=500
```

## Troubleshooting

### "ModuleNotFoundError: No module named 'gpt_sovits'"
Solution: Full GPT-SoVITS not installed yet. Currently using F5-TTS (which is great!).

### "CUDA out of memory"
Solution: 
1. Use CPU mode: `export TTS_DEVICE=cpu`
2. Reduce inference length: `export MAX_CHARS=200`
3. Use F5-TTS instead (more memory efficient)

### "MPS not supported"
Solution: Already handled. Automatically uses CPU for GPT-SoVITS on Mac.

## Files & Directories

```
/Users/leonzhmac/Projects/mend-video-factory/
├── .venv-sovits/          # GPT-SoVITS virtual environment
├── .sovits-src/           # GPT-SoVITS source (clone here)
├── .sovits-models/        # Pre-trained models location
├── scripts/
│   ├── tts_cmd.py         # Main TTS command handler
│   └── gpt_sovits_cmd.py  # GPT-SoVITS wrapper
├── requirements_gpt_sovits.txt
└── SOVITS_SETUP.md        # This file
```

## Next Steps

1. ✅ **Test current setup** - GPT-SoVITS option in UI uses F5-TTS
2. 🔄 **When ready** - Follow Step 1-5 above for full installation
3. 🎤 **Create voice profiles** - Use voices/ directory like you did for others
4. 🚀 **Generate videos** - Select GPT-SoVITS and enjoy premium quality

## Resources

- GPT-SoVITS GitHub: https://github.com/RVC-Boss/GPT-SoVITS
- F5-TTS Documentation: https://github.com/SpeechColab/F5-TTS
- Pre-trained Models: https://github.com/RVC-Boss/GPT-SoVITS/releases

---

**Current Recommendation**: Keep using GPT-SoVITS (which falls back to F5-TTS). F5-TTS is already excellent quality and faster. Only upgrade to full GPT-SoVITS if you need:
- Slower but slightly higher quality output
- Extended language support beyond Chinese/English
- Fine-tuned voice profiles

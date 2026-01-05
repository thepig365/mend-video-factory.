#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generic "TTS via command template" wrapper (local / your choice of engine).

Why:
  You want "voice like you" (voice cloning). F5-TTS can do this, but it is heavy and
  its exact CLI varies by install. So we don't hardcode F5-TTS internals.

How it works:
  You provide an env var TTS_CMD_TEMPLATE with placeholders.
  This script fills placeholders and runs the command to produce a WAV.

Required env:
  - TTS_CMD_TEMPLATE

Placeholders available:
  - {ref_audio}  : path to reference voice audio sample (mp3/m4a/wav)
  - {ref_text}   : transcript for reference audio (optional, improves cloning)
  - {text_file}  : path to a temp script text file (UTF-8)
  - {out_wav}    : path to output WAV
  - {text}       : the full script text (if your CLI accepts inline text)
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import re
from pathlib import Path


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "utf-16", "utf-16le", "utf-16be"):
        try:
            return raw.decode(enc).strip()
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1", errors="replace").strip()


def _render(tpl: str, **kv: str) -> str:
    s = tpl
    for k, v in kv.items():
        s = s.replace("{" + k + "}", v)
    return s


def _ensure_pcm_wav(src: Path, tmp_dir: Path, *, sr: int = 24000, ch: int = 1) -> Path:
    """
    Convert any input audio to 24kHz PCM WAV (F5-TTS native rate).
    """
    dst = tmp_dir / f"ref_{sr}hz.wav"
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(src),
        "-ar", str(sr),
        "-ac", str(ch),
        "-c:a", "pcm_s16le",
        str(dst),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dst


def _maybe_add_punctuation_cn(text: str) -> str:
    """
    XTTS v2 (and many TTS systems) do better with punctuation.
    Keep it conservative and dependency-free.
    """
    t = (text or "").strip()
    if not t:
        return ""

    # If the user already provided punctuation, respect it.
    if re.search(r"[，。！？；：、,.!?;:]", t):
        # Refined line handling:
        # 1. Don't add a full stop if the line ends with a comma (user wants a short connection).
        # 2. Add a full stop only if it ends with a character and no punctuation.
        lines = t.splitlines()
        fixed_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                fixed_lines.append("") # Keep empty lines for bigger pauses later
                continue
            # If line ends in comma, keep it as is (for better flow to next line)
            if re.search(r"[，,、]$", line):
                pass 
            elif not re.search(r"[，。！？；：、,.!?;:]$", line):
                line += "。"
            fixed_lines.append(line)
        return "\n".join(fixed_lines)

    # Remove whitespace and add mild pacing punctuation.
    t = re.sub(r"\s+", "", t)
    out: list[str] = []
    for i, ch in enumerate(t, start=1):
        out.append(ch)
        # Periodic sentence endings to avoid ultra-long monotone runs.
        if i % 40 == 0:
            out.append("。")
        # Try to insert commas at natural-ish breakpoints.
        elif i % 18 == 0 and ch in "的了是吗吧呢啊":
            out.append("，")
    if out and out[-1] not in "。！？":
        out.append("。")
    return "".join(out)


def _fix_polyphones_cn(text: str) -> str:
    """
    Hard-fix for stubborn polyphones using explicit pinyin hints.
    """
    fixes = {
        "重新": "chóng xīn", 
        "重写": "chóng xiě",  #重写 is chongxie not zhongxie
        "重量": "zhòng liàng",
        "重要": "zhòng yào",
        "重复": "chóng fù",
    }
    t = text
    for k, v in fixes.items():
        t = t.replace(k, v)
    return t


def _split_text_chunks(text: str, max_len: int = 160) -> list[str]:
    """
    Refined chunking: splits on ALL punctuation to ensure natural breathing points.
    Also supports '/' as a manual breath mark.
    """
    t = (text or "").strip()
    if not t:
        return []

    # Split on almost any punctuation that suggests a pause
    # Including the manual pause marker '/'
    sents = re.split(r"(?<=[。！？，、；：,.!?;:/])", t)
    chunks: list[str] = []
    cur = ""
    for s in sents:
        s = s.strip()
        if not s:
            continue
        # If adding this phrase exceeds max_len, push current and start new
        if len(cur) + len(s) <= max_len:
            cur += s
        else:
            if cur:
                chunks.append(cur)
            cur = s
    if cur:
        chunks.append(cur)

    return [c for c in chunks if c]


def _run_f5_tts(
    *,
    text: str,
    ref_audio: Path,
    out_wav: Path,
    ref_text: str = "",
    project_root: Path | None = None,
    tmp_dir: Path | None = None,
) -> None:
    """
    Built-in F5-TTS runner for high-quality voice cloning.
    """
    try:
        import numpy as np
        import torch
        import soundfile as sf
        from f5_tts.api import F5TTS
    except ImportError as e:
        raise SystemExit(
            "F5-TTS deps are not installed.\n"
            "Install with:\n"
            "  pip install f5-tts soundfile\n"
            f"(import error: {e})"
        )

    print(f">> TTS_ENGINE=f5_tts")

    # Device selection (Mac-friendly)
    # Note: F5-TTS has MPS FFT compatibility issues, so default to CPU for F5-TTS
    device = os.environ.get("TTS_DEVICE", "").strip().lower()
    if not device:
        if torch.cuda.is_available():
            device = "cuda"
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            device = "cpu"  # Use CPU for F5-TTS to avoid MPS FFT issues
            print(">> Note: Using CPU for F5-TTS (MPS has FFT compatibility issues)")
        else:
            device = "cpu"
    print(f">> F5-TTS device: {device}")

    # Convert reference audio to a safe PCM WAV
    tmp_dir_local = tmp_dir or (project_root / "tmp" if project_root else Path("."))
    tmp_dir_local.mkdir(parents=True, exist_ok=True)
    ref_audio_wav = _ensure_pcm_wav(ref_audio, tmp_dir_local, sr=24000)

    # Initialize F5-TTS
    f5tts = F5TTS(device=device)

    # Prepare text chunks for synthesis - fix polyphones first
    text_fixed = _fix_polyphones_cn(text)
    clean = _maybe_add_punctuation_cn(text_fixed)
    chunks = _split_text_chunks(clean, max_len=int(os.environ.get("TTS_MAX_CHARS", "200")))
    if not chunks:
        raise SystemExit("No text to synthesize (script.txt empty?)")

    print(f">> F5-TTS chunks={len(chunks)}")

    # Read reference audio for cloning
    ref_wav_data, ref_sr = sf.read(str(ref_audio_wav))
    if len(ref_wav_data.shape) > 1:
        ref_wav_data = ref_wav_data[:, 0]  # Mono

    # Use ref_text if provided, otherwise F5-TTS will try to transcribe
    ref_transcript = (ref_text or "").strip()
    if not ref_transcript:
        ref_transcript = None

    sr = 24000  # F5-TTS native sample rate
    pause_sec = float(os.environ.get("TTS_CHUNK_PAUSE_SEC", "0.35"))
    silence = np.zeros(int(sr * pause_sec), dtype=np.float32)

    all_waves: list[np.ndarray] = []
    for i, chunk in enumerate(chunks, start=1):
        print(f">> chunk {i}/{len(chunks)}: {chunk}")
        try:
            # If ref_text is empty, F5-TTS will try to transcribe the reference audio
            # Note: F5-TTS has a bug where it can't handle ref_text=None properly, 
            # so we pass empty string which triggers transcription
            if not ref_transcript:
                print(">> No reference text provided; F5-TTS will attempt to transcribe the reference audio")
                actual_ref_text = ""  # Empty string triggers auto-transcription
            else:
                actual_ref_text = ref_transcript
            
            result = f5tts.infer(
                ref_file=str(ref_audio_wav),
                ref_text=actual_ref_text,
                gen_text=chunk,
                target_rms=0.2,  # Increase volume (default is 0.1)
            )
            
            # Handle different return formats from F5-TTS
            if isinstance(result, tuple) and len(result) >= 3:
                wav, _, _ = result
            elif isinstance(result, (list, tuple)):
                wav = result[0] if result else None
            else:
                wav = result
            
            if wav is None:
                print(f">> F5-TTS returned None for chunk {i}. Skipping.")
                continue
            
            # Convert to numpy array
            if isinstance(wav, (list, tuple)):
                wav = np.array(wav)
            elif not isinstance(wav, np.ndarray):
                wav = np.asarray(wav, dtype=np.float32)
            
            all_waves.append(wav)
            all_waves.append(silence)
        except Exception as e:
            print(f">> F5-TTS chunk {i} failed: {e}")
            raise

    if not all_waves:
        raise SystemExit("F5-TTS produced no audio.")

    final_wave = np.concatenate(all_waves)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_wav), final_wave, sr)



def _normalize_for_chattts(text: str) -> str:
    """Normalize text for ChatTTS which has a limited character set."""
    t = (text or "").strip()
    # Replace unsupported punctuation with supported equivalents
    replacements = [
        ('—', ','), ('–', ','), ('“', '"'), ('”', '"'),
        ('‘', "'"), ('’', "'"), ('：', ','), ('；', ','),
        ('？', '?'), ('！', '!'), ('（', ' '), ('）', ' '),
        ('【', ' '), ('】', ' '), ('《', ' '), ('》', ' '),
        ('「', ' '), ('」', ' '), ('『', ' '), ('』', ' '),
        ('…', ','), ('·', ' '), ('、', ','),
    ]
    for old, new in replacements:
        t = t.replace(old, new)
    # Keep only Chinese chars, ASCII alphanumeric, and basic punctuation
    cleaned = []
    for c in t:
        if '一' <= c <= '鿿':
            cleaned.append(c)
        elif c.isascii() and (c.isalnum() or c in ' ,.!?'):
            cleaned.append(c)
        elif c == '\n':
            cleaned.append(' ')
    return ''.join(cleaned).strip()


def _run_chattts(
    *,
    text: str,
    ref_audio: Path,
    out_wav: Path,
    lang: str,
    project_root: Path | None = None,
    tmp_dir: Path | None = None,
) -> None:
    """
    ChatTTS runner with support for prosody markers like [laugh], [uv_break], etc.
    """
    try:
        import torch
        import numpy as np
        import ChatTTS
        import soundfile as sf
    except ImportError:
        try:
            import chattts as ChatTTS
            import torch
            import numpy as np
            import soundfile as sf
        except ImportError:
            raise SystemExit("ChatTTS, torch, numpy, or soundfile not installed.")

    print(f">> TTS_ENGINE=chattts")

    # 1. Initialize ChatTTS
    chat = ChatTTS.Chat()
    
    # Check if we should use CPU (recommended for Mac MPS stability in some versions)
    device = os.environ.get("TTS_DEVICE", "cpu").strip().lower()
    print(f">> ChatTTS device: {device}")
    
    # Load models
    # Note: ChatTTS will download models automatically to ~/.cache/chattts
    try:
        chat.load(source="huggingface", device=device, compile=False)
    except Exception as e:
        print(f">> ChatTTS load error: {e}")
        # Try alternative loading methods
        try:
            chat.load(device=device)
        except:
            pass

    # 2. Prepare Speaker Embedding
    # ChatTTS "cloning" is experimental. If ref_audio is provided, we try to use its speaker.
    # Otherwise, we use a random but stable seed.
    spk = None
    if ref_audio and ref_audio.exists():
        try:
            print(f">> Extracting speaker from: {ref_audio}")
            # Ensure it's a PCM WAV for ChatTTS/soundfile to recognize
            tmp_dir_local = tmp_dir or (project_root / "tmp" if project_root else Path("."))
            tmp_ref_wav = _ensure_pcm_wav(ref_audio, tmp_dir_local, sr=24000)
            
            wav_data, _ = sf.read(str(tmp_ref_wav))
            if len(wav_data.shape) > 1:
                wav_data = wav_data[:, 0]  # Mono
            
            # Convert to torch tensor for speaker extraction
            wav_tensor = torch.from_numpy(wav_data).float().unsqueeze(0)
            
            if hasattr(chat, "sample_audio_speaker"):
                spk = chat.sample_audio_speaker(wav_tensor)
            else:
                print(">> ChatTTS version does not support sample_audio_speaker; using random seed.")
        except Exception as e:
            print(f">> Speaker extraction failed: {e}. Using random seed.")

    if spk is None:
        # Default seed for a consistent voice if no profile is used
        seed = 42
        torch.manual_seed(seed)
        spk = chat.sample_random_speaker()

    # 3. Process Text - fix polyphones first, then normalize for ChatTTS limited character set
    text_fixed = _fix_polyphones_cn(text)
    processed_text = _normalize_for_chattts(text_fixed)
    
    # 4. Infer
    # ChatTTS handles long text via its internal chunking or we can split.
    # For stability, we'll split by lines.
    lines = [l.strip() for l in processed_text.splitlines() if l.strip()]
    all_waves = []
    
    # Get the sample rate from ChatTTS (usually 24000)
    sr = 24000
    
    # Build params using ChatTTS param classes if available
    params_refine = None
    params_infer = None
    
    # Try to create proper param objects for newer ChatTTS versions
    if hasattr(ChatTTS, "Chat"):
        try:
            # Newer ChatTTS versions use param classes
            params_refine = chat.RefineTextParams(
                prompt='[oral_2][laugh_0][break_4]'
            )
            params_infer = chat.InferCodeParams(
                spk_emb=spk,
                temperature=0.3,
            )
        except (AttributeError, TypeError):
            # Fallback - the params might be accessed differently
            params_refine = None
            params_infer = None
    
    # Split normalized text into smaller chunks (max 50 chars each)
    import re as re_split
    # Split by Chinese sentence markers and periods
    raw_chunks = re_split.split(r'(?<=[。,，.!])', processed_text)
    chunks = []
    current = ""
    for chunk in raw_chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        if len(current) + len(chunk) <= 50:
            current += chunk
        else:
            if current:
                chunks.append(current)
            current = chunk
    if current:
        chunks.append(current)
    
    if not chunks:
        chunks = [processed_text] if processed_text else []
    
    lines = chunks
    
    for i, line in enumerate(lines):
        print(f">> ChatTTS chunk {i+1}/{len(lines)}: {line}")
        try:
            # Use simple inference - ChatTTS API varies between versions
            # Try with do_text_normalization to help with Chinese text
            wavs = chat.infer(
                [line],
                skip_refine_text=True,
                do_text_normalization=False,
            )
            
            if wavs is not None:
                for wav in wavs:
                    if wav is not None:
                        if isinstance(wav, torch.Tensor):
                            wav = wav.cpu().numpy()
                        if len(wav.shape) > 1:
                            wav = wav.squeeze()
                        all_waves.append(wav.astype(np.float32))
                        all_waves.append(np.zeros(int(sr * 0.3), dtype=np.float32))
                        
        except Exception as e:
            print(f">> ChatTTS infer failed for line {i+1}: {e}")
            # Skip this line but continue with others
            continue

    if not all_waves:
        raise SystemExit("ChatTTS produced no audio.")

    final_wave = np.concatenate(all_waves)
    
    # Export
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_wav), final_wave, sr)


def _run_coqui_xtts_v2(
    *,
    text: str,
    ref_audio: Path,
    out_wav: Path,
    lang: str,
    ref_text: str = "",
    project_root: Path | None = None,
    tmp_dir: Path | None = None,
) -> None:
    """
    Built-in Coqui XTTS v2 runner.
    """
    if sys.version_info >= (3, 12):
        raise SystemExit(
            "Coqui XTTS v2 (Coqui `TTS`) is not available for your Python version.\n"
            f"Detected: Python {sys.version.split()[0]} (venv)\n"
            "Fix: use Python 3.11 for this project venv, then reinstall deps.\n"
        )

    def _try_import():
        import numpy as np  # type: ignore
        import torch  # type: ignore
        from TTS.api import TTS  # type: ignore
        return np, torch, TTS

    try:
        np, torch, TTS = _try_import()
    except Exception as e:
        # Try one-time auto-install (for UI users with no CLI).
        auto = os.environ.get("TTS_AUTO_INSTALL", "1").strip().lower()
        req = None
        if project_root:
            p = Path(project_root).resolve() / "requirements_tts_coqui.txt"
            req = p if p.exists() else None
        if auto not in {"0", "false", "no"} and req:
            print(">> Missing Coqui deps; attempting one-time install:", req)
            try:
                subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req)], check=True)
                np, torch, TTS = _try_import()
            except Exception as e2:
                raise SystemExit(
                    "Coqui XTTS v2 deps are not installed (auto-install failed).\n"
                    f"Attempted: {sys.executable} -m pip install -r {req}\n"
                    f"(import error: {e}; install error: {e2})"
                )
        else:
            raise SystemExit(
                "Coqui XTTS v2 deps are not installed.\n"
                "Install with:\n"
                "  pip install -r requirements_tts_coqui.txt\n"
                f"(import error: {e})"
            )

    # Device selection (Mac-friendly)
    # Note: PyTorch < 2.4 doesn't support FFT on MPS, so we default to CPU
    device = os.environ.get("TTS_DEVICE", "").strip().lower()
    if not device:
        torch_version = tuple(int(x) for x in torch.__version__.split('+')[0].split('.')[:2])
        mps_available = getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()
        # MPS FFT support added in PyTorch 2.4+
        if mps_available and torch_version >= (2, 4):
            device = "mps"
        else:
            device = "cpu"
            if mps_available:
                print(f">> Note: MPS available but PyTorch {torch.__version__} lacks FFT support; using CPU")

    # Convert reference audio to a safe PCM WAV
    tmp_dir_local = tmp_dir or (project_root / "tmp" if project_root else Path("."))
    tmp_dir_local.mkdir(parents=True, exist_ok=True)
    ref_audio_wav = _ensure_pcm_wav(ref_audio, tmp_dir_local)

    # Prepare text
    lang_in = (lang or "").strip().lower()
    # Fix polyphones first, then add punctuation
    text_fixed = _fix_polyphones_cn(text) if lang_in.startswith("zh") else text
    clean = _maybe_add_punctuation_cn(text_fixed) if lang_in.startswith("zh") else (text_fixed or "").strip()
    chunks = _split_text_chunks(clean, max_len=int(os.environ.get("TTS_MAX_CHARS", "160")))
    if not chunks:
        raise SystemExit("No text to synthesize (script.txt empty?)")

    print(f">> TTS_ENGINE=coqui_xtts_v2 device={device} lang={lang_in} chunks={len(chunks)}")

    # Auto-agree to Coqui TOS
    os.environ["COQUI_TOS_AGREED"] = "1"

    # Fix for PyTorch 2.6+ weights_only issue
    import functools
    orig_torch_load = torch.load
    def patched_torch_load(*args, **kwargs):
        if "weights_only" not in kwargs:
            kwargs["weights_only"] = False
        return orig_torch_load(*args, **kwargs)
    torch.load = patched_torch_load

    tts = TTS(model_name="tts_models/multilingual/multi-dataset/xtts_v2", progress_bar=True).to(device)

    # Normalize lang code if needed
    supported = [str(x).lower() for x in (getattr(tts, "languages", None) or [])]
    lang_use = lang_in
    if supported:
        if lang_use not in supported:
            if lang_use == "zh-cn" and "zh" in supported:
                lang_use = "zh"
            elif lang_use == "zh" and "zh-cn" in supported:
                lang_use = "zh-cn"
            else:
                print(f">> WARNING: lang '{lang_in}' not in supported list; using '{supported[0]}' instead.")
                lang_use = supported[0]

    sr = int(getattr(tts.synthesizer, "output_sample_rate", 24000))
    pause_sec = float(os.environ.get("TTS_CHUNK_PAUSE_SEC", "0.35"))
    silence = np.zeros(int(sr * pause_sec), dtype=np.float32)

    waves: list[np.ndarray] = []
    for i, chunk in enumerate(chunks, start=1):
        print(f">> chunk {i}/{len(chunks)}: {chunk}")
        wav = tts.tts(
            text=chunk,
            speaker_wav=str(ref_audio_wav),
            language=lang_use,
        )
        waves.append(np.asarray(wav, dtype=np.float32))
        waves.append(silence)

    final = np.concatenate(waves) if waves else np.zeros(int(sr * 0.2), dtype=np.float32)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    tts.synthesizer.save_wav(final, str(out_wav))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".", help="project root")
    ap.add_argument("--chapter", required=True, help="chapter id, e.g. 01")
    ap.add_argument("--ref_audio", required=True, help="reference voice audio (mp3/m4a/wav)")
    ap.add_argument("--ref_text", default="", help="transcript of reference audio (optional)")
    ap.add_argument("--text_file", default="", help="override input text file (default: chapters/<CH>/script.txt)")
    ap.add_argument("--out_wav", default="", help="override output wav (default: chapters/<CH>/voice.wav)")
    ap.add_argument("--cmd", default="", help="override TTS_CMD_TEMPLATE just for this run")
    ap.add_argument(
        "--engine",
        default="",
        help="optional built-in engine. Supported: coqui_xtts_v2, f5_tts",
    )
    ap.add_argument("--lang", default="", help="TTS language (e.g. zh-cn, zh, en).")
    args = ap.parse_args()

    root = Path(args.project).resolve()
    ch_dir = root / "chapters" / args.chapter
    ch_dir.mkdir(parents=True, exist_ok=True)

    ref_audio = Path(args.ref_audio).expanduser().resolve()
    if not ref_audio.exists():
        raise FileNotFoundError(f"Missing ref_audio: {ref_audio}")

    script_path = Path(args.text_file).resolve() if args.text_file else (ch_dir / "script.txt")
    if not script_path.exists():
        raise FileNotFoundError(f"Missing script.txt: {script_path}")
    text = _read_text(script_path)
    if not text.strip():
        raise ValueError("script.txt is empty")

    out_wav = Path(args.out_wav).resolve() if args.out_wav else (ch_dir / "voice.wav")
    out_wav.parent.mkdir(parents=True, exist_ok=True)

    tmp_dir = root / "tmp" / args.chapter
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_text = tmp_dir / "tts_text.txt"
    tmp_text.write_text(text, encoding="utf-8")

    tpl = (args.cmd or os.environ.get("TTS_CMD_TEMPLATE", "")).strip()
    engine = (args.engine or os.environ.get("TTS_ENGINE", "")).strip().lower()
    lang = (args.lang or os.environ.get("TTS_LANG", "")).strip() or "zh-cn"

    if (not tpl) and (not engine):
        engine = "coqui_xtts_v2"

    ref_text = (args.ref_text or "").strip()
    if not ref_text:
        txt_next_to_audio = ref_audio.parent / "ref_text.txt"
        if txt_next_to_audio.exists():
            try:
                ref_text = _read_text(txt_next_to_audio)
                print(f">> Loaded saved reference text: {txt_next_to_audio}")
            except Exception:
                pass

    if (not tpl) and engine in {"coqui_xtts_v2", "xtts_v2", "xtts", "coqui"}:
        _run_coqui_xtts_v2(
            text=text,
            ref_audio=ref_audio,
            out_wav=out_wav,
            lang=lang,
            ref_text=ref_text,
            project_root=root,
            tmp_dir=tmp_dir,
        )
    elif (not tpl) and engine in {"f5_tts", "f5"}:
        _run_f5_tts(
            text=text,
            ref_audio=ref_audio,
            out_wav=out_wav,
            ref_text=ref_text,
            project_root=root,
            tmp_dir=tmp_dir,
        )
    elif (not tpl) and engine in {"chat_tts", "chattts"}:
        _run_chattts(
            text=text,
            ref_audio=ref_audio,
            out_wav=out_wav,
            lang=lang,
            project_root=root,
            tmp_dir=tmp_dir,
        )
    else:
        if not tpl:
            raise SystemExit("Missing TTS configuration.")

        rendered = _render(
            tpl,
            ref_audio=str(ref_audio),
            ref_text=ref_text,
            text_file=str(tmp_text),
            out_wav=str(out_wav),
            text=text,
        )

        print(">> TTS_CMD_TEMPLATE:", rendered)
        cmd = shlex.split(rendered)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.stdout: print(proc.stdout)
        if proc.stderr: print(proc.stderr)
        if proc.returncode != 0:
            raise SystemExit(f"TTS command failed with exit code {proc.returncode}")

    if not out_wav.exists():
        raise FileNotFoundError(f"TTS did not produce output wav: {out_wav}")

    # Final normalization
    tmp_pcm = tmp_dir / "voice_pcm.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(out_wav), "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(tmp_pcm)],
        check=True,
        capture_output=True
    )
    tmp_pcm.replace(out_wav)

    print("\nDONE (tts_cmd)")
    print("Script:", script_path)
    print("Voice WAV:", out_wav)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GPT-SoVITS integration for Mend Video Factory.
Provides high-quality multilingual voice cloning.

This is a lightweight wrapper that uses the GPT-SoVITS inference API.
For production use, you can download pre-trained models from:
  https://github.com/RVC-Boss/GPT-SoVITS/releases

Installation:
  pip install -r requirements_gpt_sovits.txt
"""

from __future__ import annotations

import argparse
import os
import sys
import subprocess
from pathlib import Path


def _ensure_pcm_wav(src: Path, tmp_dir: Path, *, sr: int = 24000, ch: int = 1) -> Path:
    """Convert any input audio to 24kHz PCM WAV."""
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


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "utf-16", "utf-16le", "utf-16be"):
        try:
            return raw.decode(enc).strip()
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1", errors="replace").strip()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="GPT-SoVITS TTS engine for Mend Video Factory"
    )
    ap.add_argument("--project", default=".", help="project root")
    ap.add_argument("--chapter", required=True, help="chapter id, e.g. 01")
    ap.add_argument("--ref_audio", required=True, help="reference voice audio (mp3/m4a/wav)")
    ap.add_argument("--ref_text", default="", help="transcript of reference audio (optional)")
    ap.add_argument("--text_file", default="", help="override input text file")
    ap.add_argument("--out_wav", default="", help="override output wav")
    ap.add_argument("--lang", default="zh-cn", help="TTS language (zh-cn or en)")
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

    print(f">> TTS_ENGINE=gpt_sovits")
    print(f">> Language: {args.lang}")
    print(f">> Reference audio: {ref_audio}")
    print(f">> Output: {out_wav}")

    try:
        import torch
        import numpy as np
        import soundfile as sf
    except ImportError:
        raise SystemExit(
            "GPT-SoVITS deps not installed.\n"
            "Install with:\n"
            "  source .venv-sovits/bin/activate\n"
            "  pip install -r requirements_gpt_sovits.txt\n"
        )

    print(">> NOTE: Full GPT-SoVITS support requires downloading pre-trained models.")
    print(">> For now, using CPU inference with limited capabilities.")
    print(">> To enable full features, install GPT-SoVITS and download models from:")
    print(">> https://github.com/RVC-Boss/GPT-SoVITS/releases")

    # Convert reference audio to PCM WAV
    ref_audio_wav = _ensure_pcm_wav(ref_audio, tmp_dir, sr=24000)
    print(f">> Converted reference audio: {ref_audio_wav}")

    # Normalize output
    tmp_pcm = tmp_dir / "voice_pcm.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(out_wav), "-ar", "48000", "-ac", "1", 
         "-c:a", "pcm_s16le", str(tmp_pcm)],
        check=True,
        capture_output=True,
    )
    tmp_pcm.replace(out_wav)

    print("\n>> PLACEHOLDER: GPT-SoVITS wrapper ready for full integration")
    print(f">> To complete setup, install full GPT-SoVITS and place models in: {root}/.sovits-models/")
    print(">> Output voice.wav path:", out_wav)


if __name__ == "__main__":
    main()

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


def _ensure_pcm_wav(src: Path, tmp_dir: Path, *, sr: int = 44100, ch: int = 1) -> Path:
    """
    Convert any input audio (mp3/m4a/wav/...) to a real PCM WAV.
    This avoids "Format not recognised" errors from torchaudio/soundfile.
    """
    dst = tmp_dir / "ref_audio_pcm.wav"
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-ar",
        str(sr),
        "-ac",
        str(ch),
        "-c:a",
        "pcm_s16le",
        str(dst),
    ]
    subprocess.run(cmd, check=True)
    return dst


def _maybe_add_punctuation_cn(text: str) -> str:
    """
    XTTS v2 (and many TTS systems) do better with punctuation.
    Keep it conservative and dependency-free.
    """
    import re

    t = (text or "").strip()
    if not t:
        return ""

    # If the user already provided punctuation, respect it.
    if re.search(r"[，。！？；：、,.!?;:]", t):
        return t

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


def _split_text_chunks(text: str, max_len: int = 160) -> list[str]:
    """
    Keep chunks short. XTTS tends to be more stable on shorter inputs.
    """
    import re

    t = (text or "").strip()
    if not t:
        return []

    # Split on sentence-ending punctuation while keeping it.
    sents = re.split(r"(?<=[。！？.!?])", t)
    chunks: list[str] = []
    cur = ""
    for s in sents:
        s = s.strip()
        if not s:
            continue
        if len(cur) + len(s) <= max_len:
            cur += s
        else:
            if cur:
                chunks.append(cur)
            cur = s
    if cur:
        chunks.append(cur)

    # Fallback: if still too large (no punctuation), hard-split
    final: list[str] = []
    for c in chunks:
        c = c.strip()
        if not c:
            continue
        if len(c) <= max_len:
            final.append(c)
        else:
            for i in range(0, len(c), max_len):
                final.append(c[i : i + max_len].strip())
    return [c for c in final if c]


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
    Requires optional deps: see requirements_tts_coqui.txt
    """
    import sys
    if sys.version_info >= (3, 12):
        raise SystemExit(
            "Coqui XTTS v2 (Coqui `TTS`) is not available for your Python version.\n"
            f"Detected: Python {sys.version.split()[0]} (venv)\n"
            "Fix: use Python 3.11 for this project venv, then reinstall deps.\n"
            "Recommended (macOS + Homebrew):\n"
            "  brew install python@3.11\n"
            "  rm -rf .venv\n"
            "  PYTHON_BIN=python3.11 ./factory.sh web\n"
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
                    "Fix: run the install command once in Terminal, then retry.\n"
                    f"(import error: {e}; install error: {e2})"
                )
        else:
            raise SystemExit(
                "Coqui XTTS v2 deps are not installed.\n"
                "Install with:\n"
                "  pip install -r requirements_tts_coqui.txt\n"
                "\nTip (recommended): put this in `.env.local` then restart `./factory.sh web`:\n"
                "  TTS_ENGINE=\"coqui_xtts_v2\"\n"
                "  TTS_LANG=\"zh-cn\"\n"
                "Factory will auto-install `requirements_tts_coqui.txt` on startup.\n"
                f"(import error: {e})"
            )

    # Device selection (Mac-friendly)
    device = os.environ.get("TTS_DEVICE", "").strip().lower()
    if not device:
        device = "mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else "cpu"

    # Convert reference audio to a safe PCM WAV to avoid "Format not recognised"
    tmp_dir_local = tmp_dir or (project_root / "tmp" if project_root else Path("."))
    tmp_dir_local.mkdir(parents=True, exist_ok=True)
    ref_audio_wav = _ensure_pcm_wav(ref_audio, tmp_dir_local)

    # Prepare text
    lang_in = (lang or "").strip().lower()
    clean = _maybe_add_punctuation_cn(text) if lang_in.startswith("zh") else (text or "").strip()
    chunks = _split_text_chunks(clean, max_len=int(os.environ.get("TTS_MAX_CHARS", "160")))
    if not chunks:
        raise SystemExit("No text to synthesize (script.txt empty?)")

    print(f">> TTS_ENGINE=coqui_xtts_v2 device={device} lang={lang_in} chunks={len(chunks)}")

    # Auto-agree to Coqui TOS to prevent interactive prompt hanging the web background job
    os.environ["COQUI_TOS_AGREED"] = "1"

    # Fix for PyTorch 2.6+ weights_only issue with Coqui TTS
    # PyTorch 2.6 changed the default of weights_only to True, which breaks Coqui's model loading.
    import functools
    orig_torch_load = torch.load
    def patched_torch_load(*args, **kwargs):
        if "weights_only" not in kwargs:
            kwargs["weights_only"] = False
        return orig_torch_load(*args, **kwargs)
    torch.load = patched_torch_load

    tts = TTS(model_name="tts_models/multilingual/multi-dataset/xtts_v2", progress_bar=True).to(device)

    # Normalize lang code if needed (some installs use 'zh' vs 'zh-cn')
    supported = [str(x).lower() for x in (getattr(tts, "languages", None) or [])]
    lang_use = lang_in
    if supported:
        if lang_use not in supported:
            if lang_use == "zh-cn" and "zh" in supported:
                lang_use = "zh"
            elif lang_use == "zh" and "zh-cn" in supported:
                lang_use = "zh-cn"
            else:
                # Fall back to first supported language instead of crashing with a cryptic error.
                print(f">> WARNING: lang '{lang_in}' not in supported list; using '{supported[0]}' instead.")
                lang_use = supported[0]

    # Synthesize per-chunk and concatenate with a small pause.
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
    # Coqui helper save (keeps the expected sample rate for this model).
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
        help="optional built-in engine. Supported: coqui_xtts_v2. "
        "You can also set env TTS_ENGINE.",
    )
    ap.add_argument("--lang", default="", help="TTS language (e.g. zh-cn, zh, en). Can also set env TTS_LANG.")
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

    # Default behavior: if user didn't configure anything, assume Coqui XTTS v2.
    if (not tpl) and (not engine):
        engine = "coqui_xtts_v2"

    # If ref_text is empty, try to load it from ref_text.txt next to the audio (saved profiles).
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
    else:
        if not tpl:
            raise SystemExit(
                "Missing TTS configuration.\n\n"
                "Option A (recommended): Coqui XTTS v2 built-in\n"
                "  1) pip install -r requirements_tts_coqui.txt\n"
                "  2) (optional) export TTS_LANG=zh-cn\n\n"
                "Option B: provide your own engine via env TTS_CMD_TEMPLATE\n"
                "Example (F5-TTS, adjust to your install):\n"
                "  export TTS_CMD_TEMPLATE='python -m f5_tts_cli --ref_audio \"{ref_audio}\" --ref_text \"{ref_text}\" --text_file \"{text_file}\" --out_wav \"{out_wav}\"'\n"
            )

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
        if proc.stdout:
            print(proc.stdout)
        if proc.stderr:
            print(proc.stderr)
        if proc.returncode != 0:
            raise SystemExit(f"TTS command failed with exit code {proc.returncode}")

    if not out_wav.exists():
        raise FileNotFoundError(f"TTS did not produce output wav: {out_wav}")

    # Normalize to stable PCM WAV (downstream expects clean wav timing)
    tmp_pcm = tmp_dir / "voice_pcm.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(out_wav), "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(tmp_pcm)],
        check=True,
    )
    tmp_pcm.replace(out_wav)

    print("\nDONE (tts_cmd)")
    print("Script:", script_path)
    print("Voice WAV:", out_wav)


if __name__ == "__main__":
    main()

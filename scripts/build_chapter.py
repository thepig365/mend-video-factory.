#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Mend Video Factory - Chapter Builder (Mac-friendly)

Expected inputs:
  chapters/<CH>/voice.wav
  chapters/<CH>/images/*.jpg|png
  chapters/<CH>/script.txt          (optional but recommended for good subtitles)
  assets/bgm.wav                    (or pass --bgm)

Outputs:
  out/<CH>/Mend_Chapter<CH>_1080p.mp4
  out/<CH>/Mend_Chapter<CH>.srt

Key features:
- Duration follows narration (voice.wav), with optional minimum length.
- Auto-loops images to maintain comfortable pacing for long chapters.
- Creates Ken Burns motion from still images.
- Mixes voice + looping bgm with ducking (sidechain compression).
- Generates subtitles from script.txt (better than ASR), fixes unicode escapes like \\uXXXX.
- Burns subtitles with a Chinese-capable font on macOS.
"""

import argparse
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from pydub import AudioSegment  # type: ignore[import-not-found]
from pydub.silence import detect_nonsilent  # type: ignore[import-not-found]


# ----------------------------
# Utilities
# ----------------------------

def run(cmd: List[str], env: Optional[dict[str, str]] = None) -> None:
    print(">>", " ".join(cmd))
    subprocess.run(cmd, check=True, env=env)


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def ensure_empty_dir(p: Path) -> None:
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True, exist_ok=True)

def find_assets_chapter_music(root: Path, chapter: str) -> Optional[Path]:
    """
    Look for per-chapter music in assets, using a simple naming convention:
      - assets/music 01.wav
      - assets/music_01.mp3
      - assets/music/music 01.m4a
    Returns the first match (sorted) or None.
    """
    ch = str(chapter).strip()
    if not ch:
        return None

    candidates_dirs = [
        root / "assets",
        root / "assets" / "music",
    ]
    # Common separators users might use
    stems = [
        f"music {ch}",
        f"music_{ch}",
        f"music-{ch}",
        f"music{ch}",
    ]
    # Common audio extensions ffmpeg can read
    exts = ["wav", "mp3", "m4a", "aac", "flac", "ogg", "opus"]

    matches: List[Path] = []
    for d in candidates_dirs:
        if not d.exists():
            continue
        for stem in stems:
            for ext in exts:
                p = d / f"{stem}.{ext}"
                if p.exists():
                    matches.append(p)

    if not matches:
        return None
    return sorted(matches, key=lambda p: p.name.lower())[0]


def read_audio_duration_sec(audio_path: Path) -> float:
    audio = AudioSegment.from_file(str(audio_path))
    return len(audio) / 1000.0


def ensure_pcm_wav(
    src_audio: Path,
    dst_wav: Path,
    *,
    sample_rate: Optional[int] = None,
    channels: Optional[int] = None,
) -> Path:
    """
    Ensure audio is a *real* PCM WAV on disk, regardless of the file extension.

    Why this exists:
    - Users sometimes provide MP3 audio named *.wav (we've seen ffmpeg detect 'mp3' from voice.wav).
    - MP3 can have encoder delay / padding, and ffmpeg may print "Estimating duration from bitrate",
      both of which can cause subtitle timing to drift / feel "ahead".
    - Converting once up-front makes timing + mixing stable.
    """
    if not src_audio.exists():
        raise FileNotFoundError(src_audio)

    dst_wav.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["ffmpeg", "-y", "-i", str(src_audio)]
    if sample_rate is not None:
        cmd += ["-ar", str(int(sample_rate))]
    if channels is not None:
        cmd += ["-ac", str(int(channels))]
    # 16-bit PCM WAV is widely compatible and stable for timing.
    cmd += ["-vn", "-c:a", "pcm_s16le", str(dst_wav)]
    run(cmd)
    return dst_wav


def ffprobe_duration_sec(media_path: Path) -> float:
    """
    Return duration seconds for audio/video using ffprobe.
    More reliable than parsing ffmpeg output.
    """
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {media_path}:\n{proc.stderr.strip()}")
    s = (proc.stdout or "").strip()
    if not s:
        raise RuntimeError(f"ffprobe returned empty duration for {media_path}")
    return float(s)


def collect_video_clips(clips_dir: Path) -> List[Path]:
    """
    Collect MP4 clips in a stable order.
    Expected filenames like clip_001.mp4, clip_002.mp4, ... but we also support any *.mp4.
    """
    if not clips_dir.exists():
        return []
    clips = [p for p in clips_dir.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"]

    def natural_key(p: Path) -> tuple:
        name = p.stem
        nums: List[int] = []
        buf = ""
        for ch in name:
            if ch.isdigit():
                buf += ch
            else:
                if buf:
                    nums.append(int(buf))
                    buf = ""
        if buf:
            nums.append(int(buf))
        return (nums, p.name.lower())

    return sorted(clips, key=natural_key)


def normalize_clip_for_concat(
    src_mp4: Path,
    out_mp4: Path,
    *,
    target_w: int = 1920,
    target_h: int = 1080,
    fps: int = 30,
    trim_sec: Optional[float] = None,
) -> None:
    """
    Normalize a clip so all clips can be concatenated reliably:
    - fixed resolution
    - fixed fps
    - h264/yuv420p
    - no audio (we mix narration + bgm later)
    Optionally trim to trim_sec.
    """
    vf = f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h},fps={fps}"
    cmd = ["ffmpeg", "-y", "-i", str(src_mp4)]
    if trim_sec is not None and trim_sec > 0:
        cmd += ["-t", f"{float(trim_sec):.3f}"]
    cmd += [
        "-vf",
        vf,
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        str(out_mp4),
    ]
    run(cmd)


def detect_and_read_text(path: Path) -> str:
    """
    Read a text file robustly:
    - try utf-8-sig, utf-8
    - try utf-16 (common if copied from some editors)
    - try latin-1 as last resort (won't be correct Chinese, but prevents crash)
    """
    raw = path.read_bytes()

    for enc in ("utf-8-sig", "utf-8", "utf-16", "utf-16le", "utf-16be"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass

    # last resort
    return raw.decode("latin-1", errors="replace")


def normalize_script_text(text: str) -> str:
    """
    Fix common issues:
    - Normalize newlines
    - Convert escaped unicode sequences like \\u23433 to real characters
      (your screenshot shows exactly this issue)
    - Trim
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # If the text literally contains "\uXXXX", decode it.
    # Example: "\\u23433\\u38745" -> "安静"
    if "\\u" in text:
        try:
            text = text.encode("utf-8").decode("unicode_escape")
        except Exception:
            # If decoding fails, keep original rather than crashing.
            pass

    return text.strip()


def fmt_srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ----------------------------
# Subtitle generation
# ----------------------------

def split_cn_sentences(text: str) -> List[str]:
    """
    Split Chinese text into subtitle-sized segments.
    This is intentionally simple and stable.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []

    parts: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        buf = ""
        for ch in line:
            buf += ch
            # Split on major punctuation + commas/colons for better pacing
            if ch in "。！？!?；;，,：:…":
                seg = buf.strip()
                if seg:
                    parts.append(seg)
                buf = ""

        if buf.strip():
            parts.append(buf.strip())

    # Remove empty segments
    parts = [p.strip() for p in parts if p.strip()]
    return parts


def hard_wrap_cn(seg: str, max_chars: int = 16) -> List[str]:
    """
    Wrap long Chinese segments into multiple lines by character count.
    Keeps subtitles readable.
    """
    seg = seg.strip()
    if not seg:
        return [""]

    lines = []
    start = 0
    while start < len(seg):
        lines.append(seg[start:start + max_chars])
        start += max_chars
    return lines


def hard_wrap_subtitle(seg: str, max_chars_cn: int = 16, max_chars_en: int = 42) -> List[str]:
    """
    Wrap subtitle text for readability.
    - If it contains whitespace (likely EN), wrap by approx character width.
    - Otherwise treat as CJK and wrap by CJK char count.
    """
    seg = (seg or "").strip()
    if not seg:
        return [""]
    if any(ch.isspace() for ch in seg):
        # Simple EN wrap: chunk by characters, keep spaces
        lines: List[str] = []
        s = seg
        while len(s) > max_chars_en:
            cut = s.rfind(" ", 0, max_chars_en + 1)
            if cut <= 0:
                cut = max_chars_en
            lines.append(s[:cut].strip())
            s = s[cut:].strip()
        if s:
            lines.append(s)
        return lines
    return hard_wrap_cn(seg, max_chars=max_chars_cn)


def write_srt_from_script(script_text: str, out_srt: Path, total_sec: float) -> None:
    """
    Generate SRT from script text by:
    - splitting sentences
    - allocating time proportional to character count
    - wrapping long segments to 1–2 lines
    """
    sentences = split_cn_sentences(script_text)
    if not sentences:
        sentences = [""]

    weights = [max(1, len(s)) for s in sentences]
    total_w = sum(weights)

    t = 0.0
    srt_lines: List[str] = []
    idx = 1

    for s, w in zip(sentences, weights):
        dur = (w / total_w) * total_sec
        start = t
        end = min(total_sec, t + dur)
        t = end

        # Wrap to keep it readable
        wrapped = hard_wrap_subtitle(s, max_chars_cn=16, max_chars_en=42)
        # Limit to 2 lines (YouTube style); if more, merge conservatively
        if len(wrapped) > 2:
            wrapped = ["".join(wrapped[:2]), "".join(wrapped[2:])[:16] + "…"]

        srt_lines.append(str(idx))
        srt_lines.append(f"{fmt_srt_time(start)} --> {fmt_srt_time(end)}")
        srt_lines.extend(wrapped)
        srt_lines.append("")
        idx += 1

    # IMPORTANT: write real UTF-8 characters (no ensure_ascii nonsense)
    out_srt.write_text("\n".join(srt_lines), encoding="utf-8")


def _write_srt_entries(
    entries: List[tuple[float, float, str]],
    out_srt: Path,
    total_sec: float,
    offset_sec: float = 0.0,
) -> None:
    """
    Write SRT given explicit (start_sec, end_sec, text) entries.
    Applies wrapping to keep it readable.
    """
    srt_lines: List[str] = []
    idx = 1
    for start, end, text in entries:
        start = float(start) + float(offset_sec)
        end = float(end) + float(offset_sec)
        start = max(0.0, start)
        end = max(start + 0.05, end)
        if total_sec > 0:
            start = min(start, total_sec)
            end = min(end, total_sec)

        wrapped = hard_wrap_subtitle(text, max_chars_cn=16, max_chars_en=42)
        if len(wrapped) > 2:
            wrapped = ["".join(wrapped[:2]), "".join(wrapped[2:])[:16] + "…"]

        srt_lines.append(str(idx))
        srt_lines.append(f"{fmt_srt_time(start)} --> {fmt_srt_time(end)}")
        srt_lines.extend(wrapped)
        srt_lines.append("")
        idx += 1

    out_srt.write_text("\n".join(srt_lines), encoding="utf-8")


def segment_voice_by_silence(
    voice_path: Path,
    total_sec: float,
    min_silence_len_ms: int = 320,
    silence_offset_db: float = 20.0,
    pad_start_ms: int = 30,
    pad_end_ms: int = 320,
) -> List[tuple[float, float]]:
    """
    Return a list of (start_sec, end_sec) speech segments based on silence detection.
    This is a lightweight alignment method that usually matches narration pacing
    much better than evenly distributing text over total duration.
    """
    audio = AudioSegment.from_file(str(voice_path))
    total_ms = int(round(total_sec * 1000))
    audio = audio[:total_ms]

    def detect(offset_db: float) -> List[List[int]]:
        # Silence threshold relative to average loudness
        # (more negative => more sensitive, finds more speech segments)
        silence_thresh = float(audio.dBFS) - float(offset_db)
        return detect_nonsilent(audio, min_silence_len=min_silence_len_ms, silence_thresh=silence_thresh)

    # Auto-tune sensitivity: if we detect too little speech (often due to quieter narration),
    # increase offset_db to be more sensitive until speech coverage is reasonable.
    offset_db = float(silence_offset_db)
    nonsilent = detect(offset_db)
    # Heuristics tuned for narration tracks:
    # - We prefer to slightly over-detect speech rather than miss quiet words (missing words causes drift).
    # - If the first detected speech starts very late, we're likely missing a quiet intro.
    # More conservative targets (too aggressive detection causes subtitles to "run ahead")
    # NOTE: We intentionally do NOT force "first speech must start within X seconds".
    # Some narrations have a longer intentional intro silence; chasing that makes detection
    # too sensitive, and then subtitles can run ahead.
    target_speech_ratio = 0.62
    max_offset_db = 34.0

    for _ in range(8):
        if not nonsilent:
            speech_ms = 0
            # first_start_ms is not used for auto-tune decisions anymore, but keep it for debug/readability
            first_start_ms = total_ms
        else:
            speech_ms = sum(max(0, e - s) for s, e in nonsilent)
            first_start_ms = nonsilent[0][0]

        speech_ratio = speech_ms / max(1, total_ms)
        if speech_ratio >= target_speech_ratio:
            break
        if offset_db >= max_offset_db:
            break

        offset_db = min(max_offset_db, offset_db + 2.0)
        nonsilent = detect(offset_db)
    if not nonsilent:
        return [(0.0, total_sec)]

    # Pad segments a bit so subtitles don't feel late/early.
    segs: List[tuple[int, int]] = []
    for s_ms, e_ms in nonsilent:
        s_ms = max(0, s_ms - pad_start_ms)
        e_ms = min(total_ms, e_ms + pad_end_ms)
        if e_ms > s_ms:
            segs.append((s_ms, e_ms))

    # Merge segments with tiny gaps (treat them as one phrase)
    merged: List[tuple[int, int]] = []
    for s_ms, e_ms in segs:
        if not merged:
            merged.append((s_ms, e_ms))
            continue
        ps, pe = merged[-1]
        if s_ms - pe <= 320:  # gap <= 320ms
            merged[-1] = (ps, max(pe, e_ms))
        else:
            merged.append((s_ms, e_ms))

    return [(s / 1000.0, e / 1000.0) for s, e in merged]


def write_srt_from_script_silence_aligned(
    script_text: str,
    voice_path: Path,
    out_srt: Path,
    total_sec: float,
    offset_sec: float = 0.0,
) -> None:
    """
    Better default subtitle timing:
    - split the script into sentences
    - detect narration "speech segments" from audio via silence detection
    - allocate groups of speech segments to each sentence (in order)
    This keeps text much closer to what is being spoken than uniform/proportional timing.
    """
    sentences = split_cn_sentences(script_text)
    if not sentences:
        sentences = [""]

    segs = segment_voice_by_silence(voice_path, total_sec=total_sec)
    if not segs:
        write_srt_from_script(script_text, out_srt, total_sec)
        return

    # Total speech duration (excluding long silences) is what we distribute across.
    seg_durs = [max(0.01, e - s) for s, e in segs]
    total_speech = sum(seg_durs)
    weights = [max(1, len(s)) for s in sentences]
    total_w = sum(weights)

    entries: List[tuple[float, float, str]] = []

    # Case A: many speech segments -> group them into N subtitle ranges
    if len(segs) >= len(sentences):
        # Build cumulative timeline in speech-duration space, then cut into weighted buckets.
        cum = [0.0]
        for d in seg_durs:
            cum.append(cum[-1] + d)

        def seg_index_for_speech_time(t: float) -> int:
            # smallest i s.t. cum[i+1] >= t
            for i in range(len(seg_durs)):
                if cum[i + 1] >= t:
                    return i
            return len(seg_durs) - 1

        start_seg_idx = 0
        speech_t = 0.0
        for sent, w in zip(sentences, weights):
            speech_t += (w / total_w) * total_speech
            end_seg_idx = max(start_seg_idx, seg_index_for_speech_time(speech_t))

            s0 = segs[start_seg_idx][0]
            e0 = segs[end_seg_idx][1]
            entries.append((s0, e0, sent))

            start_seg_idx = end_seg_idx + 1
            if start_seg_idx >= len(segs):
                break

        # If we ran out of sentences early, append remaining segments to last line.
        if start_seg_idx < len(segs) and entries:
            s_last, _, t_last = entries[-1]
            entries[-1] = (s_last, segs[-1][1], t_last)

    # Case B: fewer speech segments than sentences -> split segments by weighted time
    else:
        # Flatten segments into a continuous timeline but only within speech regions.
        # We carve durations within segments and map them back onto absolute time.
        seg_i = 0
        cur_t = segs[0][0]
        cur_end = segs[0][1]

        for sent, w in zip(sentences, weights):
            want = (w / total_w) * total_speech
            start = cur_t
            remaining = want

            while remaining > 1e-6:
                available = max(0.0, cur_end - cur_t)
                if available <= 1e-6:
                    seg_i += 1
                    if seg_i >= len(segs):
                        cur_t = total_sec
                        cur_end = total_sec
                        break
                    cur_t, cur_end = segs[seg_i]
                    if start is None:
                        start = cur_t
                    continue

                take = min(available, remaining)
                cur_t += take
                remaining -= take

                if remaining <= 1e-6:
                    break

                # move to next speech segment
                seg_i += 1
                if seg_i >= len(segs):
                    break
                cur_t, cur_end = segs[seg_i]

            end = max(start + 0.2, cur_t)
            entries.append((start, min(total_sec, end), sent))

        # Ensure last subtitle reaches end if we're close
        if entries:
            s_last, e_last, t_last = entries[-1]
            entries[-1] = (s_last, max(e_last, min(total_sec, segs[-1][1])), t_last)

    _write_srt_entries(entries, out_srt, total_sec=total_sec, offset_sec=offset_sec)


def write_srt_from_whisper(
    voice_path: Path,
    out_srt: Path,
    *,
    total_sec: float,
    offset_sec: float = 0.0,
    model_size: str = "small",
    language: str = "auto",
) -> None:
    """
    Generate SRT from the actual speech using Whisper (via faster-whisper).
    This is the most robust way to make subtitles match narration when the script differs.
    """
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except Exception as e:
        raise RuntimeError(
            "Missing STT dependency. Install with:\n"
            "  pip install -r requirements_stt.txt\n"
            f"(import error: {e})"
        )

    lang = None if (language or "").strip().lower() in {"", "auto"} else (language or "").strip()
    model = WhisperModel(str(model_size), device="cpu", compute_type="int8")
    segments, _info = model.transcribe(str(voice_path), language=lang, beam_size=5, vad_filter=True)

    entries: List[tuple[float, float, str]] = []
    for seg in segments:
        txt = (getattr(seg, "text", "") or "").strip()
        if not txt:
            continue
        start = float(getattr(seg, "start", 0.0))
        end = float(getattr(seg, "end", start + 0.2))
        entries.append((start, end, txt))

    if not entries:
        entries = [(0.0, max(0.2, float(total_sec)), "")]
    _write_srt_entries(entries, out_srt, total_sec=total_sec, offset_sec=offset_sec)


# ----------------------------
# Video building
# ----------------------------

def collect_images(images_dir: Path) -> List[Path]:
    allowed = {".jpg", ".jpeg", ".png", ".webp"}
    imgs = [p for p in images_dir.iterdir() if p.is_file() and p.suffix.lower() in allowed]

    def natural_key(p: Path) -> tuple:
        # Sort by any numbers in the filename so 01_10.JPG comes after 01_09.jpg
        name = p.stem
        nums: List[int] = []
        buf = ""
        for ch in name:
            if ch.isdigit():
                buf += ch
            else:
                if buf:
                    nums.append(int(buf))
                    buf = ""
        if buf:
            nums.append(int(buf))
        return (nums, p.name.lower())

    return sorted(imgs, key=natural_key)


def is_probably_heif_container(image_path: Path) -> bool:
    """
    Heuristic: iPhone exports sometimes end up as HEIF/HEIC but with a .jpg extension.
    Those are ISO-BMFF files and typically start with: ????ftyp....
    """
    if image_path.suffix.lower() in {".heic", ".heif", ".avif"}:
        return True
    try:
        head = image_path.read_bytes()[:64]
    except Exception:
        return False
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brands = {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"avif"}
        if head[8:12] in brands:
            return True
        if any(b in head for b in brands):
            return True
    return False


def normalize_still_image(image_path: Path, normalized_dir: Path) -> Path:
    """
    Ensure `ffmpeg -loop 1 -i <image>` can read the file reliably.
    If the file is HEIF/HEIC-ish, convert to a real JPEG via macOS `sips`.
    """
    if not is_probably_heif_container(image_path):
        return image_path

    ensure_dir(normalized_dir)
    out_jpg = normalized_dir / f"{image_path.stem}_norm.jpg"
    run(["sips", "-s", "format", "jpeg", str(image_path), "--out", str(out_jpg)])
    return out_jpg


def build_image_style_vf(image_style: str, strength: float) -> str:
    """
    Optional "artistic" look for still images.

    Implemented as ffmpeg filters so we don't need extra Python deps.
    `strength` is clamped to 0..1.
    """
    s = max(0.0, min(1.0, float(strength)))
    style = (image_style or "none").strip().lower()

    if style in {"none", "off", "0"}:
        return ""

    # Keep these conservative: good looking + unlikely to break across ffmpeg builds.
    if style == "vivid":
        contrast = 1.0 + 0.25 * s
        saturation = 1.0 + 0.35 * s
        sharp = 0.40 * s
        return (
            f"eq=contrast={contrast:.3f}:saturation={saturation:.3f},"
            f"unsharp=7:7:{sharp:.3f}:7:7:0.0"
        )

    if style == "cinematic":
        contrast = 1.0 + 0.18 * s
        saturation = 1.0 + 0.10 * s
        gamma = 1.0 - 0.06 * s
        sharp = 0.35 * s
        return (
            f"eq=contrast={contrast:.3f}:saturation={saturation:.3f}:gamma={gamma:.3f},"
            f"unsharp=7:7:{sharp:.3f}:7:7:0.0,"
            "vignette=PI/5"
        )

    if style in {"bw", "b&w", "mono", "monochrome"}:
        contrast = 1.0 + 0.25 * s
        gamma = 1.0 - 0.05 * s
        sharp = 0.30 * s
        return (
            "format=gray,"
            f"eq=contrast={contrast:.3f}:gamma={gamma:.3f},"
            f"unsharp=7:7:{sharp:.3f}:7:7:0.0"
        )

    raise ValueError(f"Unknown image_style: {image_style!r}")


def make_kenburns_clip(
    image_path: Path,
    dur_sec: float,
    out_mp4: Path,
    zoom_amount: float = 0.14,
    image_style: str = "none",
    image_strength: float = 0.7,
) -> None:
    """
    Still image -> mp4 clip with gentle Ken Burns zoom.
    Conservative settings to avoid jitter.
    """
    fps = 30
    frames = max(1, int(dur_sec * fps))

    zoom_amount = max(0.01, min(0.25, float(zoom_amount)))  # clamp 1%..25%
    zoom_max = 1.0 + zoom_amount
    style_vf = build_image_style_vf(image_style=image_style, strength=image_strength)
    vf_parts = [
        "scale=1920:1080:force_original_aspect_ratio=increase",
        "crop=1920:1080",
        style_vf,
        f"zoompan=z='min({zoom_max:.3f},1+{zoom_amount:.3f}*on/{frames})':d={frames}:s=1920x1080:fps={fps}",
    ]
    vf = ",".join([p for p in vf_parts if p])

    run([
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-t", f"{dur_sec:.3f}",
        "-vf", vf,
        "-r", str(fps),
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-preset", "veryfast",
        str(out_mp4),
    ])


def concat_clips(clips: List[Path], out_mp4: Path, tmp_dir: Path) -> None:
    concat_txt = tmp_dir / "concat.txt"
    concat_txt.write_text(
        "\n".join([f"file '{c.as_posix()}'" for c in clips]),
        encoding="utf-8"
    )

    run([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_txt),
        "-c", "copy",
        str(out_mp4),
    ])


def mix_audio_with_ducking(
    video_noaudio: Path,
    voice_wav: Path,
    bgm_wav: Path,
    out_mp4: Path,
    total_sec: float,
    bgm_volume: float = 0.22
) -> None:
    """
    Mix voice + looping bgm, with ducking under narration.
    """
    # NOTE: sidechaincompress outputs ONLY the (ducked) main input.
    # We still must mix the narration back in with amix.
    filter_complex = (
        f"[2:a]aloop=loop=-1:size=2000000000,atrim=0:{total_sec:.3f},volume={bgm_volume}[bg];"
        # Split narration: one branch for sidechain detection, one for the audible mix.
        f"[1:a]volume=1.0,asplit=2[vox][sc];"
        f"[bg][sc]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=250[bgduck];"
        f"[bgduck][vox]amix=inputs=2:duration=first:dropout_transition=0,"
        f"alimiter=limit=0.98[m]"
    )

    run([
        "ffmpeg", "-y",
        "-i", str(video_noaudio),
        "-i", str(voice_wav),
        "-i", str(bgm_wav),
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "[m]",
        "-t", f"{total_sec:.3f}",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        str(out_mp4),
    ])


def burn_subtitles_mac(
    in_mp4: Path,
    srt_path: Path,
    out_mp4: Path,
    tmp_dir: Path,
    *,
    font_size: int = 16,
) -> None:
    """
    Burn subtitles with a Chinese-capable font on macOS.
    If PingFang SC is unavailable, change FontName to:
      - Heiti SC
      - Songti SC
      - Noto Sans CJK SC (if installed)
    """
    # In sandboxed environments fontconfig often fails because it can't write its cache.
    # Point the cache to a writable directory under our workspace.
    fontcache = tmp_dir / "fontcache"
    ensure_dir(fontcache)
    env = os.environ.copy()
    env["XDG_CACHE_HOME"] = str(fontcache)

    # Copy a known-good Chinese font file into a local fonts dir (also under workspace),
    # then tell libass to load fonts from there (avoids relying on system font discovery).
    fonts_dir = tmp_dir / "fonts"
    ensure_dir(fonts_dir)
    candidates = [
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    ]
    for src in candidates:
        if src.exists():
            dst = fonts_dir / src.name
            if not dst.exists():
                shutil.copyfile(src, dst)

    # Readable styling:
    # - FontSize is intentionally user-configurable (many users prefer smaller subs).
    fs = max(10, min(72, int(font_size)))
    outline = 1 if fs <= 22 else 2
    shadow = 1
    margin_v = 50
    style = f"FontName=Hiragino Sans GB,FontSize={fs},Outline={outline},Shadow={shadow},MarginV={margin_v}"

    # Quote paths for ffmpeg filter. Avoid weird characters in project path if possible.
    sub = srt_path.as_posix().replace("'", r"\'")
    fontsdir = fonts_dir.as_posix().replace("'", r"\'")
    run([
        "ffmpeg", "-y",
        "-i", str(in_mp4),
        "-vf", f"subtitles='{sub}':fontsdir='{fontsdir}':force_style='{style}'",
        "-c:a", "copy",
        str(out_mp4),
    ], env=env)


# ----------------------------
# Main
# ----------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapter", required=True, help="章节编号，例如 01")
    ap.add_argument("--project", default=".", help="项目根目录")
    ap.add_argument("--minutes", type=int, default=0, help="最短时长（分钟）。0 表示完全跟旁白走")
    ap.add_argument(
        "--bgm",
        default="assets/bgm.wav",
        help="背景音乐路径（相对 project 根目录）。默认会优先使用 chapters/<CH>/bgm.wav（如果存在）",
    )
    ap.add_argument("--burn_subs", action="store_true", help="是否把字幕烧录进画面")
    ap.add_argument("--desired_slide_sec", type=int, default=25, help="目标每张图持续秒数（越小=切图越快；长视频会自动循环图片）")
    ap.add_argument("--bgm_volume", type=float, default=0.22, help="背景音乐音量（推荐 0.16~0.28）")
    ap.add_argument("--subs_offset_sec", type=float, default=2.85, help="字幕整体延后秒数（字幕跑太快就加大，比如 3.0）")
    ap.add_argument(
        "--subs_from",
        default="script",
        choices=["script", "whisper"],
        help="字幕来源：script（脚本+静音对齐，默认）| whisper（语音转文字，更贴合实际旁白）",
    )
    ap.add_argument("--whisper_model", default="small", help="Whisper 模型大小（faster-whisper），例如 tiny|base|small|medium|large-v3")
    ap.add_argument("--whisper_lang", default="auto", help="Whisper 语言：auto|zh|en|...（auto 推荐）")
    ap.add_argument("--sub_font_size", type=int, default=16, help="烧录字幕字号（推荐 14~24；你说 16 就用 16）")
    ap.add_argument("--kenburns_zoom", type=float, default=0.14, help="Ken Burns 缩放幅度（0.08~0.16 推荐；越大=画面移动越快）")
    ap.add_argument(
        "--image_style",
        default="none",
        choices=["none", "cinematic", "vivid", "bw"],
        help="图片美术化风格：none|cinematic|vivid|bw（默认 none 不改变图片）",
    )
    ap.add_argument(
        "--image_strength",
        type=float,
        default=0.7,
        help="美术化强度 0~1（建议 0.4~0.9）。仅在 image_style != none 时生效",
    )
    ap.add_argument(
        "--subs_only",
        action="store_true",
        help="只生成字幕（SRT），不渲染视频。用于快速调字幕时序。",
    )
    args = ap.parse_args()

    root = Path(args.project).resolve()

    ch_dir = root / "chapters" / args.chapter
    voice = ch_dir / "voice.wav"
    images_dir = ch_dir / "images"
    script_txt = ch_dir / "script.txt"
    clips_dir = ch_dir / "clips"

    if not voice.exists():
        raise FileNotFoundError(f"Missing narration: {voice}")
    # We support either:
    # - images: chapters/<CH>/images/*.{jpg,png,...}
    # - clips:  chapters/<CH>/clips/*.mp4   (e.g. Luma generated)
    # At least one must exist.
    if not images_dir.exists() and not clips_dir.exists():
        raise FileNotFoundError(f"Missing inputs: {images_dir} (images) or {clips_dir} (clips)")

    # BGM selection:
    # - If user didn't override (or kept the default), prefer per-chapter bgm.wav when present.
    # - Otherwise use the provided --bgm path.
    chapter_bgm = ch_dir / "bgm.wav"
    bgm_arg = str(args.bgm).strip()
    if (not bgm_arg) or bgm_arg == "assets/bgm.wav":
        if chapter_bgm.exists():
            bgm = chapter_bgm.resolve()
        else:
            assets_music = find_assets_chapter_music(root, args.chapter)
            bgm = assets_music.resolve() if assets_music else (root / "assets" / "bgm.wav").resolve()
    else:
        bgm = (root / bgm_arg).resolve()
    if not bgm.exists():
        raise FileNotFoundError(f"Missing bgm: {bgm}")

    # Per-chapter output folder
    out_dir = root / "out" / args.chapter
    ensure_dir(out_dir)

    tmp_dir = root / "tmp" / args.chapter
    ensure_empty_dir(tmp_dir)

    # Normalize audio inputs early (handles MP3 mislabeled as *.wav)
    # - Voice: mono is best for silence detection + is plenty for narration.
    # - BGM: keep stereo.
    voice_pcm = ensure_pcm_wav(voice, tmp_dir / "voice_pcm.wav", sample_rate=48000, channels=1)
    bgm_pcm = ensure_pcm_wav(bgm, tmp_dir / "bgm_pcm.wav", sample_rate=44100, channels=2)

    # Duration logic
    voice_sec = read_audio_duration_sec(voice_pcm)
    min_sec = max(0, args.minutes) * 60
    total_sec = max(voice_sec, min_sec)

    # Subtitles
    srt_path = out_dir / f"Mend_Chapter{args.chapter}.srt"
    if str(args.subs_from).strip().lower() == "whisper":
        write_srt_from_whisper(
            voice_pcm,
            srt_path,
            total_sec=total_sec,
            offset_sec=float(args.subs_offset_sec),
            model_size=str(args.whisper_model),
            language=str(args.whisper_lang),
        )
    elif script_txt.exists():
        raw_text = detect_and_read_text(script_txt)
        script_text = normalize_script_text(raw_text)
        # Default: silence-aligned subtitles (much closer to narration timing).
        write_srt_from_script_silence_aligned(
            script_text,
            voice_path=voice_pcm,
            out_srt=srt_path,
            total_sec=total_sec,
            offset_sec=float(args.subs_offset_sec),
        )
    else:
        # Create a minimal placeholder SRT so pipeline stays consistent
        write_srt_from_script(
            "（未提供 script.txt。建议添加旁白文本以生成高质量字幕。）",
            srt_path,
            total_sec,
        )

    if args.subs_only:
        print("\nDONE (subs_only)")
        print("SRT:", srt_path)
        print(f"Duration: {total_sec:.2f}s (voice={voice_sec:.2f}s, min={min_sec:.2f}s)")
        return

    # Images
    # If user provided clips/*.mp4, prefer them (e.g. Luma outputs).
    input_clips = collect_video_clips(clips_dir)
    clips: List[Path] = []

    if input_clips:
        # Normalize and loop clips to cover the narration duration.
        norm_dir = tmp_dir / "normalized_clips"
        ensure_dir(norm_dir)

        # First pass: measure durations
        durs = [ffprobe_duration_sec(p) for p in input_clips]
        if any(d <= 0.05 for d in durs):
            raise ValueError("One or more clips have near-zero duration; please re-export from Luma.")

        total_have = sum(durs)
        if total_have <= 0.05:
            raise ValueError("No usable clips found.")

        # Build a list of source clips (repeating) until we cover total_sec.
        src_seq: List[Path] = []
        remaining = float(total_sec)
        i = 0
        # cap repeats to avoid infinite loops in weird cases
        max_items = 5000
        while remaining > 1e-3 and len(src_seq) < max_items:
            src = input_clips[i % len(input_clips)]
            dur = durs[i % len(durs)]
            src_seq.append(src)
            remaining -= dur
            i += 1

        # Normalize all clips in sequence; trim the last one to match total_sec closely.
        t_left = float(total_sec)
        for idx, src in enumerate(src_seq, start=1):
            dur = ffprobe_duration_sec(src)
            out = norm_dir / f"clip_{idx:03d}.mp4"
            if t_left <= 0:
                break
            trim = min(dur, t_left)
            normalize_clip_for_concat(src, out, trim_sec=trim)
            clips.append(out)
            t_left -= trim

    else:
        imgs = collect_images(images_dir)
        if len(imgs) < 3:
            raise ValueError("Not enough images. Recommend 12–16 images for best pacing.")

        desired = max(10, int(args.desired_slide_sec))
        needed = max(1, int(math.ceil(total_sec / desired)))

        # Loop images to meet the needed slide count
        slides: List[Path] = [imgs[i % len(imgs)] for i in range(needed)]
        per = total_sec / len(slides)

        # Render clips
        normalized_dir = tmp_dir / "normalized_images"
        for i, img in enumerate(slides, start=1):
            img = normalize_still_image(img, normalized_dir)
            clip_path = tmp_dir / f"clip_{i:03d}.mp4"
            make_kenburns_clip(
                img,
                per,
                clip_path,
                zoom_amount=float(args.kenburns_zoom),
                image_style=str(args.image_style),
                image_strength=float(args.image_strength),
            )
            clips.append(clip_path)

    # Concatenate
    video_noaudio = tmp_dir / "video_noaudio.mp4"
    concat_clips(clips, video_noaudio, tmp_dir)

    # Mix audio
    video_audio = tmp_dir / "video_audio.mp4"
    mix_audio_with_ducking(
        video_noaudio=video_noaudio,
        voice_wav=voice_pcm,
        bgm_wav=bgm_pcm,
        out_mp4=video_audio,
        total_sec=total_sec,
        bgm_volume=float(args.bgm_volume),
    )

    # Final
    final_mp4 = out_dir / f"Mend_Chapter{args.chapter}_1080p.mp4"
    if args.burn_subs:
        burn_subtitles_mac(
            video_audio,
            srt_path,
            final_mp4,
            tmp_dir=tmp_dir,
            font_size=int(args.sub_font_size),
        )
    else:
        shutil.copy(video_audio, final_mp4)

    print("\nDONE")
    print("Video:", final_mp4)
    print("SRT:", srt_path)
    print(f"Duration: {total_sec:.2f}s (voice={voice_sec:.2f}s, min={min_sec:.2f}s)")
    print(f"Images: source={len(imgs)}, slides_used={len(slides)}, per_slide={per:.1f}s")


if __name__ == "__main__":
    main()
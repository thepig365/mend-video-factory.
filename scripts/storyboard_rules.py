#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rule-based storyboard generator (free, no AI).

Input:
  chapters/<CH>/script.txt   (required)
  chapters/<CH>/voice.wav    (optional, for duration-based shot sizing)

Output:
  out/<CH>/storyboard.json

Goal:
  Quickly turn script text into a shot list with simple cinematic prompts.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import subprocess
from pathlib import Path
from typing import Any


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "utf-16", "utf-16le", "utf-16be"):
        try:
            return raw.decode(enc).strip()
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1", errors="replace").strip()


def _normalize(text: str) -> str:
    s = (text or "").strip()
    # normalize whitespace
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\r\n?", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s


def _looks_cjk(s: str) -> bool:
    if not s:
        return False
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    letters = sum(1 for ch in s if ch.isalpha())
    return cjk >= max(10, letters)


def split_sentences(text: str) -> list[str]:
    """
    Simple, robust sentence splitting for CN/EN mixed text.
    """
    s = _normalize(text)
    if not s:
        return []

    # Split by blank lines first (treat as strong boundaries)
    blocks = [b.strip() for b in re.split(r"\n\s*\n", s) if b.strip()]
    sentences: list[str] = []

    for b in blocks:
        buf = ""
        for ch in b:
            buf += ch
            if ch in "。！？!?；;":
                t = buf.strip()
                if t:
                    sentences.append(t)
                buf = ""
        if buf.strip():
            # split remaining by commas if very long
            tail = buf.strip()
            if len(tail) > 80:
                parts = [p.strip() for p in re.split(r"[，,：:…]", tail) if p.strip()]
                sentences.extend(parts if parts else [tail])
            else:
                sentences.append(tail)

    # cleanup
    out = []
    for x in sentences:
        x = re.sub(r"\s+", " ", x).strip()
        if x:
            out.append(x)
    return out


def split_into_beats(sentences: list[str], *, max_len: int = 32) -> list[str]:
    """
    Further split sentences into shorter "beats" for storyboard shots.
    - Split by commas / colons / ellipsis
    - If still too long, chunk by length
    """
    beats: list[str] = []
    for s in sentences:
        s = (s or "").strip()
        if not s:
            continue
        parts = [p.strip() for p in re.split(r"[，,：:…]+", s) if p.strip()]
        if not parts:
            parts = [s]
        for p in parts:
            if len(p) <= max_len:
                beats.append(p)
            else:
                # chunk long beat
                start = 0
                while start < len(p):
                    beats.append(p[start : start + max_len].strip())
                    start += max_len
    # remove empties
    return [b for b in beats if b]


def expand_beats_to_count(beats: list[str], *, target: int) -> list[str]:
    """
    If we have too few beats for the desired shot count, split the longest beats
    until we reach (or exceed) target.
    """
    target = max(1, int(target))
    beats = [b for b in beats if (b or "").strip()]
    if not beats:
        return []
    # Split longest strings by midpoint
    while len(beats) < target:
        idx = max(range(len(beats)), key=lambda i: len(beats[i]))
        s = beats[idx].strip()
        if len(s) <= 2:
            break
        mid = max(1, len(s) // 2)
        left = s[:mid].strip()
        right = s[mid:].strip()
        if not left or not right:
            break
        beats[idx : idx + 1] = [left, right]
    return beats


def ffprobe_duration_sec(media_path: Path) -> float:
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
        return 0.0
    s = (proc.stdout or "").strip()
    try:
        return float(s)
    except Exception:
        return 0.0


def pick_keywords(text: str, *, max_keywords: int = 10) -> list[str]:
    """
    Naive keyword extraction:
      - EN: top repeated words (length>=4)
      - CN: top repeated 2-3 char chunks (very naive)
    """
    t = _normalize(text)
    if not t:
        return []
    if not _looks_cjk(t):
        words = re.findall(r"[A-Za-z]{4,}", t.lower())
        freq: dict[str, int] = {}
        for w in words:
            freq[w] = freq.get(w, 0) + 1
        return [w for w, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:max_keywords]]

    # CN: count 2-gram and 3-gram, exclude very common chars
    stop = set("我们你们他们她们的是了和与在对就都而及也很再更被把将")
    chars = [c for c in t if "\u4e00" <= c <= "\u9fff" and c not in stop]
    freq2: dict[str, int] = {}
    freq3: dict[str, int] = {}
    for i in range(len(chars) - 1):
        bg = "".join(chars[i : i + 2])
        freq2[bg] = freq2.get(bg, 0) + 1
    for i in range(len(chars) - 2):
        tg = "".join(chars[i : i + 3])
        freq3[tg] = freq3.get(tg, 0) + 1

    merged: dict[str, int] = {}
    for k, v in freq3.items():
        if v >= 2:
            merged[k] = v + 2
    for k, v in freq2.items():
        if v >= 2:
            merged.setdefault(k, v)

    out = [w for w, _ in sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))[:max_keywords]]
    return out


def group_into_shots(sentences: list[str], *, target_shots: int) -> list[list[str]]:
    if not sentences:
        return []
    target_shots = max(1, int(target_shots))

    # Weighted grouping: longer beats take more "space"
    weights = [max(1, len(s)) for s in sentences]
    total_w = sum(weights)
    bucket_w = total_w / target_shots

    groups: list[list[str]] = []
    cur: list[str] = []
    cur_w = 0.0
    for s, w in zip(sentences, weights):
        cur.append(s)
        cur_w += w
        if cur_w >= bucket_w and len(groups) < target_shots - 1:
            groups.append(cur)
            cur = []
            cur_w = 0.0
    if cur:
        groups.append(cur)
    return groups


def enforce_group_count(groups: list[list[str]], *, target_shots: int) -> list[list[str]]:
    """
    Make group count exactly target_shots by splitting/merging.
    """
    target_shots = max(1, int(target_shots))
    if not groups:
        return []

    # Flatten helpers
    def group_weight(g: list[str]) -> int:
        return sum(max(1, len(x)) for x in g)

    # If too few groups: split largest groups
    while len(groups) < target_shots:
        # pick largest
        idx = max(range(len(groups)), key=lambda i: group_weight(groups[i]))
        g = groups[idx]
        if len(g) <= 1:
            # can't split further
            break
        mid = max(1, len(g) // 2)
        left = g[:mid]
        right = g[mid:]
        groups[idx : idx + 1] = [left, right]

    # If still too few (because all groups are single beats), we accept fewer shots.
    if len(groups) < target_shots:
        return groups

    # If too many groups: merge smallest neighbors
    while len(groups) > target_shots:
        # pick smallest adjacent pair cost
        best_i = 0
        best_w = 10**18
        for i in range(len(groups) - 1):
            w = group_weight(groups[i]) + group_weight(groups[i + 1])
            if w < best_w:
                best_w = w
                best_i = i
        merged = groups[best_i] + groups[best_i + 1]
        groups[best_i : best_i + 2] = [merged]

    return groups


def make_prompt(seed: int, *, lang_is_cjk: bool, shot_text: str) -> dict[str, str]:
    rng = random.Random(seed)
    camera = rng.choice(
        [
            "wide establishing shot",
            "medium shot",
            "close-up",
            "over-the-shoulder",
            "tracking shot",
            "low angle shot",
            "high angle shot",
        ]
    )
    lighting = rng.choice(["soft cinematic lighting", "golden hour", "moody low-key lighting", "diffused daylight"])
    lens = rng.choice(["35mm", "50mm", "85mm"])
    palette = rng.choice(["warm tones", "cool tones", "muted tones", "high contrast"])

    style = "cinematic, film still, shallow depth of field"
    if lang_is_cjk:
        clean = (shot_text or "").strip().rstrip("。！？!?；;，,：:…")
        base = f"画面：{clean}。镜头：{camera}，{lens}，{lighting}，{palette}。风格：{style}。"
    else:
        base = f"Scene: {shot_text}. Camera: {camera}, {lens}, {lighting}, {palette}. Style: {style}."

    return {
        "prompt_image": base,
        "prompt_video": base + (" Slow movement, 8-12 seconds." if not lang_is_cjk else " 缓慢运动，8-12秒。"),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".", help="project root")
    ap.add_argument("--chapter", required=True, help="chapter id, e.g. 01")
    ap.add_argument("--target_shot_sec", type=float, default=10.0, help="target seconds per shot (approx)")
    ap.add_argument("--seed", type=int, default=42, help="random seed for prompt variety")
    args = ap.parse_args()

    root = Path(args.project).resolve()
    ch_dir = root / "chapters" / args.chapter
    out_dir = root / "out" / args.chapter
    out_dir.mkdir(parents=True, exist_ok=True)

    script_path = ch_dir / "script.txt"
    if not script_path.exists():
        raise FileNotFoundError(f"Missing script.txt: {script_path}")

    script = _normalize(_read_text(script_path))
    sents = split_sentences(script)
    if not sents:
        raise ValueError("script.txt is empty after normalization")
    beats = split_into_beats(sents, max_len=32 if _looks_cjk(script) else 80)

    voice_path = ch_dir / "voice.wav"
    duration = ffprobe_duration_sec(voice_path) if voice_path.exists() else 0.0

    # Estimate total seconds if no audio: assume 180 chars/min for CN, 130 wpm for EN.
    if duration <= 0.05:
        if _looks_cjk(script):
            duration = max(20.0, len(script) / 3.0)  # very rough
        else:
            words = len(re.findall(r"\w+", script))
            duration = max(20.0, (words / 2.2))

    target_shots = max(1, int(math.ceil(duration / max(4.0, float(args.target_shot_sec)))))
    beats = expand_beats_to_count(beats, target=target_shots)
    groups = group_into_shots(beats, target_shots=target_shots)
    groups = enforce_group_count(groups, target_shots=target_shots)

    lang_is_cjk = _looks_cjk(script)
    shots: list[dict[str, Any]] = []
    per_shot = float(duration) / max(1, len(groups))
    t = 0.0

    for i, lines in enumerate(groups, start=1):
        text = " ".join(lines).strip()
        text = re.sub(r"[。]{2,}", "。", text)
        start = t
        end = min(duration, t + per_shot)
        t = end
        prompts = make_prompt(args.seed + i, lang_is_cjk=lang_is_cjk, shot_text=text)

        shots.append(
            {
                "id": i,
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "duration_sec": round(max(0.2, end - start), 3),
                "narration": text,
                "visual_brief": text[:120] + ("…" if len(text) > 120 else ""),
                **prompts,
            }
        )

    data = {
        "chapter": args.chapter,
        "duration_sec_est": round(float(duration), 3),
        "target_shot_sec": float(args.target_shot_sec),
        "shots": shots,
        "keywords": pick_keywords(script, max_keywords=10),
        "summary": (sents[0][:140] + ("…" if len(sents[0]) > 140 else "")),
    }

    out_path = out_dir / "storyboard.json"
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("DONE (storyboard_rules)")
    print("Storyboard:", out_path)


if __name__ == "__main__":
    main()



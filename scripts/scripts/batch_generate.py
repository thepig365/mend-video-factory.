#!/usr/bin/env python3
import argparse
import subprocess
from pathlib import Path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".", help="project root")
    ap.add_argument("--chapters", nargs="*", help="chapter list e.g. 01 02 03. If omitted, auto-detect.")
    ap.add_argument("--minutes", type=int, default=0)
    ap.add_argument("--burn_subs", action="store_true")
    args = ap.parse_args()

    root = Path(args.project).resolve()
    chapters_dir = root / "chapters"
    build = root / "scripts" / "build_chapter.py"

    if not build.exists():
        raise FileNotFoundError(build)

    if args.chapters and len(args.chapters) > 0:
        chapters = args.chapters
    else:
        chapters = sorted([p.name for p in chapters_dir.iterdir() if p.is_dir() and p.name.isdigit()])

    if not chapters:
        raise RuntimeError("No chapters found under ./chapters")

    for ch in chapters:
        print(f"\n=== GENERATING CHAPTER {ch} ===")
        cmd = [
            "python3", str(build),
            "--chapter", ch,
            "--minutes", str(args.minutes),
        ]
        if args.burn_subs:
            cmd.append("--burn_subs")

        subprocess.run(cmd, check=True)

    print("\nDONE. Check out/")

if __name__ == "__main__":
    main()
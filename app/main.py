from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
import subprocess
from pathlib import Path
import shutil

app = FastAPI()

BASE = Path(__file__).resolve().parent.parent
CHAPTERS = BASE / "chapters"
OUT = BASE / "out"

templates = Jinja2Templates(directory=str(BASE / "app/templates"))
app.mount("/static", StaticFiles(directory=str(BASE / "app/static")), name="static")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/generate")
def generate(
    chapter: str = Form(...),
    voice: UploadFile = File(...),
    script: UploadFile = File(...),
    images: list[UploadFile] = File(...),
    bgm: UploadFile | None = File(None),
    image_style: str = Form("none"),
    image_strength: float = Form(0.7),
    desired_slide_sec: int = Form(25),
    subs_offset_sec: float = Form(2.85),
    sub_font_size: int = Form(16),
):
    # Normalize chapter like "1" -> "01"
    ch = chapter.strip()
    if ch.isdigit() and len(ch) == 1:
        ch = f"0{ch}"

    ch_dir = CHAPTERS / ch
    img_dir = ch_dir / "images"

    # Clean existing chapter folder
    if ch_dir.exists():
        shutil.rmtree(ch_dir)

    img_dir.mkdir(parents=True, exist_ok=True)

    # Save voice.wav
    with open(ch_dir / "voice.wav", "wb") as f:
        f.write(voice.file.read())

    # Save script.txt
    with open(ch_dir / "script.txt", "wb") as f:
        f.write(script.file.read())

    # Save images
    for i, img in enumerate(images, start=1):
        ext = (img.filename.split(".")[-1] or "jpg").lower()
        with open(img_dir / f"{ch}_{i:02d}.{ext}", "wb") as f:
            f.write(img.file.read())

    # Optional per-chapter BGM (do NOT overwrite global assets/bgm.wav)
    if bgm and bgm.filename:
        with open(ch_dir / "bgm.wav", "wb") as f:
            f.write(bgm.file.read())

    # Run your existing chapter builder
    subprocess.run([
        "python3",
        str(BASE / "scripts" / "build_chapter.py"),
        "--project",
        str(BASE),
        "--chapter", ch,
        "--minutes", "0",
        "--desired_slide_sec",
        str(int(desired_slide_sec)),
        "--subs_offset_sec",
        str(float(subs_offset_sec)),
        "--sub_font_size",
        str(int(sub_font_size)),
        "--burn_subs",
        "--image_style",
        str(image_style),
        "--image_strength",
        str(float(image_strength)),
    ], check=True)

    video = OUT / ch / f"Mend_Chapter{ch}_1080p.mp4"
    return FileResponse(video, media_type="video/mp4", filename=video.name)

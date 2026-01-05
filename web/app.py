from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
import secrets


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = Jinja2Templates(directory=str(ROOT / "web" / "templates"))

JOBS_DIR = ROOT / "web_jobs"
CHAPTERS_DIR = ROOT / "chapters"
SCRIPTS_DIR = ROOT / "scripts"
OUT_DIR = ROOT / "out"
VOICES_DIR = ROOT / "voices"  # saved voice profiles (reference samples)

# Virtual environments for different TTS engines
VENV_DEFAULT = ROOT / ".venv"
VENV_CHATTTS = ROOT / ".venv-chattts"
VENV_SOVITS = ROOT / ".venv-sovits"


def _get_python_for_tts_engine(engine: str) -> str:
    """
    Return the correct Python interpreter path based on TTS engine.
    - ChatTTS requires torch>=2.4, transformers>=4.41 -> use .venv-chattts
    - GPT-SoVITS requires specific deps -> use .venv-sovits
    - Coqui XTTS v2 requires torch==2.1.0 -> use .venv (default)
    - F5-TTS uses the default venv
    """
    engine = (engine or "").strip().lower()
    if engine == "chat_tts":
        python = VENV_CHATTTS / "bin" / "python"
        if python.exists():
            return str(python)
        return sys.executable
    elif engine in {"gpt_sovits", "sovits"}:
        python = VENV_SOVITS / "bin" / "python"
        if python.exists():
            return str(python)
        return sys.executable
    else:
        # coqui_xtts_v2, f5_tts, or any other
        python = VENV_DEFAULT / "bin" / "python"
        if python.exists():
            return str(python)
        return sys.executable

security = HTTPBasic()


def _basic_auth(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    """
    Basic Auth for remote self-use.
    Configure via env:
      - WEB_USER
      - WEB_PASS

    If not set, auth is disabled (local dev convenience).
    """
    user = os.environ.get("WEB_USER", "").strip()
    pw = os.environ.get("WEB_PASS", "").strip()
    if not user or not pw:
        return

    ok_user = secrets.compare_digest(credentials.username or "", user)
    ok_pw = secrets.compare_digest(credentials.password or "", pw)
    if not (ok_user and ok_pw):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )


def safe_id(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return "01"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", s):
        raise ValueError("chapter id must match [A-Za-z0-9_-] and be <= 40 chars")
    return s


@dataclass
class Job:
    id: str
    chapter: str
    created_at: float = field(default_factory=time.time)
    status: str = "queued"  # queued|running|done|error
    log: str = ""
    output_mp4: Path | None = None
    output_srt: Path | None = None
    error: str | None = None


app = FastAPI(dependencies=[Depends(_basic_auth)])
_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def _append_log(job: Job, chunk: str) -> None:
    with _lock:
        # Handle \r (carriage return) for cleaner progress bars in the web UI
        if "\r" in chunk:
            parts = chunk.split("\r")
            # If the chunk ends with \r, the next line should overwrite the current last line.
            # For simplicity, we just keep the last part if there are multiple \r
            chunk = parts[-1]
            # Try to replace the last line in job.log if it doesn't end with \n
            if job.log and not job.log.endswith("\n"):
                last_newline = job.log.rfind("\n")
                if last_newline != -1:
                    job.log = job.log[:last_newline+1]
                else:
                    job.log = ""

        job.log += chunk
        if len(job.log) > 200_000:
            job.log = job.log[-200_000:]


def _ensure_empty_dir(p: Path) -> None:
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True, exist_ok=True)


def _save_upload(dst: Path, up: UploadFile) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("wb") as f:
        shutil.copyfileobj(up.file, f)


def safe_profile_name(s: str) -> str:
    s = (s or "").strip()
    if not s:
        raise ValueError("voice profile name is empty")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", s):
        raise ValueError("voice profile name must match [A-Za-z0-9_-] and be <= 40 chars")
    return s


def list_voice_profiles() -> list[str]:
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    for p in VOICES_DIR.iterdir():
        if p.is_dir():
            names.append(p.name)
    return sorted(names, key=lambda x: x.lower())


def resolve_profile_audio(profile: str) -> Path:
    """
    Return the audio file path for a saved profile.
    We accept common audio extensions; the file is stored as voices/<name>/ref.<ext>.
    """
    name = safe_profile_name(profile)
    d = VOICES_DIR / name
    if not d.exists():
        raise FileNotFoundError(f"Unknown voice profile: {name}")
    for ext in (".wav", ".m4a", ".mp3", ".aac", ".flac", ".ogg", ".opus"):
        p = d / f"ref{ext}"
        if p.exists():
            return p
    raise FileNotFoundError(f"Voice profile '{name}' exists but has no ref audio (expected voices/{name}/ref.*)")


def _run_cmd_realtime(job: Job, cmd: list[str], *, env: dict[str, str] | None = None) -> int:
    _append_log(job, ">> " + " ".join(cmd) + "\n")
    # Use Popen to read output line by line in real-time
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1,
        universal_newlines=True,
    )
    if process.stdout:
        for line in process.stdout:
            _append_log(job, line)
    
    return_code = process.wait()
    _append_log(job, f"\n(command finished with exit code {return_code})\n")
    return return_code


def run_build_job(
    job_id: str,
    chapter: str,
    burn_subs: bool,
    minutes: int,
    desired_slide_sec: int,
    bgm_volume: float,
    subs_offset_sec: float,
    sub_font_size: int,
    subs_from: str,
    whisper_model: str,
    whisper_lang: str,
    image_style: str,
    image_strength: float,
    tts_enabled: bool,
    tts_ref_text: str,
    tts_ref_audio_path: str,
    tts_lang: str,
    tts_engine: str = "coqui_xtts_v2",
) -> None:
    job = _jobs[job_id]
    job.status = "running"

    try:
        # Optional: generate chapters/<CH>/voice.wav from script.txt using a local voice-clone TTS
        if tts_enabled:
            tts_py = SCRIPTS_DIR / "tts_cmd.py"
            if not tts_py.exists():
                raise FileNotFoundError(f"Missing: {tts_py}")
            ref_audio = Path(tts_ref_audio_path).resolve()
            if not ref_audio.exists():
                raise FileNotFoundError(f"Missing uploaded TTS reference audio: {ref_audio}")

            tts_cmd = [
                _get_python_for_tts_engine(tts_engine),
                str(tts_py),
                "--project",
                str(ROOT),
                "--chapter",
                chapter,
                "--ref_audio",
                str(ref_audio),
                "--ref_text",
                str(tts_ref_text or ""),
                "--lang",
                str(tts_lang or "zh-cn"),
                "--engine",
                str(tts_engine or "coqui_xtts_v2"),
            ]
            exit_code = _run_cmd_realtime(job, tts_cmd)
            if exit_code != 0:
                raise RuntimeError(f"tts failed with exit code {exit_code}")

        build_py = SCRIPTS_DIR / "build_chapter.py"
        if not build_py.exists():
            raise FileNotFoundError(f"Missing: {build_py}")

        cmd = [
            sys.executable,
            str(build_py),
            "--project",
            str(ROOT),
            "--chapter",
            chapter,
            "--minutes",
            str(int(minutes)),
            "--desired_slide_sec",
            str(int(desired_slide_sec)),
            "--bgm_volume",
            str(float(bgm_volume)),
            "--subs_offset_sec",
            str(float(subs_offset_sec)),
            "--sub_font_size",
            str(int(sub_font_size)),
            "--subs_from",
            str(subs_from),
            "--whisper_model",
            str(whisper_model),
            "--whisper_lang",
            str(whisper_lang),
            "--image_style",
            str(image_style),
            "--image_strength",
            str(float(image_strength)),
        ]
        # Prefer per-chapter BGM if present
        chapter_bgm = CHAPTERS_DIR / chapter / "bgm.wav"
        if chapter_bgm.exists():
            cmd.extend(["--bgm", str(chapter_bgm.resolve())])
        if burn_subs:
            cmd.append("--burn_subs")

        exit_code = _run_cmd_realtime(job, cmd)
        if exit_code != 0:
            raise RuntimeError(f"build failed with exit code {exit_code}")

        out_dir = OUT_DIR / chapter
        mp4 = out_dir / f"Mend_Chapter{chapter}_1080p.mp4"
        srt = out_dir / f"Mend_Chapter{chapter}.srt"
        if not mp4.exists():
            raise FileNotFoundError(f"Missing output: {mp4}")
        if not srt.exists():
            raise FileNotFoundError(f"Missing output: {srt}")

        job.output_mp4 = mp4
        job.output_srt = srt
        job.status = "done"

    except Exception as e:
        job.status = "error"
        job.error = str(e)


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    saved = request.query_params.get("saved", "").strip()
    return TEMPLATES.TemplateResponse(
        "index.html",
        {
            "request": request,
            "voice_profiles": list_voice_profiles(),
            "saved_profile": saved,
        },
    )


@app.post("/profiles")
def create_profile(
    profile_name: str = Form(...),
    ref_audio: UploadFile = File(...),
    ref_text: str = Form(""),
    overwrite: bool = Form(False),
) -> RedirectResponse:
    """
    Save a reusable voice profile under voices/<name>/ref.<ext>.
    Note: XTTS v2 doesn't create a permanent 'voice_id' — it uses the reference audio at synthesis time.
    This profile is simply a saved reference sample (+ optional transcript).
    """
    name = safe_profile_name(profile_name)
    if not ref_audio.filename:
        raise HTTPException(status_code=400, detail="Missing reference audio file")

    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    prof_dir = VOICES_DIR / name
    if prof_dir.exists() and not bool(overwrite):
        raise HTTPException(status_code=400, detail=f"Voice profile '{name}' already exists (enable overwrite to replace)")
    prof_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(ref_audio.filename).suffix.lower() or ".wav"
    if ext not in {".wav", ".m4a", ".mp3", ".aac", ".flac", ".ogg", ".opus"}:
        ext = ".wav"
    dst = prof_dir / f"ref{ext}"
    _save_upload(dst, ref_audio)
    (prof_dir / "ref_text.txt").write_text((ref_text or "").strip(), encoding="utf-8")

    return RedirectResponse(url=f"/?saved={name}", status_code=303)


@app.post("/jobs")
def create_job(
    background: BackgroundTasks,
    chapter: str = Form("01"),
    voice_wav: UploadFile | None = File(None),
    script_txt: UploadFile | None = File(None),
    script_txt_clone: UploadFile | None = File(None),
    script_text: str = Form(""),
    script_text_upload: str = Form(""),
    images: list[UploadFile] | None = File(None),
    video_clips: list[UploadFile] | None = File(None),
    bgm_wav: UploadFile | None = File(None),
    tts_enabled: bool = Form(False),
    tts_ref_audio: UploadFile | None = File(None),
    tts_ref_text: str = Form(""),
    tts_voice_profile: str = Form(""),
    tts_save_profile_as: str = Form(""),
    burn_subs: bool = Form(True),
    minutes: int = Form(0),
    desired_slide_sec: int = Form(25),
    bgm_volume: float = Form(0.35),
    subs_offset_sec: float = Form(2.85),
    sub_font_size: int = Form(16),
    subs_from: str = Form("script"),
    whisper_model: str = Form("small"),
    whisper_lang: str = Form("auto"),
    tts_lang: str = Form("zh-cn"),
    tts_engine: str = Form("coqui_xtts_v2"),
    image_style: str = Form("none"),
    image_strength: float = Form(0.7),
) -> RedirectResponse:
    chapter_id = safe_id(chapter)
    job_id = uuid.uuid4().hex[:12]

    # Write uploads into the repo layout expected by build_chapter.py
    ch_dir = CHAPTERS_DIR / chapter_id
    img_dir = ch_dir / "images"
    clips_dir = ch_dir / "clips"
    _ensure_empty_dir(img_dir)
    _ensure_empty_dir(clips_dir)

    # Narration audio:
    # - If TTS is enabled, voice.wav will be generated by scripts/tts_cmd.py (and overwrite any existing one).
    # - If TTS is disabled, voice.wav must be uploaded.
    if voice_wav and voice_wav.filename:
        _save_upload(ch_dir / "voice.wav", voice_wav)
    else:
        if not bool(tts_enabled):
            raise HTTPException(status_code=400, detail="Please upload voice.wav (or enable TTS)")
        # Prevent accidentally reusing an old voice.wav if user reuses the same chapter id.
        try:
            (ch_dir / "voice.wav").unlink(missing_ok=True)
        except TypeError:
            p = ch_dir / "voice.wav"
            if p.exists():
                p.unlink()

    # Script text
    if bool(tts_enabled):
        if script_txt_clone and script_txt_clone.filename:
            _save_upload(ch_dir / "script.txt", script_txt_clone)
            script_text_in = ""
        else:
            script_text_in = (script_text or "").strip()
    else:
        script_text_in = (script_text_upload or "").strip()

    if script_txt and script_txt.filename:
        _save_upload(ch_dir / "script.txt", script_txt)
    elif script_text_in:
        (ch_dir / "script.txt").write_text(script_text_in, encoding="utf-8")
    elif not (ch_dir / "script.txt").exists():
        if bool(tts_enabled):
            raise HTTPException(status_code=400, detail="TTS enabled: please upload a .txt file or paste script text")
        (ch_dir / "script.txt").write_text("", encoding="utf-8")

    if images:
        for up in images:
            if not up.filename:
                continue
            name = Path(up.filename).name
            _save_upload(img_dir / name, up)

    if video_clips:
        for up in video_clips:
            if not up.filename:
                continue
            if Path(up.filename).suffix.lower() != ".mp4":
                raise HTTPException(status_code=400, detail="Video clips must be .mp4")
            name = Path(up.filename).name
            _save_upload(clips_dir / name, up)

    # Validate at least one visual input
    has_images = any(img_dir.iterdir())
    has_clips = any(clips_dir.iterdir())
    if not has_images and not has_clips:
        raise HTTPException(status_code=400, detail="Please upload images or mp4 clips.")

    if bgm_wav and bgm_wav.filename:
        _save_upload(ch_dir / "bgm.wav", bgm_wav)

    # Optional: save reference audio for TTS
    tmp_ch_dir = ROOT / "tmp" / chapter_id
    tmp_ch_dir.mkdir(parents=True, exist_ok=True)
    tts_ref_audio_path = ""
    if bool(tts_enabled):
        profile = (tts_voice_profile or "").strip()
        save_as = (tts_save_profile_as or "").strip()

        if profile:
            try:
                ref = resolve_profile_audio(profile).resolve()
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e))
            tts_ref_audio_path = str(ref)
        else:
            if not tts_ref_audio or not tts_ref_audio.filename:
                raise HTTPException(
                    status_code=400,
                    detail="TTS enabled: select a Voice profile or upload a reference voice audio file",
                )
            sample_name = Path(tts_ref_audio.filename).name
            dst = tmp_ch_dir / f"tts_ref_{sample_name}"
            _save_upload(dst, tts_ref_audio)
            tts_ref_audio_path = str(dst.resolve())

            if save_as:
                try:
                    name = safe_profile_name(save_as)
                except Exception as e:
                    raise HTTPException(status_code=400, detail=str(e))
                prof_dir = VOICES_DIR / name
                prof_dir.mkdir(parents=True, exist_ok=True)
                ext = Path(sample_name).suffix.lower() or ".wav"
                prof_audio = prof_dir / f"ref{ext}"
                shutil.copyfile(dst, prof_audio)
                (prof_dir / "ref_text.txt").write_text(str(tts_ref_text or ""), encoding="utf-8")

    job = Job(id=job_id, chapter=chapter_id)
    with _lock:
        _jobs[job_id] = job

    background.add_task(
        run_build_job,
        job_id,
        chapter_id,
        bool(burn_subs),
        int(minutes),
        int(desired_slide_sec),
        float(bgm_volume),
        float(subs_offset_sec),
        int(sub_font_size),
        str(subs_from),
        str(whisper_model),
        str(whisper_lang),
        str(image_style),
        float(image_strength),
        bool(tts_enabled),
        str(tts_ref_text),
        str(tts_ref_audio_path),
        str(tts_lang),
        str(tts_engine),
    )

    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str) -> HTMLResponse:
    job = _jobs.get(job_id)
    if not job:
        return HTMLResponse("job not found", status_code=404)

    return TEMPLATES.TemplateResponse(
        "job.html",
        {
            "request": request,
            "job": job,
        },
    )


@app.get("/jobs/{job_id}/video")
def job_video(job_id: str) -> FileResponse:
    job = _jobs.get(job_id)
    if not job or job.status != "done" or not job.output_mp4:
        raise FileNotFoundError("video not ready")
    return FileResponse(path=str(job.output_mp4), media_type="video/mp4", filename=job.output_mp4.name)


@app.get("/jobs/{job_id}/srt")
def job_srt(job_id: str) -> FileResponse:
    job = _jobs.get(job_id)
    if not job or job.status != "done" or not job.output_srt:
        raise FileNotFoundError("srt not ready")
    return FileResponse(path=str(job.output_srt), media_type="application/x-subrip", filename=job.output_srt.name)

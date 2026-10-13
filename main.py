import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

app = FastAPI(title="Metadata Masterclass API", version="1.1.0")

# Set FRONTEND_ORIGINS on the hosting service to comma-separated exact origins,
# e.g. https://your-frontend.vercel.app,https://your-domain.com
_raw_origins = os.getenv("FRONTEND_ORIGINS", "*")
_allowed_origins = [origin.strip() for origin in _raw_origins.split(",") if origin.strip()]
# Wildcard origins must not be combined with credentialed browser requests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins or ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp"}


def run_exiftool(args: list[str]) -> str:
    """Run ExifTool without invoking a shell and return stdout."""
    try:
        result = subprocess.run(
            ["exiftool", "-charset", "filename=UTF8", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
            timeout=90,
        )
        return result.stdout
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="ExifTool is not installed on the backend.")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Image processing timed out.")
    except subprocess.CalledProcessError as exc:
        message = (exc.stderr or exc.stdout or "ExifTool failed").strip()
        raise HTTPException(status_code=422, detail=f"ExifTool could not process this file: {message[:1200]}")


def validate_upload(file: UploadFile) -> str:
    filename = Path(file.filename or "upload").name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail="Unsupported file type. Upload JPG, PNG, HEIC/HEIF, TIFF, or WEBP.",
        )
    return suffix


def save_upload(file: UploadFile, suffix: str) -> str:
    fd, path = tempfile.mkstemp(prefix="metadata-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as target:
            shutil.copyfileobj(file.file, target)
        if os.path.getsize(path) == 0:
            os.remove(path)
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")
        if os.path.getsize(path) > MAX_UPLOAD_BYTES:
            os.remove(path)
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit.",
            )
        return path
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        raise


@app.get("/")
def home():
    return {"status": "ok", "service": "Metadata Masterclass Engine", "version": app.version}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/inspect")
async def inspect_metadata(file: UploadFile = File(...)):
    suffix = validate_upload(file)
    tmp_path = save_upload(file, suffix)
    try:
        output = run_exiftool(["-json", "-G", "-a", tmp_path])
        try:
            records = json.loads(output)
        except json.JSONDecodeError:
            raise HTTPException(status_code=422, detail="ExifTool returned invalid metadata JSON.")
        metadata = records[0] if records else {}
        return {"status": "success", "metadata": metadata}
    finally:
        await file.close()
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.post("/api/process")
async def process_image(
    file: UploadFile = File(...),
    strip_all: bool = Form(False),
    strip_ai: bool = Form(False),
    custom_date: Optional[str] = Form(None),
    camera_model: Optional[str] = Form(None),
):
    suffix = validate_upload(file)
    tmp_path = save_upload(file, suffix)
    try:
        args = ["-overwrite_original"]
        if strip_all:
            args.append("-all=")
        elif strip_ai:
            # Remove common AI/software markers without changing image pixels.
            args.extend([
                "-C2PA:all=",
                "-XMP-c2pa:all=",
                "-Software=",
                "-XMP:Software=",
                "-XMP:CreatorTool=",
                "-XMP-iptcExt:DigitalSourceType=",
            ])

        if custom_date:
            # ExifTool accepts YYYY:MM:DD HH:MM:SS for AllDates.
            import re
            if not re.fullmatch(r"\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}", custom_date.strip()):
                raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY:MM:DD HH:MM:SS.")
            args.append(f"-AllDates={custom_date.strip()}")

        if camera_model and camera_model.strip():
            if len(camera_model.strip()) > 120:
                raise HTTPException(status_code=422, detail="Camera model must be 120 characters or fewer.")
            args.append(f"-Model={camera_model.strip()}")

        # Run only if at least one metadata operation was requested.
        if len(args) > 1:
            run_exiftool([*args, tmp_path])

        original_name = Path(file.filename or f"image{suffix}").name
        safe_stem = Path(original_name).stem[:100] or "image"
        output_name = f"processed_{safe_stem}{suffix}"
        media_type = file.content_type or "application/octet-stream"
        return FileResponse(
            path=tmp_path,
            filename=output_name,
            media_type=media_type,
            background=BackgroundTask(os.remove, tmp_path),
        )
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    finally:
        await file.close()

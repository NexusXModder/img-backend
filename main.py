import os
import shutil
import subprocess
import tempfile
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

app = FastAPI(title="Metadata Masterclass API")

# Vercel / Frontend Domain থেকে রিকোয়েস্ট অ্যালাউ করার জন্য CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Production-এ আপনার Frontend URL দিতে পারেন
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def run_exiftool(args: list):
    """ExifTool CLI কমান্ড রান করার লসলেস ফাংশন"""
    try:
        cmd = ["exiftool"] + args
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        raise Exception(f"ExifTool Error: {e.stderr}")

@app.post("/api/inspect")
async def inspect_metadata(file: UploadFile = File(...)):
    """ফাইলের পিক্সেলে টাচ না করে শুধু বর্তমান মেটাডাটা রিড করার API"""
    suffix = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        # ExifTool দিয়ে JSON বয়লারে মেটাডাটা নেওয়া
        import json
        output = run_exiftool(["-json", tmp_path])
        data = json.loads(output)[0]
        return JSONResponse(content={"status": "success", "metadata": data})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

@app.post("/api/process")
async def process_image(
    file: UploadFile = File(...),
    strip_all: bool = Form(False),
    strip_ai: bool = Form(False),
    custom_date: str = Form(None), # Format: YYYY:MM:DD HH:MM:SS
    camera_model: str = Form(None)
):
    """
    ১০০% Lossless Metadata Manipulation API.
    ছবি রি-কোড/কম্প্রেস করা হয় না, শুধু বাইনারি এক্সিফ ফিল্ড আপডেট হয়।
    """
    suffix = os.path.splitext(file.filename)[1]
    
    # টেম্পোরারি ফাইল ক্রিয়েট
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        exif_args = ["-overwrite_original"]

        # ১. AI & C2PA Metadata Stripping
        if strip_ai:
            exif_args.extend([
                "-C2PA:all=",
                "-XMP-c2pa:all=",
                "-Software=",
                "-XMP:Software=",
                "-XMP:CreatorTool=",
                "-XMP-iptcExt:DigitalSourceType="
            ])

        # ২. সম্পূর্ণ মেটাডাটা ক্লিন
        if strip_all:
            exif_args.append("-all=")

        # ৩. কাস্টম ডেট অ্যান্ড টাইম
        if custom_date:
            exif_args.append(f"-AllDates={custom_date}")

        # ৪. কাস্টম ক্যামেরা মডেল
        if camera_model:
            exif_args.append(f"-Model={camera_model}")

        # কোনো অপশন সিলেক্ট করা থাকলে ExifTool রান করবে
        if len(exif_args) > 1:
            exif_args.append(tmp_path)
            run_exiftool(exif_args)

        # অরিজিনাল ফরম্যাটেই ফাইল রেসপন্স পাঠানো
        media_type = file.content_type or "application/octet-stream"
        return FileResponse(
            path=tmp_path,
            filename=f"processed_{file.filename}",
            media_type=media_type,
            background=None
        )
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def home():
    return {"status": "Metadata Masterclass Engine is Live!"}

import asyncio
import json
import threading
import uuid
from pathlib import Path

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)
FRONTEND_DIR = BASE_DIR.parent / "frontend"
TOOLS_DIR = BASE_DIR.parent / "tools"
FFMPEG_LOCATION = str(TOOLS_DIR) if (TOOLS_DIR / "ffmpeg").exists() else None

app = FastAPI(title="TubeFetch - Descargador de YouTube")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

downloads: dict[str, dict] = {}

QUALITY_FORMATS = {
    "best": "bestvideo*+bestaudio/best",
    "2160": "bestvideo[height<=2160]+bestaudio/best[height<=2160]/best",
    "1440": "bestvideo[height<=1440]+bestaudio/best[height<=1440]/best",
    "1080": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
    "720": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
    "480": "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
    "360": "bestvideo[height<=360]+bestaudio/best[height<=360]/best",
    "audio": "bestaudio/best",
}


class InfoRequest(BaseModel):
    url: str


class DownloadRequest(BaseModel):
    url: str
    quality: str = "best"


@app.post("/api/info")
def get_info(req: InfoRequest):
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    heights = sorted(
        {
            f["height"]
            for f in info.get("formats", [])
            if f.get("vcodec") and f["vcodec"] != "none" and f.get("height")
        },
        reverse=True,
    )
    has_audio_only = any(
        f.get("acodec") and f["acodec"] != "none"
        and (not f.get("vcodec") or f["vcodec"] == "none")
        for f in info.get("formats", [])
    )

    thumb = None
    if isinstance(info.get("thumbnail"), str) and info.get("thumbnail"):
        thumb = info["thumbnail"]
    elif info.get("thumbnails"):
        thumbs = [t for t in info["thumbnails"] if t.get("url")]
        if thumbs:
            thumb = thumbs[-1]["url"]

    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration": info.get("duration"),
        "thumbnail": thumb,
        "heights": heights,
        "has_audio_only": has_audio_only,
        "url": req.url,
    }


def _progress_hook(state: dict, d: dict):
    status = d.get("status")
    if status == "downloading":
        state["status"] = "downloading"
        title = d.get("info_dict", {}).get("title")
        if title:
            state["title"] = title
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        state["progress"] = (done / total * 100) if total else 0.0
        state["speed"] = d.get("_speed_str", "") or ""
        state["eta"] = d.get("_eta_str", "") or ""
    elif status == "finished":
        state["status"] = "processing"
        state["progress"] = 100.0


def _run_download(dl_id: str, url: str, quality: str):
    state = downloads[dl_id]
    is_audio = quality == "audio"
    try:
        opts = {
            "outtmpl": str(DOWNLOADS_DIR / "%(title)s [%(id)s].%(ext)s"),
            "format": QUALITY_FORMATS.get(quality, QUALITY_FORMATS["best"]),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "progress_hooks": [lambda d, s=state: _progress_hook(s, d)],
        }
        if FFMPEG_LOCATION:
            opts["ffmpeg_location"] = FFMPEG_LOCATION
        if is_audio:
            opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
            ]
        else:
            opts["merge_output_format"] = "mp4"

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            state["title"] = info.get("title", state["title"])
            path = Path(ydl.prepare_filename(info))
            if path.suffix in (".webm", ".m4a", ".mkv", ".opus", ".mp4"):
                path = path.with_suffix(".mp4" if not is_audio else ".mp3")
            state["filename"] = path.name
        state["status"] = "done"
        state["progress"] = 100.0
    except Exception as exc:
        state["status"] = "error"
        state["error"] = str(exc)


@app.post("/api/download")
def start_download(req: DownloadRequest):
    dl_id = uuid.uuid4().hex[:8]
    state = {
        "id": dl_id,
        "status": "pending",
        "title": "",
        "filename": "",
        "progress": 0.0,
        "speed": "",
        "eta": "",
        "error": "",
    }
    downloads[dl_id] = state
    threading.Thread(target=_run_download, args=(dl_id, req.url, req.quality), daemon=True).start()
    return {"id": dl_id}


@app.get("/api/progress/{dl_id}")
async def progress_stream(dl_id: str):
    async def gen():
        try:
            while True:
                state = downloads.get(dl_id)
                if state is None:
                    yield 'event: error\ndata: {"error": "not found"}\n\n'
                    return
                yield f"data: {json.dumps(state, ensure_ascii=False)}\n\n"
                if state["status"] in ("done", "error"):
                    return
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/downloads")
def list_downloads():
    items = []
    for f in DOWNLOADS_DIR.iterdir():
        if f.is_file() and not f.name.endswith(".part"):
            items.append({"name": f.name, "size": f.stat().st_size})
    items.sort(key=lambda x: x["name"], reverse=True)
    return items


@app.get("/api/file/{name}")
def get_file(name: str):
    path = (DOWNLOADS_DIR / name).resolve()
    if DOWNLOADS_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    return FileResponse(path, filename=path.name)


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

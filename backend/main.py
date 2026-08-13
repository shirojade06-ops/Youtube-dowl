import asyncio
import json
import shutil
import threading
import uuid
from pathlib import Path

import yt_dlp
from fastapi import FastAPI, HTTPException, Request
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
COOKIES_FILE = BASE_DIR / "cookies.txt"


def _cookies_active() -> bool:
    return COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 0


def _cookies_opt(opts: dict) -> dict:
    if _cookies_active():
        opts["cookiefile"] = str(COOKIES_FILE)
    return opts

app = FastAPI(title="TubeFetch - Descargador de YouTube")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

downloads: dict[str, dict] = {}

DOWNLOAD_PROFILES = [
    {"impersonate": "chrome"},
    {"extractor_args": {"youtube": {"player_client": ["tv", "android"]}}},
    {"extractor_args": {"youtube": {"player_client": ["web_embedded"]}}},
    {"extractor_args": {"youtube": {"player_client": ["ios"]}}},
]


def _friendly_error(msg: str) -> str:
    low = msg.lower()
    if "403" in msg or "forbidden" in low:
        return "YouTube bloqueó la descarga desde este servidor (error 403). Suele ser temporal; intenta de nuevo en unos minutos o prueba otro video."
    if "ffmpeg is not installed" in low:
        return "Falta ffmpeg para combinar video y audio. Prueba con una calidad más baja o solo audio."
    if "private video" in low or "sign in" in low and "to confirm" in low:
        return "El video es privado o requiere iniciar sesión para verse."
    if "age" in low and ("restrict" in low or "confirm" in low):
        return "El video tiene restricción de edad y no puede descargarse desde el servidor."
    if "unavailable" in low or "not available" in low:
        return "El video no está disponible (fue eliminado, es una transmisión en vivo o está restringido)."
    if "po token" in low or "bot" in low:
        return "YouTube detectó actividad automatizada (PO Token / verificación de bot). Intenta de nuevo más tarde."
    return msg


class InfoRequest(BaseModel):
    url: str


class DownloadRequest(BaseModel):
    url: str
    quality: str = "best"


@app.post("/api/info")
def get_info(req: InfoRequest):
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    _cookies_opt(opts)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if info.get("_type") == "playlist" and info.get("entries"):
        info = info["entries"][0]

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


def _format_selectors(quality: str) -> list[str]:
    if quality == "audio":
        return ["bestaudio/best"]
    if quality == "best":
        return ["bestvideo*+bestaudio/best", "best"]
    h = int(quality) if quality.isdigit() else 1080
    return [
        f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best",
        f"best[height<={h}]/best",
    ]


def _run_download(dl_id: str, url: str, quality: str):
    state = downloads[dl_id]
    is_audio = quality == "audio"
    ffmpeg_ok = bool(FFMPEG_LOCATION) or shutil.which("ffmpeg") is not None

    selectors = _format_selectors(quality)
    if not ffmpeg_ok and not is_audio:
        selectors = [s for s in selectors if "+bestaudio" not in s]

    errors: list[str] = []
    for profile in DOWNLOAD_PROFILES:
        for fmt in selectors:
            if state["status"] == "error":
                return
            state["status"] = "downloading"
            state["progress"] = 0.0
            state["speed"] = ""
            state["eta"] = ""
            state["attempt"] = len(errors) + 1
            opts = {
                "outtmpl": str(DOWNLOADS_DIR / "%(title)s [%(id)s].%(ext)s"),
                "format": fmt,
                "quiet": True,
                "no_warnings": True,
                "noprogress": True,
                "noplaylist": True,
                "socket_timeout": 30,
                "retries": 2,
                "fragment_retries": 2,
                "extractor_retries": 1,
                "progress_hooks": [lambda d, s=state: _progress_hook(s, d)],
                **profile,
            }
            if FFMPEG_LOCATION:
                opts["ffmpeg_location"] = FFMPEG_LOCATION
            _cookies_opt(opts)
            if is_audio:
                opts["postprocessors"] = [
                    {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
                ]
            else:
                opts["merge_output_format"] = "mp4"

            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                if info.get("_type") == "playlist" and info.get("entries"):
                    info = info["entries"][0]
                state["title"] = info.get("title", state["title"])
                path = Path(ydl.prepare_filename(info))
                if path.suffix in (".webm", ".m4a", ".mkv", ".opus", ".mp4"):
                    path = path.with_suffix(".mp4" if not is_audio else ".mp3")
                state["filename"] = path.name
                state["status"] = "done"
                state["progress"] = 100.0
                return
            except Exception as exc:
                err = str(exc)
                errors.append(err)
                low = err.lower()
                if "ffmpeg is not installed" in low:
                    break
                if "unable to download video data" in low and "403" not in err:
                    break
                if "format is not available" in low and fmt == selectors[-1]:
                    break
                continue

    state["status"] = "error"
    state["error"] = _friendly_error(errors[-1] if errors else "Error desconocido")


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
        "attempt": 0,
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


@app.get("/api/cookies")
def cookies_status():
    return {"active": _cookies_active()}


@app.post("/api/cookies")
async def upload_cookies(request: Request):
    body = await request.body()
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        raise HTTPException(status_code=400, detail="El archivo está vacío.")
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    if not any(len(ln.split("\t")) == 7 for ln in lines):
        raise HTTPException(
            status_code=400,
            detail="Formato inválido. Exporta las cookies en formato Netscape (por ejemplo con la extensión 'Get cookies.txt LOCALLY', con sesión iniciada en YouTube).",
        )
    COOKIES_FILE.write_text(text + "\n", encoding="utf-8")
    return {"active": True}


@app.delete("/api/cookies")
def clear_cookies():
    if COOKIES_FILE.exists():
        COOKIES_FILE.unlink()
    return {"active": False}


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

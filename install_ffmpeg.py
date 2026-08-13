import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request

URL = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
BIN_NAMES = ("ffmpeg", "ffprobe")


def main() -> int:
    if shutil.which("ffmpeg"):
        print("ffmpeg ya disponible en PATH, no hace falta bajarlo")
        return 0
    if os.path.exists(os.path.join(TOOLS_DIR, "ffmpeg")) and os.path.exists(os.path.join(TOOLS_DIR, "ffprobe")):
        print(f"ffmpeg estático ya presente en {TOOLS_DIR}")
        return 0

    os.makedirs(TOOLS_DIR, exist_ok=True)
    print("Descargando ffmpeg estático...")
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".tar.xz", delete=False) as tmp:
            tmp_path = tmp.name
            with urllib.request.urlopen(URL, timeout=180) as resp:
                shutil.copyfileobj(resp, tmp)

        with tarfile.open(tmp_path, "r:xz") as tar:
            for member in tar.getmembers():
                if "/bin/" not in member.name or not member.isfile():
                    continue
                name = member.name.rsplit("/", 1)[-1]
                if name not in BIN_NAMES:
                    continue
                src = tar.extractfile(member)
                dest = os.path.join(TOOLS_DIR, name)
                with open(dest, "wb") as f:
                    shutil.copyfileobj(src, f)
                os.chmod(dest, 0o755)
                print("Instalado:", dest)
    except Exception as exc:
        print(f"ERROR al instalar ffmpeg: {exc}", file=sys.stderr)
        return 1
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    missing = [n for n in BIN_NAMES if not os.path.exists(os.path.join(TOOLS_DIR, n))]
    if missing:
        print(f"ERROR: faltan binarios: {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
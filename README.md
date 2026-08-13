# TubeFetch — Descargador de videos de YouTube

App web con interfaz animada para descargar videos y audio de YouTube.

- **Backend:** Python + FastAPI + yt-dlp
- **Frontend:** HTML, CSS y JavaScript con animaciones (fondo con orbes flotantes, tarjetas de vidrio esmerilado, barra de progreso con brillo, notificaciones)
- **Progreso en tiempo real** mediante Server-Sent Events (SSE)

## Características

- Análisis del video: título, canal, duración y miniatura
- Selección de calidad: máxima, 1080p, 720p, 480p, 360p o solo audio MP3
- Barra de progreso con velocidad y tiempo restante en vivo
- Historial de descargas recientes con descarga directa

## Requisitos

- Python 3.10+
- **ffmpeg** (necesario para mezclar video+audio y convertir a MP3)

  ```bash
  # Debian/Ubuntu
  sudo apt install ffmpeg
  # macOS
  brew install ffmpeg
  ```

## Uso

```bash
chmod +x run.sh
./run.sh
```

Abre `http://localhost:8000` en tu navegador.

Los archivos descargados se guardan en `backend/downloads/`.

## Notas

- La descarga depende de la disponibilidad de formatos del video (algunos videos de gran calidad no ofrecen separación video/audio).
- yt-dlp se actualiza con frecuencia; si algo deja de funcionar, ejecuta `pip install -U yt-dlp`.

# Dikte Web

[Türkçe](README.tr.md)

Dikte Web is a single-user, self-hosted workspace for turning microphone recordings and audio or video files into text. It is based on [Yusuf İpek’s Dikte](https://github.com/yusufipk/dikte) and runs in a browser with FastAPI, Jinja and JavaScript.

- **Dictation** records from your microphone with a timer, pause and resume, and a waveform. Press Ctrl+Space while the workspace has focus to start or finish. Copy the transcript, compare it with the original, or download it as TXT.
- **Writing** uses a dictionary of names and terms as a recognition hint. Set writing instructions or a translation language in Settings → Cleanup.
- **Files** accepts audio or video and exports TXT and SRT. You can apply cleanup and wrap subtitles along word boundaries.
- **Meetings** generates a Markdown summary and timestamped transcript. A stereo recording uses the left channel for your microphone and the right channel for the other side. Mono recordings have one speaker label. If summarization fails, retry uses the saved transcript.
- **Assistant** accepts written questions or voice questions through the Dictation screen. It keeps a configurable conversation history. You can override the hosted model for a request.
- **History** searches full transcripts, dates and models. When audio retention is enabled, the original audio is available there. Deleting or trimming an entry also removes its retained audio.

**Settings → Themes** offers Dark, Light, Forest Green and Steel Blue. The theme previews immediately and is remembered after saving.

## Run with Docker

For a new installation, configure these values in the project’s `.env` file. The named Docker volume keeps settings, history, and models across container updates.

```dotenv
DIKTE_WEB_PASSWORD=replace-with-your-password
DIKTE_COOKIE_SECURE=0
DIKTE_DATA_DIR=dikte_data
```

```sh
docker compose up -d
```

Open <http://localhost:8245>. Keep `DIKTE_COOKIE_SECURE=1` when using HTTPS. A reverse proxy or a Cloudflare Tunnel can expose the loopback service over HTTPS. The browser requires HTTPS or localhost for microphone access.

Compose pulls `ghcr.io/eco-null/dictation-web:latest`. Set `DIKTE_IMAGE` to a versioned tag to pin a release. To test local source changes using Docker, build a local image and name it in a Compose override, or run from source below.

For an existing installation, keep its current data mount and Compose project. The historical bind mount is `/DATA/AppData/dikte-web` when `DIKTE_DATA_DIR` is unset. If you choose a different bind path, make it writable by the container’s `dikte` user first. The port remains 8245, bound to loopback by default. Set `DIKTE_BIND_ADDRESS` and `DIKTE_PORT` to change the bind address or port. Supply your own password or read the generated password from `/data/share/dikte/web_password` in the container.

## Run from source

Install Python 3.11+ and put FFmpeg on PATH.

```sh
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Set `DIKTE_COOKIE_SECURE=0` in the shell for plain HTTP development. If `DIKTE_WEB_PASSWORD` is empty, the application stores the generated password in `web_password` under its data directory. The server uses one process for the background job queue and session signing.

## Providers and models

Settings has independent providers and model choices for transcription, cleanup, the assistant and meeting transcription. Hosted options are OpenAI, Groq, OpenRouter, OmniRoute and a custom OpenAI-compatible endpoint. A custom endpoint can run without a key. The endpoint must support `/audio/transcriptions` for transcription and `/chat/completions` for text operations. SRT and meeting timelines depend on the transcription provider supplying timestamps. The meeting summary uses the cleanup provider, with a separate summary model field for OpenRouter.

Choose **Local** to use whisper.cpp for audio and llama.cpp for text. Settings → Local models manages compatible release downloads and verifies checksums. On a host with no compatible release asset, install `whisper-server` or `llama-server` and add it to PATH. Use Settings to save API keys. Suggestions use the saved endpoint and credentials, and model IDs can always be entered directly.

## Data and API

| Setting | Purpose |
| --- | --- |
| `DIKTE_WEB_PASSWORD` | Shared login password |
| `DIKTE_COOKIE_SECURE` | HTTPS-only session cookie, defaults to `1` |
| `DIKTE_MAX_UPLOAD` | Maximum size of a file in bytes, defaults to 1 GiB |
| `XDG_CONFIG_HOME` | Base directory for `dikte/config.json` |
| `XDG_DATA_HOME` | Base directory for history, meetings, models and the generated password |

In Docker the config root is `/data/config` and the data root is `/data/share`. Back up the host mount to preserve both. It can contain API keys, transcripts and retained audio.

- `POST /api/dictate`, `POST /api/files/transcribe` and `POST /api/meetings` accept multipart uploads and return a job ID.
- `GET /api/jobs/{id}` returns status and result. `GET /api/jobs/{id}/download?fmt=txt|srt` downloads an available output. The queue admits one heavy job at a time and returns HTTP 409 while busy.
- `GET /api/meetings/{base}/download` downloads minutes. `POST /api/meetings/{base}/retry` starts a retry.
- `POST /api/agent` accepts `question` and optional `provider` and `model`. `GET /api/history` lists completed dictations. `GET /api/settings` exposes masked settings and `POST /api/settings` accepts changed fields.
- `GET /healthz` is public. Other API routes require a signed login cookie.

## Development and upstream

```sh
python -m pip install -r requirements-dev.txt
python -m pytest
```

This update uses upstream [`ab35e01`](https://github.com/yusufipk/dikte/commit/ab35e01a6eaaa4933b2478f7a3c5760d7eb7c90c) as the reference for subtitle wrapping, word-timing cues and the short-recording hallucination filter. The dictionary, writing instructions, translation, recording controls and voice questions are web adaptations. System-wide shortcuts, application auto-paste, system-audio capture and desktop CLI integrations require the native app.

Licensed under [GPL-3.0](LICENSE).

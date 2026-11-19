import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

import api
import assistant
import config as cfg
import filetranscribe as ft
import ggml
import meeting
import vad
import worker

from app import auth, jobs, rms, settings as web_settings

router = APIRouter()

MAX_UPLOAD = int(os.environ.get("DIKTE_MAX_UPLOAD", str(1024 ** 3)))
MAX_AUDIO_SECONDS = 4 * 3600
MAX_QUESTION_CHARS = 4000


def _valid_base(base):
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", base))


def _conf(request):
    return request.app.state.conf


def _save_upload(upload: UploadFile) -> str:
    if upload.size is not None and upload.size > MAX_UPLOAD:
        raise HTTPException(413, detail="upload too large")
    suffix = Path(upload.filename or "").suffix
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,12}", suffix):
        suffix = ""
    fd, path = tempfile.mkstemp(prefix="dikte-upload-", suffix=suffix)
    os.close(fd)
    try:
        with open(path, "wb") as fh:
            total = 0
            while chunk := upload.file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD:
                    raise HTTPException(413, detail="upload too large")
                fh.write(chunk)
    except Exception:
        os.unlink(path)
        raise
    return path


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _start(request, kind, work, cleanup=None):
    try:
        job_id = jobs.submit(kind, work)
    except Exception as exc:
        if cleanup:
            cleanup()
        if isinstance(exc, jobs.BusyError):
            raise HTTPException(409, detail="A job is already running.") from exc
        raise
    return {"job_id": job_id}


def _pct(done, total):
    if not total:
        return "?"
    return f"{int(done * 100 / total)}%"


def _hydrate_local(conf, service="transcribe"):
    if conf[f"{service}_provider"] == "local":
        ggml.whisper.configure(
            model=conf[f"{service}_local_model"],
            threads=conf[f"{service}_local_threads"],
            gpu=conf[f"{service}_local_gpu"],
            preload=conf[f"{service}_local_preload"],
            binary=conf["local_binary"],
        )


@router.get("/api/models")
def list_models(request: Request):
    whisper, llm = [], []
    whisper_error, llm_error = "", ""
    try:
        whisper = [{"name": m.name, "size": m.size, "url": m.url,
                    "path": str(ggml.whisper_model_path(m.name)),
                    "installed": m.name in ggml.installed_whisper_models()}
                   for m in ggml.whisper_models()]
    except Exception as exc:
        whisper_error = str(exc)
    try:
        llm = [{"repo": r,
                "quants": [{"name": q.name, "size": q.size, "url": q.url,
                            "path": str(ggml.llm_model_path(q.name)),
                            "installed": q.name in ggml.installed_llm_models()}
                           for q in ggml.llm_quants(r)]}
               for r in ggml.llm_repos()]
    except Exception as exc:
        llm_error = str(exc)
    programs = [{"name": p.name,
                 "installed": bool(ggml.installed_program(p)),
                 "version": ggml.installed_version(p),
                 "system": ggml.system_program(p)}
                for p in (ggml.WHISPER, ggml.LLAMA)]
    out = {"whisper_models": whisper, "llm": llm, "programs": programs}
    if whisper_error:
        out["whisper_error"] = whisper_error
    if llm_error:
        out["llm_error"] = llm_error
    return out


@router.get("/api/models/list")
def provider_models(request: Request, provider: str = "", svc: str = ""):
    """The model ids a hosted provider offers, for the settings dropdown.

    The key and the base URL are read out of the per-service settings, not taken
    from the caller: the client only says which provider and service it is
    looking at, and the stored key stays in the config (and out of the reply).
    Providers without a listing (omniroute, local) come back empty, and so does
    any failure — the UI degrades to a free-text field.
    """
    conf = _conf(request)
    models = []
    error = None
    target = conf.target_for(svc, provider)
    if target is not None and target.provider not in ("omniroute", "local"):
        try:
            models = api.list_models(target.provider, target.api_key, target.base_url,
                                     transcription=svc in ("transcribe", "meeting"))
        except Exception as exc:
            error = str(exc)
    return {"models": models, "error": error}


@router.post("/api/models/install")
def install_model(request: Request, payload: dict = Body(...)):
    kind = str(payload.get("kind") or "")
    name = str(payload.get("name") or "")
    repo = str(payload.get("repo") or "")
    conf = _conf(request)

    if kind == "program" and name not in ("whisper", "llama"):
        raise HTTPException(400, detail="unknown program")
    if kind not in ("program", "whisper", "llm"):
        raise HTTPException(400, detail="unknown kind")
    if kind == "llm" and repo not in ggml.llm_repos():
        raise HTTPException(400, detail="repo not allowed")

    def work(emit):
        if kind == "program":
            program = ggml.WHISPER if name == "whisper" else ggml.LLAMA
            emit("Looking up the release…")
            path = ggml.install_program(
                program,
                on_progress=lambda done, total:
                    emit(f"Downloading… {_pct(done, total)}"))
            return {"path": path}
        if kind == "whisper":
            item = next((m for m in ggml.whisper_models() if m.name == name), None)
            if item is None:
                raise ValueError("No such Whisper model.")
            target = ggml.whisper_model_path(name)
            ok = ggml.download(item, target,
                               on_progress=lambda done, total:
                                   emit(f"Downloading… {_pct(done, total)}"))
            if not ok:
                raise RuntimeError("Model installation stopped.")
            web_settings.apply(conf, {"transcribe_local_model": name})
            return {"path": str(target), "installed": True}
        if kind == "llm":
            item = next((q for q in ggml.llm_quants(repo) if q.name == name), None)
            if item is None:
                raise ValueError("No such language model.")
            target = ggml.llm_model_path(name)
            ok = ggml.download(item, target,
                               on_progress=lambda done, total:
                                   emit(f"Downloading… {_pct(done, total)}"))
            if not ok:
                raise RuntimeError("Model installation stopped.")
            updates = {key: name for key in ("cleanup_local_model", "assistant_local_model")
                       if not conf[key]}
            if updates:
                web_settings.apply(conf, updates)
            return {"path": str(target), "installed": True}
        raise HTTPException(400, detail="unknown kind")

    return _start(request, "model", work)


@router.post("/api/models/delete")
def delete_model_route(request: Request, payload: dict = Body(...)):
    path = str(payload.get("path") or "")
    if not path:
        raise HTTPException(400, detail="no path")
    resolved = Path(path).resolve()
    models_dir = Path(ggml.MODELS_DIR).resolve()
    allowed = (models_dir / "whisper", models_dir / "llm")
    if resolved.parent not in allowed or resolved.suffix.lower() not in (".bin", ".gguf"):
        raise HTTPException(400, detail="path outside the models directory")
    try:
        with jobs.exclusive():
            with ggml.llm_use:
                ggml.delete_model(resolved)
    except jobs.BusyError as exc:
        raise HTTPException(409, detail="A job is already running.") from exc
    except ggml.LocalError as exc:
        raise HTTPException(400, detail=str(exc))
    return {"deleted": True}


@router.get("/api/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, detail="no such job")
    return job.as_dict()


@router.post("/api/dictate")
def dictate(request: Request, audio: UploadFile = File(...), ask: bool = Form(False)):
    path = _save_upload(audio)
    conf = web_settings.snapshot(_conf(request))
    limit = min(MAX_AUDIO_SECONDS, conf["max_seconds"] or MAX_AUDIO_SECONDS)

    def work(emit):
        try:
            _hydrate_local(conf)
            with tempfile.TemporaryDirectory(prefix="dikte-dict-") as folder:
                emit(api.t("Converting audio…"))
                wav = ft._to_wav(path, folder, max_seconds=limit + 2)
                duration = ft.wav_seconds(wav)
                if duration > limit + 2:
                    raise api.ApiError(api.t("The recording exceeds the configured time limit."))
                pipeline = worker.Pipeline(conf)
                pipeline.stage.connect(emit)
                raw, text, warning = pipeline.run(wav, duration, rms.series(wav), ask=ask)
                entry = pipeline.history_entry
                return {"raw": raw, "text": text, "warning": warning,
                        "ts": entry["ts"], "model": entry["model"], "mode": entry["mode"]}
        finally:
            _unlink(path)

    return _start(request, "dictation", work, cleanup=lambda: _unlink(path))


@router.post("/api/files/transcribe")
def transcribe_file(request: Request, file: UploadFile = File(...),
                    timestamps: str = Form(""), cleanup: str = Form(""),
                    wrap: str | None = Form(None)):
    path = _save_upload(file)
    conf = web_settings.snapshot(_conf(request))
    want_ts = timestamps == "on"
    do_cleanup = cleanup == "on"
    wrap_srt = conf["file_wrap_srt"] if wrap is None else wrap == "on"

    def work(emit):
        try:
            _hydrate_local(conf)
            transcriber = ft.FileTranscriber(conf)
            transcriber.progress.connect(emit)
            text, segments = transcriber.transcribe(path, want_ts, do_cleanup,
                                                     max_seconds=MAX_AUDIO_SECONDS)
            srt = ft.to_srt(text, segments, wrap=wrap_srt) if want_ts else ""
            return {"text": text, "segments": [list(s) for s in segments],
                    "srt": srt, "warning": transcriber.warning}
        finally:
            _unlink(path)

    return _start(request, "file", work, cleanup=lambda: _unlink(path))


@router.get("/api/jobs/{job_id}/download")
def download(request: Request, job_id: str, fmt: str = "txt", format: str | None = None):
    job = jobs.get(job_id)
    if job is None or not job.result:
        raise HTTPException(404, detail="no such job")
    fmt = format or fmt
    if fmt == "srt":
        if not job.result.get("srt"):
            raise HTTPException(404, detail="This job has no subtitles.")
        body, name = job.result["srt"], "transcript.srt"
    elif fmt == "txt":
        body, name = job.result.get("text", ""), "transcript.txt"
    else:
        raise HTTPException(400, detail="Unknown download format.")
    return Response(content=body, media_type="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _meeting_wav(path, workdir, limit):
    out = os.path.join(workdir, "meeting.wav")
    return ft._ffmpeg(["-i", path, "-vn", "-t", str(limit + 1), "-ar", "16000",
                       "-c:a", "pcm_s16le", out], out)


@router.get("/api/meetings")
def list_meetings(request: Request):
    rows = cfg.read_meetings()
    rows.reverse()
    return {"meetings": rows}


@router.post("/api/meetings")
def create_meeting(request: Request, file: UploadFile = File(...),
                         participants: str = Form("")):
    path = _save_upload(file)
    conf = web_settings.snapshot(_conf(request))
    limit = min(MAX_AUDIO_SECONDS, conf["meeting_max_seconds"] or MAX_AUDIO_SECONDS)
    if participants.strip():
        conf["meeting_participants"] = participants.strip()[:4000]

    def work(emit):
        try:
            _hydrate_local(conf, "meeting")
            with tempfile.TemporaryDirectory(prefix="dikte-meet-") as folder:
                emit(api.t("Converting audio…"))
                wav = _meeting_wav(path, folder, limit)
                duration = ft.wav_seconds(wav)
                if duration > limit:
                    raise api.ApiError(api.t("The recording exceeds the configured time limit."))
                base = f"{meeting.new_base()}-{uuid.uuid4().hex[:12]}"
                entry = meeting.new_entry(base, duration)
                entry["participants"] = conf["meeting_participants"]
                target_wav = cfg.meeting_paths(base)[1]
                target_wav.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(wav, target_wav)
                try:
                    cfg.save_meeting(entry)
                except Exception:
                    _unlink(target_wav)
                    raise
                pipeline = meeting.MeetingPipeline(conf)
                pipeline.progress.connect(lambda base, msg: emit(msg))
                title = pipeline.run(entry)
                return {"base": base, "title": title}
        finally:
            _unlink(path)

    return _start(request, "meeting", work, cleanup=lambda: _unlink(path))


@router.get("/api/meetings/{base}")
def meeting_detail(base: str):
    if not _valid_base(base):
        raise HTTPException(400, detail="invalid meeting id")
    row = next((r for r in cfg.read_meetings() if r["base"] == base), None)
    if row is None:
        raise HTTPException(404)
    doc_path = cfg.meeting_paths(base)[0]
    doc = doc_path.read_text(encoding="utf-8") if doc_path.exists() else ""
    return {"meeting": row, "doc": doc}


@router.get("/api/meetings/{base}/download")
def download_meeting(base: str):
    doc = meeting_detail(base)["doc"]
    if not doc:
        raise HTTPException(404, detail="No minutes are available.")
    return Response(doc, media_type="text/markdown",
                    headers={"Content-Disposition": 'attachment; filename="meeting.md"'})


@router.post("/api/meetings/{base}/retry")
def retry_meeting(request: Request, base: str):
    if not _valid_base(base):
        raise HTTPException(400, detail="invalid meeting id")
    row = next((r for r in cfg.read_meetings() if r["base"] == base), None)
    if row is None:
        raise HTTPException(404)
    conf = web_settings.snapshot(_conf(request))
    if "participants" in row:
        conf["meeting_participants"] = row["participants"]

    def work(emit):
        _hydrate_local(conf, "meeting")
        pipeline = meeting.MeetingPipeline(conf)
        pipeline.progress.connect(lambda b, msg: emit(msg))
        title = pipeline.run(row)
        return {"base": base, "title": title}

    return _start(request, "meeting", work)


@router.delete("/api/meetings/{base}")
def delete_meeting(request: Request, base: str):
    if not _valid_base(base):
        raise HTTPException(400, detail="invalid meeting id")
    if not any(row["base"] == base for row in cfg.read_meetings()):
        raise HTTPException(404)
    try:
        with jobs.exclusive():
            cfg.delete_meetings([base])
    except jobs.BusyError as exc:
        raise HTTPException(409, detail="A job is already running.") from exc
    return {"deleted": base}


@router.post("/api/agent")
def ask_agent(request: Request, payload: dict = Body(...)):
    question = str(payload.get("question") or "").strip()
    if not question:
        raise HTTPException(400, detail="empty question")
    if len(question) > MAX_QUESTION_CHARS:
        raise HTTPException(400, detail="question too long")
    conf = web_settings.snapshot(_conf(request))
    try:
        answer, _warning = assistant.ask(
            question, conf,
            provider=str(payload.get("provider") or ""),
            model=str(payload.get("model") or ""),
        )
    except assistant.AssistantError as exc:
        raise HTTPException(400, detail=str(exc))
    return {"answer": answer}


@router.get("/api/history")
def history(request: Request):
    rows = cfg.read_history()
    rows.reverse()
    return {"entries": rows}


@router.get("/api/history/recordings/{name}")
def download_recording(name: str):
    if not re.fullmatch(r"[a-f0-9]{32}\.wav", name):
        raise HTTPException(404)
    path = cfg.DATA_DIR / "recordings" / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav", filename="recording.wav")


@router.delete("/api/history")
def delete_history(request: Request, payload: dict = Body(...)):
    rows = payload.get("rows") or []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise HTTPException(422, detail="Invalid history rows.")
    cfg.delete_history(rows)
    return {"deleted": len(rows)}


@router.post("/api/history/clear")
def clear_history():
    cfg.clear_history()
    return {"cleared": True}


@router.get("/api/settings")
def get_settings(request: Request):
    return {"settings": web_settings.present(_conf(request))}


@router.post("/api/settings")
def post_settings(request: Request, payload: dict = Body(...)):
    updates = payload.get("settings", {})
    if not isinstance(updates, dict):
        raise HTTPException(422, detail="Invalid settings.")
    try:
        web_settings.apply(_conf(request), updates)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    return {"saved": True}

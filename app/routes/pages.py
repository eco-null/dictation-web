import hmac
import os
import threading
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app import auth, settings as web_settings

router = APIRouter()

LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW = 15 * 60
_login_failures = {}
_login_failures_lock = threading.Lock()

PROVIDERS = [
    ("openai", "OpenAI"), ("groq", "Groq"), ("openrouter", "OpenRouter"),
    ("omniroute", "OmniRoute"), ("custom", "Custom server"), ("local", "Local"),
]

SERVICE_TITLES = {
    "transcribe": "Transcription", "cleanup": "Cleanup",
    "assistant": "Assistant", "meeting": "Meetings",
}

SERVICE_KEYS = {
    "transcribe": [
        ("language", "Speech language"), ("transcribe_prompt", "Transcription prompt"),
        ("dictionary", "Dictionary"), ("file_timestamps", "Include timestamps and SRT"),
        ("file_wrap_srt", "Wrap longer subtitles"), ("file_cleanup", "Clean up file transcripts"),
    ],
    "cleanup": [
        ("cleanup_enabled", "Clean up dictations"), ("cleanup_reasoning", "Reasoning"),
        ("writing_style", "Writing instructions"), ("translate_to", "Translate dictation to"),
        ("cleanup_prompt", "Cleanup prompt"), ("file_cleanup_prompt", "File cleanup prompt"),
    ],
    "assistant": [
        ("assistant_session_minutes", "Conversation retention (minutes)"),
        ("assistant_timeout", "Request timeout (seconds)"), ("assistant_reasoning", "Reasoning"),
        ("assistant_prompt", "Assistant prompt"),
    ],
    "meeting": [
        ("meeting_language", "Speech language"), ("meeting_cleanup", "Clean up the transcript"),
        ("meeting_reasoning", "Reasoning"), ("meeting_summary_model", "OpenRouter summary model"),
        ("meeting_self_name", "Your name"), ("meeting_other_name", "Other speaker"),
        ("meeting_participants", "Participants"), ("meeting_keep_audio", "Keep meeting audio"),
        ("meeting_max_seconds", "Meeting limit (seconds)"), ("meeting_prompt", "Meeting prompt"),
    ],
}
GENERAL_KEYS = [
    ("ui_language", "Interface language"), ("mic_target", "Microphone"),
    ("max_seconds", "Recording limit (seconds)"), ("history_limit", "History limit"),
    ("keep_audio", "Keep dictation audio"), ("skip_silent", "Skip silent recordings"),
    ("silence_db", "Silence floor (dB)"), ("speech_margin_db", "Speech margin (dB)"),
    ("min_voiced_seconds", "Minimum speech (seconds)"),
    ("filter_hallucinations", "Filter stock phrases"),
]


def _env_bool(name, default):
    return str(os.environ.get(name, default)).lower() in ("1", "true", "on", "yes")


def _render(request, template, context=None, status_code=200):
    ctx = dict(context or {})
    ctx.setdefault("current_path", request.url.path)
    ctx["ui_theme"] = web_settings.theme(request.app.state.conf)
    return request.app.state.templates.TemplateResponse(
        request, template, ctx, status_code=status_code)


def _client_ip(request):
    forwarded = request.headers.get("cf-connecting-ip") or \
        (request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    return forwarded or (request.client.host if request.client else "")


def _too_many_login_failures(ip):
    with _login_failures_lock:
        now = time.time()
        stamps = [ts for ts in _login_failures.get(ip, [])
                  if now - ts < LOGIN_WINDOW]
        if not stamps:
            _login_failures.pop(ip, None)
            return False
        _login_failures[ip] = stamps
        return len(stamps) >= LOGIN_MAX_FAILURES


def _record_login_failure(ip):
    with _login_failures_lock:
        now = time.time()
        stamps = [ts for ts in _login_failures.get(ip, [])
                  if now - ts < LOGIN_WINDOW]
        stamps.append(now)
        _login_failures[ip] = stamps


def _reset_login_failures(ip=None):
    with _login_failures_lock:
        if ip is None:
            _login_failures.clear()
        else:
            _login_failures.pop(ip, None)


@router.get("/")
def index():
    return RedirectResponse("/dictate", status_code=303)


@router.get("/login")
def login_page(request: Request):
    if auth.check(request.cookies.get(auth.COOKIE, "")):
        return RedirectResponse("/dictate", status_code=303)
    return _render(request, "login.html", {"error": ""})


@router.post("/login")
def login_post(request: Request, password: str = Form("")):
    ip = _client_ip(request)
    if _too_many_login_failures(ip):
        return _render(request, "login.html",
                       {"error": "Too many failed attempts. Try again later."},
                       status_code=429)
    if not hmac.compare_digest(password.encode(), auth.password().encode()):
        _record_login_failure(ip)
        return _render(request, "login.html",
                       {"error": "Wrong password."})
    _reset_login_failures(ip)
    response = RedirectResponse("/dictate", status_code=303)
    response.set_cookie(auth.COOKIE, auth.new_session(), httponly=True,
                        samesite="lax",
                        secure=_env_bool("DIKTE_COOKIE_SECURE", "1"),
                        max_age=auth.SESSION_HOURS * 3600)
    return response


@router.get("/logout")
@router.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE)
    return response


@router.get("/dictate")
def dictate_page(request: Request):
    import config as cfg
    conf = request.app.state.conf
    return _render(request, "dictation.html", {
        "conf": conf,
        "provider_ready": conf.transcribe_ready(),
        "recording_limit": min(14400, conf["max_seconds"] or 14400),
        "entries": list(reversed(cfg.read_history(limit=4))),
    })


@router.get("/files")
def files_page(request: Request):
    return _render(request, "files.html", {"conf": request.app.state.conf})


@router.get("/meetings")
def meetings_page(request: Request):
    import config as cfg
    rows = cfg.read_meetings()
    rows.reverse()
    return _render(request, "meetings.html",
                   {"meetings": rows, "conf": request.app.state.conf})


@router.get("/meetings/{base}")
def meeting_detail_page(request: Request, base: str):
    import config as cfg
    row = next((r for r in cfg.read_meetings() if r["base"] == base), None)
    if row is None:
        from fastapi import HTTPException
        raise HTTPException(404)
    doc_path = cfg.meeting_paths(base)[0]
    doc = doc_path.read_text(encoding="utf-8") if doc_path.exists() else ""
    return _render(request, "meeting_detail.html",
                   {"meeting": row, "doc": doc})


@router.get("/agent")
def agent_page(request: Request):
    return _render(request, "agent.html", {"providers": PROVIDERS})


@router.get("/history")
def history_page(request: Request):
    import config as cfg
    rows = cfg.read_history()
    rows.reverse()
    return _render(request, "history.html",
                   {"entries": rows, "conf": request.app.state.conf})


@router.get("/settings")
def settings_page(request: Request):
    services = [
        {"key": key, "title": title,
         "providers": [{"name": value, "label": label} for value, label in PROVIDERS]}
        for key, title in SERVICE_TITLES.items()
    ]
    display = web_settings.present(request.app.state.conf)
    return _render(request, "settings.html", {
        "themes": web_settings.THEMES,
        "fields": web_settings.WEB_FIELDS,
        "settings": display,
        "masked": web_settings.MASKED,
        "conf": request.app.state.conf,
        "services": services,
        "general_keys": GENERAL_KEYS,
        "service_keys": SERVICE_KEYS,
    })

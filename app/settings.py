import math
import threading

import config as cfg

_lock = threading.RLock()
_PROVIDERS = ("openai", "groq", "openrouter", "omniroute", "custom")

THEMES = {
    "postready-dark": "Dark",
    "postready-light": "Light",
    "forest": "Forest Green",
    "steel": "Steel Blue",
}
DEFAULT_THEME = cfg.DEFAULTS["ui_theme"]


def theme(conf):
    value = conf["ui_theme"]
    return value if isinstance(value, str) and value in THEMES else DEFAULT_THEME


def _provider_fields(service, providers, local_extra):
    fields = {}
    fields[f"{service}_provider"] = ("select", list(providers) + ["local"])
    for p in providers:
        fields[f"{service}_{p}_url"] = ("str", [])
        fields[f"{service}_{p}_key"] = ("str", [])
        fields[f"{service}_{p}_model"] = ("str", [])
    fields[f"{service}_local_model"] = ("str", [])
    fields[f"{service}_local_threads"] = ("int", [])
    fields[f"{service}_local_gpu"] = ("bool", [])
    fields[f"{service}_local_preload"] = ("bool", [])
    for k in local_extra:
        fields[f"{service}_local_{k}"] = ("int" if k == "context" else "str", [])
    return fields


WEB_FIELDS = {
    "ui_theme": ("select", THEMES),
    "ui_language": ("select", ["auto", "tr", "en"]),
    "language": ("select", ["auto", "tr", "en"]),
    "transcribe_prompt": ("str", []),
    "skip_silent": ("bool", []),
    "silence_db": ("float", []),
    "speech_margin_db": ("float", []),
    "min_voiced_seconds": ("float", []),
    "filter_hallucinations": ("bool", []),
    "history_limit": ("int", []),
    "mic_target": ("str", []),
    "keep_audio": ("bool", []),
    "max_seconds": ("int", []),
    "file_timestamps": ("bool", []),
    "file_wrap_srt": ("bool", []),
    "dictionary": ("str", []),
    "writing_style": ("str", []),
    "translate_to": ("select", ["", "Turkish", "English", "German", "French", "Spanish", "Italian", "Arabic"]),
    "file_cleanup": ("bool", []),
    "file_cleanup_prompt": ("str", []),
    "cleanup_enabled": ("bool", []),
    "cleanup_reasoning": ("str", []),
    "cleanup_prompt": ("str", []),
    "meeting_cleanup": ("bool", []),
    "meeting_language": ("select", ["", "auto", "tr", "en"]),
    "meeting_summary_model": ("str", []),
    "meeting_reasoning": ("str", []),
    "meeting_prompt": ("str", []),
    "meeting_max_seconds": ("int", []),
    "meeting_keep_audio": ("bool", []),
    "meeting_self_name": ("str", []),
    "meeting_other_name": ("str", []),
    "meeting_participants": ("str", []),
    "assistant_reasoning": ("str", []),
    "assistant_prompt": ("str", []),
    "assistant_session_minutes": ("int", []),
    "assistant_timeout": ("int", []),
**_provider_fields("transcribe", _PROVIDERS, []),
**_provider_fields("cleanup", _PROVIDERS, ["context", "reasoning"]),
**_provider_fields("assistant", _PROVIDERS, ["context", "reasoning"]),
**_provider_fields("meeting", _PROVIDERS, []),
}

MASKED = {key for key in WEB_FIELDS if key.endswith("_key")}


def _coerce(key, raw):
    kind = WEB_FIELDS[key][0]
    if kind == "bool":
        return str(raw).lower() in ("1", "true", "on", "yes")
    if kind == "int":
        text = str(raw).strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None
    if kind == "float":
        text = str(raw).strip()
        if not text:
            return None
        try:
            value = float(text)
            return value if math.isfinite(value) else None
        except ValueError:
            return None
    value = str(raw).strip()
    if kind == "select" and value not in WEB_FIELDS[key][1]:
        raise ValueError(f"Invalid value for {key}.")
    return value


def redact(key, value):
    if key in MASKED and value:
        return f"{value[:3]}…{value[-3:]}" if len(value) > 8 else "••••"
    return value


def present(conf):
    with _lock:
        return {key: theme(conf) if key == "ui_theme" else redact(key, conf[key])
                for key in WEB_FIELDS}


def snapshot(conf):
    with _lock:
        return conf.snapshot()


def apply(conf, updates):
    with _lock:
        pending = conf.snapshot()
        for key, raw in updates.items():
            if key not in WEB_FIELDS:
                continue
            if key in MASKED and (str(raw).strip() == redact(key, conf[key]) or raw == ""):
                continue
            value = "" if key in MASKED and raw is None else _coerce(key, raw)
            if value is None:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if key != "silence_db" and value < 0:
                    raise ValueError(f"Invalid value for {key}.")
            pending[key] = value
        pending.save()
        conf.data = pending.data

"""Verify the public pages and persistence of a running Compose container."""

import argparse
import json
import sys
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("--verify-saved", action="store_true")
    args = parser.parse_args()
    password = sys.stdin.readline().strip()
    assert password

    client = build_opener(HTTPCookieProcessor(CookieJar()))
    base = args.base_url.rstrip("/")

    def request(path, data=None, headers=None):
        with client.open(Request(base + path, data=data, headers=headers or {}), timeout=10) as response:
            return response.read().decode()

    assert json.loads(request("/healthz")) == {"ok": True}
    try:
        request("/api/settings")
    except HTTPError as error:
        assert error.code == 401
    else:
        raise AssertionError("Settings were available before signing in")

    assert "Dikte Web" in request("/login", urlencode({"password": password}).encode())
    if not args.verify_saved:
        default = json.loads(request("/api/settings"))["settings"]["ui_theme"]
        assert default == "postready-dark"
        payload = json.dumps({"settings": {"ui_theme": "forest"}}).encode()
        result = request("/api/settings", payload, {"Content-Type": "application/json"})
        assert json.loads(result) == {"saved": True}

    assert json.loads(request("/api/settings"))["settings"]["ui_theme"] == "forest"
    for path in ("/dictate", "/files", "/meetings", "/agent", "/history", "/settings"):
        assert 'data-theme="forest"' in request(path), path
    assert "--canvas" in request("/static/themes.css")
    print("Compose container smoke test passed")


if __name__ == "__main__":
    main()

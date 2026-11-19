

import os
import unittest

from fastapi.testclient import TestClient

import config as cfg
import i18n
from app.main import app as fastapi_app
from tests.support import DikteTest

os.environ["DIKTE_COOKIE_SECURE"] = "0"

class PagesTest(DikteTest):
    def setUp(self):
        super().setUp()
        self.client = TestClient(fastapi_app)
        self.addCleanup(self.client.close)
        fastapi_app.state.conf = self.config()
        self.client.post("/login", data={"password": "test-password"})

    def test_every_page_renders_logged_in(self):
        for path in ("/dictate", "/files", "/meetings", "/agent",
                     "/history", "/settings"):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, path)

    def test_meeting_detail_404_for_unknown_base(self):
        resp = self.client.get("/meetings/19990101-000000")
        self.assertEqual(resp.status_code, 404)

    def test_nav_has_all_sections(self):
        body = self.client.get("/dictate").text
        for label in ("Dictation", "Files", "Meetings", "Assistant",
                      "History", "Settings", "Log out"):
            self.assertIn(label, body)

    def test_html_lang_is_resolved_not_a_key(self):
        i18n.set_language("tr")
        self.addCleanup(i18n.set_language, "en")
        body = self.client.get("/dictate").text
        self.assertIn('lang="tr"', body)
        self.assertNotIn('lang="auto"', body)

    def test_settings_never_sends_masked_keys_as_values(self):
        conf = self.config(openai_api_key="sk-verysecret123")
        fastapi_app.state.conf = conf
        body = self.client.get("/settings").text
        self.assertNotIn('value="sk-', body)

    def test_logout_clears_the_session(self):
        resp = self.client.get("/logout", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.client.cookies.clear()
        resp = self.client.get("/api/history")
        self.assertEqual(resp.status_code, 401)

    def test_logout_works_via_post(self):
        resp = self.client.post("/logout", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.client.cookies.clear()
        resp = self.client.get("/api/history")
        self.assertEqual(resp.status_code, 401)

    def test_login_accepts_a_unicode_password(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {"DIKTE_WEB_PASSWORD": "Türkçe parola"}):
            response = self.client.post("/login", data={"password": "Türkçe parola"},
                                        follow_redirects=False)
        self.assertEqual(response.status_code, 303)

    def test_login_rate_limit_returns_429_after_failures(self):
        from app.routes import pages as pages_routes
        self.addCleanup(pages_routes._reset_login_failures)
        for _ in range(pages_routes.LOGIN_MAX_FAILURES):
            resp = self.client.post("/login", data={"password": "wrong"})
            self.assertEqual(resp.status_code, 200)
        resp = self.client.post("/login", data={"password": "wrong"})
        self.assertEqual(resp.status_code, 429)
        self.assertIn("Too many failed attempts", resp.text)

    def test_login_rate_limit_uses_the_proxy_client_ip(self):
        from app.routes import pages as pages_routes
        self.addCleanup(pages_routes._reset_login_failures)
        headers = {"CF-Connecting-IP": "203.0.113.1"}
        for _ in range(pages_routes.LOGIN_MAX_FAILURES):
            resp = self.client.post("/login", data={"password": "wrong"},
                                    headers=headers)
            self.assertEqual(resp.status_code, 200)
        resp = self.client.post("/login", data={"password": "wrong"},
                                headers=headers)
        self.assertEqual(resp.status_code, 429)
        other = self.client.post("/login", data={"password": "wrong"},
                                 headers={"CF-Connecting-IP": "203.0.113.2"})
        self.assertEqual(other.status_code, 200)

    def test_dictation_page_wires_copy_and_download(self):
        body = self.client.get("/dictate").text
        self.assertIn('data-copy', body)
        self.assertIn('data-target="#text"', body)
        self.assertIn('data-download', body)
        self.assertIn('data-fmt="txt"', body)

    def test_dictation_page_has_record_controls(self):
        body = self.client.get("/dictate").text
        for marker in ('id="record"', 'id="stop"', 'id="cancel"', 'id="pause"', 'role="status"'):
            self.assertIn(marker, body)
        self.assertIn('aria-current="page"', body)

    def test_css_respects_reduced_motion(self):
        css = self.client.get("/static/app.css").text
        self.assertIn("prefers-reduced-motion", css)

    def test_css_has_focus_visible_rings(self):
        css = self.client.get("/static/app.css").text
        self.assertIn(":focus-visible", css)

    def test_nav_uses_svg_icons_not_emoji(self):
        body = self.client.get("/dictate").text
        self.assertIn("<svg", body)
        for emoji in ("\U0001F3A4", "\U0001F4C1", "\U0001F4CB", "\u2699\uFE0F"):
            self.assertNotIn(emoji, body)

    def test_login_page_has_auth_card(self):
        self.client.cookies.clear()
        resp = self.client.get("/login")
        body = resp.text
        self.assertEqual(resp.status_code, 200)
        self.assertIn('action="/login"', body)
        self.assertIn('name="password"', body)
        self.assertIn("auth-card", body)

    def test_files_page_has_dropzone_and_downloads(self):
        body = self.client.get("/files").text
        for marker in ("dropzone", "data-download", 'data-fmt="srt"'):
            self.assertIn(marker, body)

    def test_files_and_meetings_have_a_labeled_native_upload_input(self):
        for path in ("/files", "/meetings"):
            body = self.client.get(path).text
            self.assertIn('for="upload-file"', body)
            self.assertRegex(body, r'<input id="upload-file" type="file"[^>]+required>')

    def test_authenticated_pages_disable_caching(self):
        for path in ("/dictate", "/files", "/meetings", "/agent",
                     "/history", "/settings", "/api/history"):
            response = self.client.get(path)
            self.assertEqual(response.headers["cache-control"], "no-store", path)

    def test_meeting_detail_disable_caching(self):
        cfg.save_meeting({"base": "20260101-120000", "status": "done",
                          "title": "Toplantı", "model": "x"})
        response = self.client.get("/meetings/20260101-120000")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_login_page_disable_caching(self):
        self.client.cookies.clear()
        response = self.client.get("/login")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn('aria-label="Workspace"', response.text)

    def test_meetings_page_has_action_buttons(self):
        cfg.save_meeting({"base": "20260101-120000", "status": "failed",
                          "title": "Eski toplantı", "model": "x"})
        body = self.client.get("/meetings").text
        for marker in ("data-retry", "data-delete-meeting", "/meetings/20260101-120000"):
            self.assertIn(marker, body)

    def test_agent_page_has_chat_surface(self):
        body = self.client.get("/agent").text
        for marker in ('role="log"', 'name="question"', 'name="provider"', 'name="model"'):
            self.assertIn(marker, body)

    def test_history_page_has_bulk_actions(self):
        cfg.append_history({"text": "Complete transcript", "raw": "Raw", "model": "demo"})
        body = self.client.get("/history").text
        for marker in ("delete-selected", "row-check", "history-search",
                       'data-target="#transcript-1"', "Complete transcript"):
            self.assertIn(marker, body)

    def test_settings_page_has_local_and_models_sections(self):
        body = self.client.get("/settings").text
        self.assertIn('name="transcribe_provider"', body)
        self.assertIn('name="cleanup_provider"', body)
        self.assertIn('value="local"', body)
        self.assertIn('name="ui_language"', body)
        self.assertIn('name="file_timestamps"', body)
        self.assertIn('id="models"', body)

    def test_settings_page_offers_omniroute_provider(self):
        body = self.client.get("/settings").text
        self.assertIn('value="omniroute"', body)

    def test_settings_service_cards_present(self):
        body = self.client.get("/settings").text
        for svc in ("transcribe", "cleanup", "assistant"):
            self.assertIn(f'name="{svc}_provider"', body)
        self.assertIn("data-service", body)
        self.assertIn("General", body)

    def test_settings_provider_fields_marked_for_show_hide(self):
        body = self.client.get("/settings").text
        self.assertIn('data-provider="openai"', body)
        self.assertIn('data-provider="omniroute"', body)
        self.assertIn('data-provider="local"', body)
        self.assertIn('name="transcribe_omniroute_url"', body)
        self.assertIn('name="assistant_openai_key"', body)

    def test_settings_only_selected_provider_visible_server_side(self):
        body = self.client.get("/settings").text
        transcription = body.split('id="transcribe"', 1)[1].split("</fieldset>", 1)[0]
        self.assertRegex(transcription, r'data-provider="openai"\s*>')
        for provider in ("groq", "openrouter", "omniroute", "custom", "local"):
            self.assertRegex(transcription, rf'data-provider="{provider}"\s+hidden>')

    def test_settings_has_turkish_labels_when_tr(self):
        i18n.set_language("tr")
        self.addCleanup(i18n.set_language, "en")
        body = self.client.get("/settings").text
        self.assertIn("Sağlayıcı", body)
        self.assertIn("Transkripsiyon", body)
        self.assertIn("Temel URL", body)

    def test_settings_separates_the_two_languages(self):
        body = self.client.get("/settings").text
        self.assertIn("Speech language", body)
        self.assertIn("Interface language", body)
        self.assertIn('name="ui_language"', body)
        self.assertEqual(body.count('name="language"'), 1)

    def test_settings_keeps_builtin_prompts_dynamic(self):
        body = self.client.get("/settings").text
        self.assertRegex(body, r'<textarea name="cleanup_prompt"[^>]*></textarea>')
        self.assertIn("Uses the built-in prompt when empty", body)

    def test_settings_has_a_meetings_card(self):
        body = self.client.get("/settings").text
        self.assertIn('data-service="meeting"', body)
        self.assertIn('name="meeting_provider"', body)
        self.assertIn('name="meeting_openrouter_model"', body)

    def test_theme_defaults_to_brand_and_persists_across_pages(self):
        body = self.client.get("/settings").text
        self.assertIn('data-theme="postready-dark"', body)
        self.assertIn('name="ui_theme" value="postready-dark" checked', body)
        for label in ("Dark", "Light", "Forest Green", "Steel Blue"):
            self.assertIn(f'<span>{label}</span>', body)
        for alternative in ("postready-light", "forest", "steel"):
            self.assertIn(f'name="ui_theme" value="{alternative}"', body)
        response = self.client.post("/api/settings", json={"settings": {"ui_theme": "forest"}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(cfg.Config()["ui_theme"], "forest")
        self.assertIn('data-theme="forest"', self.client.get("/files").text)
        self.client.cookies.clear()
        self.assertIn('data-theme="forest"', self.client.get("/login").text)

    def test_unknown_theme_does_not_replace_the_saved_choice(self):
        fastapi_app.state.conf = self.config(ui_theme="steel")
        response = self.client.post("/api/settings", json={"settings": {"ui_theme": "other"}})
        self.assertEqual(response.status_code, 422)
        self.assertIn('data-theme="steel"', self.client.get("/dictate").text)

    def test_settings_has_save_action(self):
        body = self.client.get("/settings").text
        self.assertIn('form="settings-form" id="save-settings"', body)
        self.assertIn('id="save-status"', body)
        self.assertIn("/static/settings.js", body)

    def test_models_allow_search_or_manual_entry(self):
        body = self.client.get("/settings").text
        for key in ("transcribe_openai_model", "cleanup_groq_model", "assistant_openrouter_model"):
            self.assertIn(f'<input name="{key}"', body)
            self.assertIn(f'<datalist id="catalog-{key}">', body)

    def test_settings_preserves_custom_model_when_the_catalog_is_empty(self):
        fastapi_app.state.conf = self.config(transcribe_openai_model="manual-model")
        body = self.client.get("/settings").text
        self.assertIn('name="transcribe_openai_model" value="manual-model"', body)

    def test_omniroute_and_local_model_fields_are_text_inputs(self):
        body = self.client.get("/settings").text
        self.assertIn('<input name="transcribe_omniroute_model"', body)
        self.assertIn('<input name="transcribe_local_model"', body)

    def test_settings_offers_browser_microphone_selection(self):
        body = self.client.get("/settings").text
        self.assertIn('<select name="mic_target" id="mic-select">', body)
        self.assertIn('id="detect-microphones"', body)

    def test_local_models_collapsed_in_a_details_block(self):
        body = self.client.get("/settings").text
        self.assertIn("Local models", body)
        self.assertIn("models-details", body)

if __name__ == "__main__":
    unittest.main()

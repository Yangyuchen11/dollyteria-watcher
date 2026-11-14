import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hooks.auto_order import RecaptchaTokenProvider
from web.app import create_app


class TestGoogleCookieSync(unittest.TestCase):
    def test_domain_filter(self):
        self.assertTrue(RecaptchaTokenProvider._is_google_trust_domain(".google.com"))
        self.assertTrue(RecaptchaTokenProvider._is_google_trust_domain("accounts.google.com"))
        self.assertTrue(RecaptchaTokenProvider._is_google_trust_domain(".google.co.jp"))
        self.assertTrue(RecaptchaTokenProvider._is_google_trust_domain(".youtube.com"))
        self.assertFalse(RecaptchaTokenProvider._is_google_trust_domain("www.dollyteria.com"))
        self.assertFalse(RecaptchaTokenProvider._is_google_trust_domain("google.com.evil.org"))
        self.assertFalse(RecaptchaTokenProvider._is_google_trust_domain(""))

    def test_has_google_login_cookies(self):
        self.assertFalse(RecaptchaTokenProvider._has_google_login_cookies([]))
        self.assertFalse(RecaptchaTokenProvider._has_google_login_cookies([
            {"name": "_GRECAPTCHA", "value": "abc", "domain": "www.google.com"},
            {"name": "NID", "value": "xyz", "domain": ".google.com"},
        ]))
        self.assertTrue(RecaptchaTokenProvider._has_google_login_cookies([
            {"name": "SID", "value": "secret", "domain": ".google.com"},
        ]))
        self.assertTrue(RecaptchaTokenProvider._has_google_login_cookies([
            {"name": "__Secure-1PSID", "value": "secret", "domain": ".google.co.jp"},
        ]))

    def test_normalize_cookie_for_cdp(self):
        # Standard Cookie-Editor format with expirationDate + no_restriction
        c1 = RecaptchaTokenProvider._normalize_cookie_for_cdp({
            "name": "__Secure-1PSID",
            "value": "tok1",
            "domain": ".google.com",
            "path": "/",
            "secure": False,
            "httpOnly": True,
            "sameSite": "no_restriction",
            "expirationDate": 1893456000.5,
        })
        self.assertEqual(c1["name"], "__Secure-1PSID")
        self.assertEqual(c1["domain"], ".google.com")
        self.assertTrue(c1["secure"])
        self.assertTrue(c1["httpOnly"])
        self.assertEqual(c1["sameSite"], "None")
        self.assertEqual(c1["expires"], 1893456000.5)

        # __Host- prefix must omit domain and use url + path="/" + secure=True
        c2 = RecaptchaTokenProvider._normalize_cookie_for_cdp({
            "name": "__Host-GAPS",
            "value": "gaps_val",
            "domain": "accounts.google.com",
            "path": "/foo",
            "secure": False,
            "sameSite": "unspecified",
        })
        self.assertNotIn("domain", c2)
        self.assertEqual(c2["url"], "https://accounts.google.com/")
        self.assertEqual(c2["path"], "/")
        self.assertTrue(c2["secure"])
        self.assertNotIn("sameSite", c2)

        # Non-Google domains are rejected
        self.assertIsNone(RecaptchaTokenProvider._normalize_cookie_for_cdp({
            "name": "PHPSESSID",
            "value": "123",
            "domain": "www.dollyteria.com",
        }))

    def test_import_rejects_invalid_payload(self):
        provider = RecaptchaTokenProvider.get_instance()
        r1 = provider.import_google_cookies("not-valid-json")
        self.assertFalse(r1["success"])
        r2 = provider.import_google_cookies([{"name": "PHPSESSID", "value": "1", "domain": "www.dollyteria.com"}])
        self.assertFalse(r2["success"])
        self.assertEqual(r2["imported"], 0)

    def test_import_preserves_existing_cookies_and_storage(self):
        provider = RecaptchaTokenProvider.get_instance()
        slot_mock = MagicMock()
        slot_mock.ws_url = "ws://127.0.0.1:9222/devtools/page/test"
        cdp_mock = MagicMock()
        cdp_mock.call.return_value = {"success": True}

        with (
            patch.object(provider, "_ensure_browser_and_pool_ready", return_value=True),
            patch.object(provider, "_cleanup_proc_unlocked"),
            patch.object(provider, "_ensure_headless_signin_disabled"),
            patch.object(provider, "_read_live_google_cookies", return_value=(True, [{"name": "SID", "value": "v", "domain": ".google.com"}])),
            patch.object(provider, "_slots", [slot_mock]),
            patch("hooks.auto_order._SimpleCDPClient", return_value=cdp_mock),
        ):
            res = provider.import_google_cookies([{"name": "SID", "value": "v", "domain": ".google.com"}])

        self.assertTrue(res["success"])
        called_methods = [c.args[0] for c in cdp_mock.call.call_args_list]
        self.assertIn("Network.setCookie", called_methods)
        self.assertNotIn("Network.clearBrowserCookies", called_methods)
        self.assertNotIn("Storage.clearDataForOrigin", called_methods)

    def test_ensure_headless_signin_disabled(self):
        tmp_dir = tempfile.mkdtemp(prefix="dolly_pref_test_")
        try:
            default_dir = os.path.join(tmp_dir, "Default")
            os.makedirs(default_dir, exist_ok=True)
            pref_path = os.path.join(default_dir, "Preferences")
            with open(pref_path, "w", encoding="utf-8") as f:
                json.dump({"intl": {"accept_languages": "ja-JP"}, "signin": {"custom_flag": 1}}, f)

            RecaptchaTokenProvider._ensure_headless_signin_disabled(tmp_dir)
            with open(pref_path, "r", encoding="utf-8") as f:
                prefs = json.load(f)
            self.assertEqual(prefs["intl"]["accept_languages"], "ja-JP")
            self.assertEqual(prefs["signin"]["custom_flag"], 1)
            self.assertFalse(prefs["signin"]["allowed"])
            self.assertFalse(prefs["signin"]["allowed_on_next_startup"])
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_stealth_js_platform_consistent(self):
        js = RecaptchaTokenProvider._build_stealth_js()
        self.assertIn("Direct3D11", js)
        self.assertIn("OpenGL 4.6", js)
        self.assertIn("PluginArray", js)
        self.assertIn("__dollyStealthReady", js)
        self.assertIn("__dollyPatched", js)

    def test_flask_cookie_endpoints_auth_and_routing(self):
        mock_monitor = MagicMock()
        mock_monitor.config = {"web_password": "secret_pw", "watches": []}
        mock_monitor.state = {"seen_ids": []}
        mock_monitor.notifier.recent_events = []
        mock_monitor.notifier.listeners = []
        mock_scheduler = MagicMock()
        mock_scheduler.get_status.return_value = {"running": False}

        app = create_app(mock_monitor, mock_scheduler)
        client = app.test_client()

        # Unauthenticated requests must be rejected with 401
        self.assertEqual(client.get("/api/browser/export_cookies").status_code, 401)
        self.assertEqual(client.post("/api/browser/import_cookies", json={"cookies": []}).status_code, 401)
        self.assertEqual(client.post("/api/browser/reset_profile").status_code, 401)

        # Login with configured password
        login_res = client.post("/api/auth/login", json={"password": "secret_pw"})
        self.assertEqual(login_res.status_code, 200)

        with patch.object(RecaptchaTokenProvider, "export_google_cookies", return_value={
            "success": True, "has_google_login": True, "count": 2, "cookies": [{"name": "SID"}]
        }) as mock_exp:
            r_exp = client.get("/api/browser/export_cookies")
            self.assertEqual(r_exp.status_code, 200)
            self.assertTrue(r_exp.get_json()["has_google_login"])
            mock_exp.assert_called_once()

        with patch.object(RecaptchaTokenProvider, "import_google_cookies", return_value={
            "success": True, "imported": 2, "has_google_login": True
        }) as mock_imp:
            r_imp = client.post("/api/browser/import_cookies", json={"cookies_json": json.dumps([{"name": "SID"}])})
            self.assertEqual(r_imp.status_code, 200)
            self.assertTrue(r_imp.get_json()["has_google_login"])
            mock_imp.assert_called_once()

        with patch.object(RecaptchaTokenProvider, "reset_browser_profile", return_value={
            "success": True, "profile_dir": "/app/data/browser_profile", "restarted": True, "message": "ok"
        }) as mock_reset:
            r_rst = client.post("/api/browser/reset_profile")
            self.assertEqual(r_rst.status_code, 200)
            self.assertTrue(r_rst.get_json()["success"])
            self.assertTrue(r_rst.get_json()["restarted"])
            mock_reset.assert_called_once()

    def test_reset_browser_profile_only_deletes_browser_profile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_dir = os.path.join(tmpdir, "browser_profile")
            os.makedirs(os.path.join(profile_dir, "Default"), exist_ok=True)
            cookie_file = os.path.join(profile_dir, "Default", "Cookies")
            with open(cookie_file, "w", encoding="utf-8") as f:
                f.write("old-conflicting-cookies")

            # Sibling app data files that MUST NOT be deleted
            config_file = os.path.join(tmpdir, "config.json")
            state_file = os.path.join(tmpdir, "state.json")
            db_file = os.path.join(tmpdir, "archive.db")
            for p in (config_file, state_file, db_file):
                with open(p, "w", encoding="utf-8") as f:
                    f.write("keep-me")

            provider = RecaptchaTokenProvider.__new__(RecaptchaTokenProvider)
            provider._lock = __import__("threading").Lock()
            provider._proc = None
            provider._gui_proc = None
            provider._sitekey = "test-sitekey"
            provider._cleanup_proc_unlocked = MagicMock()
            provider._get_profile_dir = MagicMock(return_value=profile_dir)
            provider._ensure_browser_and_pool_ready = MagicMock(return_value=True)

            res = provider.reset_browser_profile()

            self.assertTrue(res["success"])
            self.assertTrue(res["restarted"])
            self.assertFalse(os.path.exists(profile_dir))
            provider._cleanup_proc_unlocked.assert_called_once()
            provider._ensure_browser_and_pool_ready.assert_called_once_with("test-sitekey")
            for p in (config_file, state_file, db_file):
                self.assertTrue(os.path.exists(p), f"Sibling file {p} should never be deleted")


if __name__ == "__main__":
    unittest.main()

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


if __name__ == "__main__":
    unittest.main()

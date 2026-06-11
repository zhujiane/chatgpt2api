import unittest

from services.register.clearance import (
    ClearanceBundle,
    ClearanceCookie,
    RegisterClearanceStore,
    apply_user_agent,
    merge_cookie_header,
    normalize_clearance_config,
)
from services.register.openai_register import PlatformRegistrar


class RegisterClearanceTests(unittest.TestCase):
    def test_normalize_clearance_config(self) -> None:
        config = normalize_clearance_config({
            "mode": "flaresolverr",
            "target_url": "",
            "timeout_sec": "0",
            "refresh_interval": "12",
        })

        self.assertEqual(config["mode"], "flaresolverr")
        self.assertEqual(config["target_url"], "https://auth.openai.com")
        self.assertEqual(config["timeout_sec"], 1)
        self.assertEqual(config["refresh_interval"], 12)

    def test_manual_bundle_uses_proxy_affinity(self) -> None:
        store = RegisterClearanceStore()
        bundle = store.get(
            {
                "mode": "manual",
                "target_url": "https://auth.openai.com",
                "cf_cookies": "cf_clearance=abc; oai-did=generated-elsewhere",
                "user_agent": "Mozilla/5.0 Chrome/145.0.0.0",
            },
            "socks5://127.0.0.1:1080",
        )

        self.assertIsNotNone(bundle)
        assert bundle is not None
        self.assertEqual(bundle.affinity_key, "socks5://127.0.0.1:1080")
        self.assertEqual(bundle.clearance_host, "auth.openai.com")
        self.assertEqual(bundle.cf_cookies, "cf_clearance=abc")
        self.assertEqual([cookie.name for cookie in bundle.cookies], ["cf_clearance"])

    def test_merge_cookie_header_deduplicates_names(self) -> None:
        merged = merge_cookie_header("foo=1; cf_clearance=old", "cf_clearance=new; bar=2")

        self.assertEqual(merged, "foo=1; cf_clearance=old; bar=2")

    def test_apply_user_agent_updates_client_hints(self) -> None:
        headers = apply_user_agent({}, "Mozilla/5.0 Chrome/136.1.2.3 Safari/537.36")

        self.assertEqual(headers["user-agent"], "Mozilla/5.0 Chrome/136.1.2.3 Safari/537.36")
        self.assertIn('v="136"', headers["sec-ch-ua"])
        self.assertIn("136.1.2.3", headers["sec-ch-ua-full-version-list"])

    def test_registrar_headers_do_not_override_session_cookies(self) -> None:
        registrar = PlatformRegistrar()
        try:
            registrar.clearance_bundle = ClearanceBundle(
                cookies=(ClearanceCookie("cf_clearance", "abc", "auth.openai.com"),),
                cf_cookies="cf_clearance=abc",
                user_agent="Mozilla/5.0 Chrome/136.1.2.3 Safari/537.36",
                affinity_key="direct",
                clearance_host="auth.openai.com",
                created_at=0,
            )

            headers = registrar._json_headers("https://auth.openai.com/create-account/password")

            self.assertNotIn("cookie", {key.lower(): value for key, value in headers.items()})
            self.assertEqual(headers["user-agent"], registrar.clearance_bundle.user_agent)
        finally:
            registrar.close()


if __name__ == "__main__":
    unittest.main()

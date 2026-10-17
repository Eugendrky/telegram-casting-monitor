import unittest

from app.credentials import (
    OFFICIAL_ANDROID_API_HASH,
    OFFICIAL_ANDROID_API_ID,
    resolve_api_credentials,
)


class CredentialsTests(unittest.TestCase):
    def test_uses_official_android_when_env_empty(self) -> None:
        api_id, api_hash, fallback = resolve_api_credentials("", "")
        self.assertEqual(api_id, OFFICIAL_ANDROID_API_ID)
        self.assertEqual(api_hash, OFFICIAL_ANDROID_API_HASH)
        self.assertTrue(fallback)

    def test_prefers_explicit_keys(self) -> None:
        api_id, api_hash, fallback = resolve_api_credentials("2040", "abc")
        self.assertEqual(api_id, 2040)
        self.assertEqual(api_hash, "abc")
        self.assertFalse(fallback)

    def test_rejects_partial_keys(self) -> None:
        with self.assertRaises(ValueError):
            resolve_api_credentials("2040", "")


if __name__ == "__main__":
    unittest.main()

import unittest

from app.credentials import resolve_api_credentials


class CredentialsTests(unittest.TestCase):
    def test_explicit_credentials(self):
        self.assertEqual(resolve_api_credentials("123456", "secret"), (123456, "secret", False))

    def test_missing_credentials_rejected(self):
        for api_id, api_hash in (("", ""), ("123", ""), ("", "hash")):
            with self.subTest(api_id=api_id):
                with self.assertRaises(ValueError):
                    resolve_api_credentials(api_id, api_hash)

    def test_invalid_id_rejected(self):
        with self.assertRaises(ValueError):
            resolve_api_credentials("abc", "secret")

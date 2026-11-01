import unittest
from app.cloud_web_reader import same_account, select_peers, validate_posts

class WebQRGuards(unittest.TestCase):
    def test_identity_requires_exact_telegram_id(self):
        self.assertTrue(same_account("123456789",123456789))
        for other in ("123456788","00123456789",None,True,"",123456788):
            self.assertFalse(same_account(other,123456789))
    def test_unknown_chat_cannot_be_read(self):
        raw=[{"username":"moscowfilms","peer":"-123"},
             {"username":"privatechat","peer":"-987"}]
        self.assertEqual(select_peers(raw,{"moscowfilms"}),{"moscowfilms":"-123"})
    def test_only_recent_posts_are_accepted(self):
        now=1800000000
        raw=[{"id":10,"date":now-50,"text":"Кастинг"},
             {"id":11,"date":now-200000,"text":"Старое"}]
        self.assertEqual(validate_posts(raw,now=now,cursor=0),
                         [(10,now-50,"Кастинг")])

if __name__=="__main__":
    unittest.main()

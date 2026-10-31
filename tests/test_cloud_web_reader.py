import unittest
from app.cloud_web_reader import select_peers,validate_posts

class WebReaderTests(unittest.TestCase):
    def test_only_explicitly_approved_sources(self):
        peers=[{"username":"moscowfilms","peer":"-111"},{"username":"othergroup","peer":"-222"},{"username":"kinocekh","peer":"-333"},{"username":"moscowfilms","peer":"777"}]
        self.assertEqual(select_peers(peers,{"moscowfilms","kinocekh"}),{"kinocekh":"-333"})
    def test_recent_cursor_and_text_validation(self):
        now=1800000000
        raw=[{"id":10,"date":now-500,"text":"Кастинг актриса"},{"id":9,"date":now-500,"text":"Второй"},
             {"id":11,"date":now-200000,"text":"старый"},{"id":12,"date":now-20,"text":""}]
        self.assertEqual(validate_posts(raw,now=now,cursor=9),[(10,now-500,"Кастинг актриса")])
    def test_unexpected_payload_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_posts({},now=1800000000)

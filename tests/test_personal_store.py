import tempfile
import unittest
from pathlib import Path
from cryptography.fernet import Fernet
from app.bot_db import Database
from app.personal_store import PersonalStore
from app.session_cipher import SessionCipher

class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.tmp.name)/"db.sqlite")
        self.personal=PersonalStore(self.db)
        self.cipher=SessionCipher(Fernet.generate_key().decode())
        self.db.add_public("moscowfilms")
        self.db.add_public("kinocekh")
        for uid in (101,202):
            self.personal.save_session(uid,self.cipher.seal("session-"+str(uid)))
    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()
    def test_session_is_encrypted_and_isolated(self):
        encrypted=self.personal.session(101)
        self.assertNotIn(b"session-101",encrypted)
        self.assertEqual(self.cipher.open(encrypted),"session-101")
        self.assertNotEqual(self.personal.session(101),self.personal.session(202))
    def test_public_approval_is_required(self):
        self.assertEqual(self.personal.select_source(101,"@moscowfilms"),"moscowfilms")
        with self.assertRaises(ValueError):
            self.personal.select_source(202,"@untrusted_channel")
        self.assertEqual(self.personal.sources(202),[])
    def test_archive_dedup_delivery_and_deletion(self):
        self.personal.select_source(101,"@moscowfilms")
        self.personal.select_source(202,"@kinocekh")
        import time
        stamp=int(time.time())
        post="Кастинг Москва, ищем актрису 20-30"
        self.assertTrue(self.personal.save_casting(101,"moscowfilms",11,post,stamp,matched=True))
        self.assertFalse(self.personal.save_casting(101,"moscowfilms",11,post,stamp,matched=True))
        self.assertTrue(self.personal.save_casting(202,"kinocekh",22,post,stamp,matched=True))
        self.assertEqual(len(self.personal.pending()),2)
        self.personal.mark_delivery(101,"moscowfilms",11,"sent")
        self.assertEqual(len(self.personal.latest(101)),1)
        self.assertEqual(self.personal.latest(202),[])
        self.personal.forget(101)
        self.assertIsNone(self.personal.session(101))
        self.assertEqual(self.personal.sources(101),[])
        self.assertEqual(self.personal.latest(101),[])
        self.assertIsNotNone(self.personal.session(202))
        self.assertEqual(len(self.personal.pending()),1)
    def test_source_cursor_and_retry_isolation(self):
        self.personal.select_source(101,"moscowfilms")
        self.personal.select_source(202,"moscowfilms")
        self.personal.update_source(101,"moscowfilms",cursor=55,error=None,checked_at=100000)
        self.assertEqual(self.personal.sources(101)[0][1],55)
        self.assertEqual(self.personal.sources(202)[0][1],0)
    def test_bad_cipher_rejected(self):
        other=SessionCipher(Fernet.generate_key().decode())
        with self.assertRaises(ValueError):
            other.open(self.personal.session(101))

if __name__=="__main__":
    unittest.main()

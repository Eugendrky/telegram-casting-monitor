import tempfile
import unittest
from pathlib import Path
from app.bot_db import Database
from app.bot_runtime import Receiver
from app.junction_relay import normalized
from app.public_channels import source_id

class API:
    def __init__(self): self.messages=[]
    def send(self,chat_id,text,reply_markup=None): self.messages.append((chat_id,text))
    def call(self,*args,**kwargs): return True

class RelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.tmp.name)/"test.db")
        self.api=API()
        self.receiver=Receiver(self.api,self.db,999)
        self.sid=self.db.add_public("moscowfilms")
        self.group=-100987654321
        self.junction=567890
    def tearDown(self):
        self.db.close();self.tmp.cleanup()

    def message(self,id=10,uid=None,text="Кастинг Москва ищем актрису 30 лет https://t.me/moscowfilms/42"):
        return {"chat":{"id":self.group,"type":"supergroup","title":"Relay"},
                "from":{"id":uid or self.junction,"is_bot":True},
                "message_id":id,"date":1780000000,"text":text}

    def bind(self):
        self.receiver.process({"message":{
            "chat":{"id":self.group,"type":"supergroup"},
            "from":{"id":999},
            "text":"/junction_bind @moscowfilms",
            "reply_to_message":{"from":{"id":self.junction,"is_bot":True}}
        }})
    def test_owner_binds_source_only_with_verified_reply(self):
        self.receiver.process({"message":{
            "chat":{"id":self.group,"type":"supergroup"},
            "from":{"id":222},"text":"/junction_bind @moscowfilms",
            "reply_to_message":{"from":{"id":self.junction,"is_bot":True}}
        }})
        self.assertIsNone(self.db.junction_binding(self.group))
        self.bind()
        self.assertEqual(self.db.junction_binding(self.group)[0],self.sid)
    def test_reject_untrusted_bot_and_mismatched_origin(self):
        self.bind()
        self.receiver.process({"message":self.message(uid=901)})
        self.receiver.process({"message":self.message(id=11,text="Кастинг Москва https://t.me/kinocekh/7")})
        self.assertEqual(self.db.count_source_matches(self.sid),0)
    def test_forward_origin_with_matching_channel(self):
        self.bind()
        m=self.message(text="Кастинг Москва ищем актрису 30 лет")
        m["forward_origin"]={"type":"channel","chat":{"username":"moscowfilms"},
                              "message_id":42,"date":1780000000}
        post=normalized(m,self.db.junction_binding(self.group))
        self.assertEqual(post["message_id"],42)
    def test_duplicate_original_link_not_redelivered(self):
        self.db.register(101)
        self.bind()
        self.receiver.process({"message":self.message()})
        self.receiver.process({"message":self.message(id=11)})
        self.assertEqual(self.db.count_source_matches(self.sid),1)
        self.assertEqual(len(self.db.pending()),1)
        self.receiver.flush()
        self.assertEqual(len(self.db.latest(101)),1)
    def test_unbind_immediately_stops_relay(self):
        self.bind()
        self.receiver.process({"message":{
            "chat":{"id":self.group,"type":"supergroup"},
            "from":{"id":999},"text":"/junction_unbind"
        }})
        self.assertIsNone(self.db.junction_binding(self.group))
        self.receiver.process({"message":self.message()})
        self.assertEqual(self.db.count_source_matches(self.sid),0)

if __name__=="__main__":
    unittest.main()

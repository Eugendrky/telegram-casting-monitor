import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from app.authorized_reader import scan_source, configured
from app.bot_db import Database
from app.bot_runtime import Receiver

class API:
    def __init__(self): self.messages=[]
    def send(self,uid,text,reply_markup=None): self.messages.append((uid,text))
    def call(self,*args,**kwargs): return True

class Client:
    def __init__(self,messages): self.messages=messages;self.lookups=[]
    async def get_input_entity(self,username):
        self.lookups.append(username);return username
    async def iter_messages(self,entity,limit=80,min_id=0):
        for m in self.messages:
            if m.id>min_id:
                yield m

class ClientReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_profile_delivery_cursor_and_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Database(Path(tmp)/"db.sqlite");bot=Receiver(API(),db,999)
            db.add_public("moscowfilms")
            bot.handle_private({"from":{"id":11},"text":"/start"})
            bot.handle_private({"from":{"id":11},"text":"/city Москва"})
            bot.handle_private({"from":{"id":11},"text":"/gender женский"})
            bot.handle_private({"from":{"id":11},"text":"/age 32"})
            bot.handle_private({"from":{"id":11},"text":"/height 166"})
            msg=SimpleNamespace(id=17,raw_text="Кастинг Москва. Ищем актрису 30-35 лет",
                   date=datetime.now(timezone.utc))
            fake=Client([msg])
            found=await scan_source(fake,db,bot,"moscowfilms")
            self.assertEqual(found,1)
            self.assertEqual(len(db.pending()),1)
            bot.flush()
            sid=db.add_public("moscowfilms")
            self.assertEqual(db.client_cursor(sid),17)
            db.update_client_status(sid,error=None,retry_after=900,now=0)
            db.connection.execute("UPDATE public_state SET client_next_scan=0")
            db.connection.commit()
            await scan_source(fake,db,bot,"moscowfilms")
            self.assertEqual(len(db.latest(11)),1)
            self.assertEqual(fake.lookups,["moscowfilms","moscowfilms"])
            db.close()

    async def test_scan_does_not_read_non_selected_dialogs(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Database(Path(tmp)/"db.sqlite")
            sid=db.add_public("kinocekh")
            db.enable_source(sid,False)
            self.assertFalse(configured("","",""))
            self.assertEqual(db.client_cursor(sid),0)
            db.close()

if __name__=="__main__":
    unittest.main()

"""Bot API casting receiver. No user login, dialog scraping, or historical reads."""
from __future__ import annotations
import asyncio
import logging
import os
import time
from pathlib import Path
from app.bot_db import Database
from app.bot_http import BotAPI, TelegramError
from app.config import Profile
from app.matcher import match_post
from app.public_channels import normalize_channel, monitor_public

log = logging.getLogger("casting.bot")
HELP = (
    "🎬 Поиск кастингов из публичных Telegram-каналов\n\n"
    "Я самостоятельно проверяю выбранные публичные каналы, "
    "отбираю объявления по вашему профилю и присылаю подходящие.\n\n"
    "/profile — мой профиль\n"
    "/city Москва — город\n"
    "/gender male|female|any — пол\n"
    "/age 25 — возраст\n"
    "/height 170 — рост\n"
    "/sources — выбранные каналы и подписки\n"
    "/castings — мои последние находки\n"
    "/pause и /resume — уведомления\n"
    "/status — состояние\n/suggest — предложить канал\n\n"
    "Для администратора: /addsource @channel и /publicstatus"
)

def link_for(chat: dict, message_id: int) -> str | None:
    username = chat.get("username")
    if username:
        return f"https://t.me/{username}/{message_id}"
    chat_id = str(chat.get("id",""))
    if chat_id.startswith("-100"):
        return f"https://t.me/c/{chat_id[4:]}/{message_id}"
    return None

def profile_text(profile: dict):
    return (f"Город: {profile['city'] or 'любой'}\n"
            f"Пол: {profile['gender']}\nВозраст: {profile['age'] or 'любой'}\n"
            f"Рост: {profile['height_cm'] or 'любой'}\n"
            f"Уведомления: {'включены' if profile['active'] else 'выключены'}")

def parse_setting(command: str, value: str):
    if command == "/city":
        return "city", value.strip()[:90]
    if command == "/gender" and value in ("male","female","any"):
        return "gender",value
    if command in ("/age","/height"):
        try:
            number=int(value)
        except ValueError:
            return None
        if command == "/age" and 4 <= number <= 95:
            return "age",number
        if command == "/height" and 100 <= number <= 230:
            return "height_cm",number
    return None

def profile_menu():
    return {"inline_keyboard":[
        [{"text":"🎂 Возраст","callback_data":"profile:age"},
         {"text":"📍 Город","callback_data":"profile:city"}],
        [{"text":"👤 Пол","callback_data":"profile:gender"},
         {"text":"📏 Рост","callback_data":"profile:height"}],
        [{"text":"🏠 Главное меню","callback_data":"menu:home"}]
    ]}

def menu():
    return {"inline_keyboard":[
        [{"text":"📨 Предложить канал","callback_data":"suggest:start"},
         {"text":"📚 Источники","callback_data":"menu:sources"}],
        [{"text":"👤 Мой профиль","callback_data":"menu:profile"},
         {"text":"🎬 Кастинги","callback_data":"menu:castings"}]
    ]}

class Receiver:
    def __init__(self, api: BotAPI, db: Database, owner_id: int):
        self.api,self.db,self.owner_id=api,db,owner_id

    def source_list(self,user_id: int):
        lines=[]
        for chat_id,title,kind,enabled,joined,username in self.db.sources():
            if not joined:
                continue
            status="✅" if enabled else "⏳"
            following="🔔" if self.db.following(user_id,chat_id) else "🔕"
            lines.append(f"{status} {following} {title}\nID: {chat_id}")
        if not lines:
            return "Источников пока нет. Администратор может добавить /addsource @channel"
        return ("Источники:\n"+ "\n".join(lines[:35])+
                "\n\n/follow ID и /unfollow ID — подписка на конкретный источник"+
                ("\n/enable ID и /disable ID — разрешения администратора" if user_id==self.owner_id else ""))

    def handle_private(self, message: dict):
        user_id=message.get("from",{}).get("id")
        if not isinstance(user_id,int): return
        text=(message.get("text") or "").strip()
        if not text.startswith("/"):
            prompt=self.db.profile_prompt(user_id)
            if prompt:
                parsed=parse_setting("/"+prompt,text)
                if parsed is None:
                    self.api.send(user_id,"Не удалось распознать значение. Для отмены отправьте /cancel.")
                else:
                    self.db.set_profile_prompt(user_id,None)
                    self.db.update_profile(user_id,*parsed)
                    self.api.send(user_id,"✅ Сохранено.\n"+profile_text(self.db.profile(user_id)),
                                  reply_markup=profile_menu())
                return
            if self.db.awaiting_suggestion(user_id):
                self.db.set_waiting_for_suggestion(user_id,False)
                self.submit_suggestion(user_id,text)
            return
        cmd,*rest=text.split(maxsplit=1)
        cmd=cmd.split("@")[0].lower()
        arg=rest[0].strip() if rest else ""
        if cmd=="/cancel":
            self.db.set_profile_prompt(user_id,None)
            self.db.set_waiting_for_suggestion(user_id,False)
            self.api.send(user_id,"Действие отменено.",reply_markup=menu())
        elif cmd=="/addsource":
            if self.owner_id <= 0 or user_id != self.owner_id:
                self.api.send(user_id,"Добавлять каналы может администратор.")
                return
            try:
                channel=normalize_channel(arg)
                channel_id=self.db.add_public(channel)
            except ValueError as exc:
                self.api.send(user_id,str(exc))
                return
            self.api.send(user_id,f"✅ Добавлен публичный канал @{channel}. ID: {channel_id}. Проверю доступность при ближайшем цикле.")
        elif cmd=="/publicstatus":
            if user_id != self.owner_id:
                return
            rows=self.db.public_status()
            self.api.send(user_id,"\\n".join(f"{x[0]}: последний пост {x[1]}, ошибка {x[2] or 'нет'}" for x in rows)[:3700] or "Публичных каналов пока нет.")
        elif cmd=="/start":
            self.db.register(user_id)
            self.api.send(user_id,HELP + f"\n\nВаш Telegram ID: {user_id}" +
                          ("\nУкажите BOT_OWNER_ID в Railway, чтобы управлять источниками." if self.owner_id <= 0 else ""),reply_markup=menu())
        elif cmd=="/help": self.api.send(user_id,HELP)
        elif cmd=="/suggest":
            if arg: self.submit_suggestion(user_id,arg)
            else:
                self.db.set_waiting_for_suggestion(user_id,True)
                self.api.send(user_id,"Пришлите ссылку на публичный Telegram-канал.")
        elif cmd=="/admin":
            if user_id!=self.owner_id: return
            counts=self.db.admin_counts()
            feedback=self.db.feedback_stats()
            self.api.send(user_id,
                "🛡 Админка\nПользователей: {}\nИсточников: {}\nПубликаций: {}\nОшибок: {}\nПредложений: {}".format(
                    counts["users"],counts["sources"],counts["posts"],counts["errors"],counts["pending"]) +
                "\n👍 Подходит: {}  👎 Не подходит: {}".format(
                    feedback.get("relevant",0),feedback.get("irrelevant",0)),
                reply_markup={"inline_keyboard":[[{"text":"📥 Предложения","callback_data":"admin:queue:0"}]]})
        elif cmd=="/profile":
            self.db.register(user_id)
            self.api.send(user_id,profile_text(self.db.profile(user_id)),reply_markup=profile_menu())
        elif cmd in ("/city","/gender","/age","/height"):
            if not arg:
                field=cmd[1:]
                self.db.set_profile_prompt(user_id,field)
                prompts={"city":"Напишите город, например Москва.",
                         "gender":"Укажите male, female или any.",
                         "age":"Сколько вам лет? Напишите число от 4 до 95.",
                         "height":"Укажите рост в сантиметрах, от 100 до 230."}
                self.api.send(user_id,prompts[field]+" Для отмены /cancel")
                return
            parsed=parse_setting(cmd,arg)
            if parsed is None: self.api.send(user_id,"Неверное значение. Используйте /help")
            else:
                self.db.update_profile(user_id,*parsed)
                self.api.send(user_id,"Настройки сохранены.\n"+profile_text(self.db.profile(user_id)))
        elif cmd in ("/pause","/resume"):
            self.db.update_profile(user_id,"active",int(cmd=="/resume"))
            self.api.send(user_id,"Уведомления включены." if cmd=="/resume" else "Уведомления выключены.")
        elif cmd=="/sources":
            self.db.register(user_id)
            self.api.send(user_id,self.source_list(user_id))
        elif cmd in ("/follow","/unfollow","/enable","/disable"):
            try: chat_id=int(arg)
            except ValueError:
                self.api.send(user_id,"Укажите числовой ID из /sources.");return
            if cmd in ("/enable","/disable"):
                if user_id!=self.owner_id:
                    self.api.send(user_id,"Доступно только администратору.");return
                ok=self.db.enable_source(chat_id,cmd=="/enable")
                self.api.send(user_id,"Источник обновлён." if ok else "Источник недоступен. Добавьте бота в чат.")
            else:
                if not self.db.source(chat_id) or not self.db.source(chat_id)[4]:
                    self.api.send(user_id,"Нет такого доступного источника.");return
                self.db.register(user_id)
                self.db.follow(user_id,chat_id,cmd=="/follow")
                self.api.send(user_id,"Подписка изменена.")
        elif cmd=="/castings":
            rows=self.db.latest(user_id)
            lines=[f"🎬 {title}\n{body[:190]}\n{url or ''}" for title,body,url in rows]
            self.api.send(user_id,"\n\n".join(lines)[:3800] if lines else "Пока нет подходящих кастингов.")
        elif cmd=="/status":
            self.api.send(user_id,f"Бот работает. Источников: {len(self.db.sources())}. Для настройки /profile")
        else: self.api.send(user_id,HELP)

    def submit_suggestion(self, user_id: int, raw: str):
        try:
            channel=normalize_channel(raw)
            if any(row[5]==channel for row in self.db.sources()):
                self.api.send(user_id,"Этот канал уже включён в каталог.")
                return
            suggestion_id,status=self.db.propose(user_id,channel)
        except ValueError as exc:
            self.api.send(user_id,str(exc))
            return
        if status=="pending":
            self.api.send(user_id,"✅ Канал отправлен на проверку.")
            if self.owner_id>0 and user_id!=self.owner_id:
                try:
                    self.api.send(self.owner_id,"Предложен канал @{} пользователем {}".format(channel,user_id),
                      reply_markup={"inline_keyboard":[[
                        {"text":"✅ Одобрить","callback_data":"review:yes:{}".format(suggestion_id)},
                        {"text":"❌ Отклонить","callback_data":"review:no:{}".format(suggestion_id)}]]})
                except TelegramError as exc:
                    log.warning("Suggestion notification: API %s",exc.code)
        else:
            self.api.send(user_id,"Этот канал уже рассматривался. Статус: "+status)

    def handle_callback(self, event: dict):
        user_id=(event.get("from") or {}).get("id")
        if not isinstance(user_id,int): return
        data=event.get("data","")
        callback_id=event.get("id")
        if callback_id:
            try: self.api.call("answerCallbackQuery",callback_query_id=callback_id)
            except TelegramError: pass
        if data=="suggest:start":
            self.db.set_waiting_for_suggestion(user_id,True)
            self.api.send(user_id,"Пришлите ссылку на публичный Telegram-канал.")
        elif data=="menu:sources":
            self.api.send(user_id,self.source_list(user_id))
        elif data.startswith("profile:"):
            field=data.partition(":")[2]
            if field in ("city","age","height","gender"):
                self.db.register(user_id)
                self.db.set_profile_prompt(user_id,field)
                prompts={"city":"Напишите ваш город.","age":"Сколько вам лет?",
                         "height":"Укажите рост в сантиметрах.",
                         "gender":"Укажите male, female или any."}
                self.api.send(user_id,prompts[field]+" Отмена: /cancel")
        elif data=="menu:home":
            self.api.send(user_id,HELP,reply_markup=menu())
        elif data=="menu:profile":
            self.db.register(user_id)
            self.api.send(user_id,profile_text(self.db.profile(user_id)))
        elif data=="menu:castings":
            rows=self.db.latest(user_id)
            self.api.send(user_id,"\n\n".join(
                "🎬 {}\n{}\n{}".format(title,body[:180],url or "") for title,body,url in rows)[:3700]
                if rows else "Подходящих кастингов пока нет.")
        elif data.startswith("admin:queue:"):
            if user_id!=self.owner_id:return
            try: page=max(0,min(int(data.split(":")[2]),100))
            except (IndexError,ValueError):return
            rows=self.db.pending_suggestions(6,page*6)
            if not rows: self.api.send(user_id,"📥 Новых предложений нет.")
            for proposal_id,author,username,created in rows:
                self.api.send(user_id,"#{}: @{}\nОт: {}\n{}".format(
                    proposal_id,username,author,created),
                    reply_markup={"inline_keyboard":[[
                        {"text":"✅ Одобрить","callback_data":"review:yes:{}".format(proposal_id)},
                        {"text":"❌ Отклонить","callback_data":"review:no:{}".format(proposal_id)}]]})
            if len(rows)==6:
                self.api.send(user_id,"Следующая страница",
                    reply_markup={"inline_keyboard":[[{"text":"Далее ➡️",
                      "callback_data":"admin:queue:{}".format(page+1)}]]})
        elif data.startswith("feedback:"):
            parts=data.split(":")
            if len(parts)!=4 or parts[1] not in ("yes","no"): return
            try:
                chat_id,message_id=int(parts[2]),int(parts[3])
            except ValueError:
                return
            approved=self.db.add_feedback(
                user_id,chat_id,message_id,
                "relevant" if parts[1]=="yes" else "irrelevant"
            )
            self.api.send(user_id,
                "Спасибо! Оценка сохранена." if approved else "Объявление не найдено в истории.")
        elif data.startswith("review:"):
            if user_id!=self.owner_id:return
            parts=data.split(":")
            if len(parts)!=3 or parts[1] not in ("yes","no"):return
            try: proposal_id=int(parts[2])
            except ValueError:return
            result=self.db.review_suggestion(proposal_id,parts[1]=="yes")
            if result is None:
                self.api.send(user_id,"Предложение уже обработано.")
                return
            author,username,status,chat_id=result
            self.api.send(user_id,"@{}: {}".format(username,status))
            try:
                self.api.send(author,"Канал @{}: {}".format(username,
                    "добавлен в каталог" if status=="approved" else "отклонён"))
            except TelegramError as exc:
                log.warning("Suggestion response API %s",exc.code)

    def handle_membership(self, event: dict):
        chat=event.get("chat") or {}
        new=event.get("new_chat_member") or {}
        status=new.get("status","")
        joined=status in ("member","administrator")
        self.db.set_source(chat["id"],chat.get("title","Источник"),
                           chat.get("username"),chat.get("type"),joined)
        if joined and self.owner_id > 0:
            try:
                self.api.send(self.owner_id,f"Новый источник: {chat.get('title')}\n"
                              f"ID: {chat['id']}\nЧтобы разрешить обработку: /enable {chat['id']}")
            except TelegramError as exc:
                log.warning("Owner notification failed: %s",exc.code)

    def handle_post(self, message: dict):
        chat=message.get("chat") or {}
        chat_id=chat.get("id")
        source=self.db.source(chat_id)
        if source is None:
            self.db.set_source(chat_id,chat.get("title","Источник"),
                               chat.get("username"),chat.get("type"),True)
            return  # New source requires explicit owner approval.
        if not source[3] or not source[4]: return
        if (message.get("from") or {}).get("is_bot"): return
        body=message.get("text") or message.get("caption") or ""
        if not body.strip(): return
        message_id=message.get("message_id")
        if not isinstance(message_id,int): return
        for uid,city,gender,age,height in self.db.subscribers(chat_id):
            profile=Profile(city=city,gender=gender,age=age,height_cm=height,
                            kids_ok=False,include_keywords=[],exclude_keywords=[
                            "набор закрыт","уже набрали","итоги кастинга"])
            result=match_post(body,profile)
            if result.matched:
                self.db.record_post(chat_id,message_id,source[1],body,
                                    link_for(chat,message_id),message.get("date"))
                self.db.queue(uid,chat_id,message_id)

    def process(self, update: dict):
        if "callback_query" in update:
            self.handle_callback(update["callback_query"])
            return
        if "my_chat_member" in update:
            self.handle_membership(update["my_chat_member"])
        message=update.get("channel_post") or update.get("message")
        if message:
            chat=message.get("chat") or {}
            if chat.get("type")=="private":
                self.handle_private(message)
            elif chat.get("type") in ("group","supergroup","channel"):
                self.handle_post(message)

    def flush(self):
        for uid,chat_id,message_id,title,body,url in self.db.pending():
            content=f"🎬 {title}\n\n{body[:3200]}\n\n{url or ''}"
            try:
                self.api.send(uid,content,reply_markup={"inline_keyboard":[[
                    {"text":"👍 Подходит","callback_data":"feedback:yes:{}:{}".format(chat_id,message_id)},
                    {"text":"👎 Не подходит","callback_data":"feedback:no:{}:{}".format(chat_id,message_id)}
                ]]})
                self.db.mark_delivery(uid,chat_id,message_id,"sent")
            except TelegramError as exc:
                if exc.code in (403,400):
                    self.db.mark_delivery(uid,chat_id,message_id,"blocked",str(exc.code))
                elif exc.code==429:
                    log.warning("Telegram Bot API rate limit; pausing delivery")
                    break
                else:
                    self.db.mark_delivery(uid,chat_id,message_id,"failed",str(exc.code))

async def run():
    token=os.getenv("BOT_TOKEN","").strip()
    owner=int(os.getenv("BOT_OWNER_ID","0") or "0")
    path=Path(os.getenv("SQLITE_PATH","/data/castings.db"))
    db=Database(path)
    # Owner-supplied initial public sources. Existing approval/settings persist.
    for name in ("castingiot4000r", "moscowfilms", "kinocekh"):
        if not any(item[5] == name for item in db.sources()):
            db.add_public(name)
    api=BotAPI(token)
    receiver=Receiver(api,db,owner)
    try:
        await asyncio.to_thread(api.call,"deleteWebhook",drop_pending_updates=False)
        await asyncio.to_thread(api.call,"getMe")
        log.info("Bot API started: no user session used")
        public_task=asyncio.create_task(monitor_public(
            db,receiver,interval_seconds=int(os.getenv("PUBLIC_SCAN_INTERVAL_SECONDS","1800")),
            lookback_hours=int(os.getenv("PUBLIC_LOOKBACK_HOURS","48"))))
        while True:
            try:
                updates=await asyncio.to_thread(api.get_updates,db.cursor()+1)
                for update in updates:
                    try:
                        await asyncio.to_thread(receiver.process,update)
                    except TelegramError as exc:
                        log.warning("Update %s failed with Bot API %s",
                                    update.get("update_id"),exc.code)
                        if exc.code==429:
                            await asyncio.sleep(max(2,exc.retry_after))
                        break
                    except Exception:
                        log.exception("Update handler failed")
                        break
                    else: db.advance(update["update_id"])
                await asyncio.to_thread(receiver.flush)
            except TelegramError as exc:
                log.warning("Telegram API error %s",exc.code)
                await asyncio.sleep(max(5,exc.retry_after))
            except Exception:
                log.exception("Polling loop failure")
                await asyncio.sleep(10)
    finally:
        if "public_task" in locals():
            public_task.cancel()
            await asyncio.gather(public_task,return_exceptions=True)
        db.close()

def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())

if __name__=="__main__":main()

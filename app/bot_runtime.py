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
from app.casting_quality import actionable
from app.public_channels import normalize_channel, monitor_public
from app.authorized_reader import run_authorized_reader
from app.locale_ru import suggestion_status, readable_time, source_error
from app.source_readiness import readiness_text, empty_castings_text
from app.owner_web_pilot import OwnerWebPilot
from app.quality_guardian import inspect as inspect_quality, render as render_quality, run_guardian

log = logging.getLogger("casting.bot")
HELP = (
    "🎬 Поиск кастингов из публичных Telegram-каналов\n\n"
    "Я самостоятельно проверяю выбранные публичные каналы, "
    "отбираю объявления по вашему профилю и присылаю подходящие.\n\n"
    "/profile — мой профиль\n"
    "/city Москва — город\n"
    "/gender — пол (выбор кнопкой)\n"
    "/age 25 — возраст\n"
    "/height 170 — рост\n"
    "/sources — выбранные каналы и подписки\n"
    "/castings — мои последние находки\n"
    "/pause и /resume — уведомления\n"
    "/status — состояние\n/ready — доступность источников\n/scan — проверить источники\n/suggest — предложить канал\n\n"
    "Предложить новый канал можно через /suggest."
)
ADMIN_HELP = ("\n\nДля администратора: /admin, /addsource "
              "(со ссылкой на канал), /publicstatus.")

def link_for(chat: dict, message_id: int) -> str | None:
    username = chat.get("username")
    if username:
        return f"https://t.me/{username}/{message_id}"
    chat_id = str(chat.get("id",""))
    if chat_id.startswith("-100"):
        return f"https://t.me/c/{chat_id[4:]}/{message_id}"
    return None

GENDER_LABELS = {"male": "Мужской", "female": "Женский", "any": "Любой"}
GENDER_CHOICES = {
    "male": "male", "мужской": "male", "мужчина": "male", "м": "male",
    "female": "female", "женский": "female", "женщина": "female", "ж": "female",
    "any": "any", "любой": "any", "любая": "any", "все": "any",
    "без разницы": "any", "неважно": "any",
}

def gender_menu():
    return {"inline_keyboard": [
        [{"text": "👨 Мужской", "callback_data": "gender:set:male"},
         {"text": "👩 Женский", "callback_data": "gender:set:female"}],
        [{"text": "Любой", "callback_data": "gender:set:any"}],
        [{"text": "↩️ Назад в профиль", "callback_data": "menu:profile"}],
    ]}

def profile_text(profile: dict):
    return (f"Город: {profile['city'] or 'любой'}\n"
            f"Пол: {GENDER_LABELS.get(profile['gender'], 'Любой')}\nВозраст: {profile['age'] or 'любой'}\n"
            f"Рост: {profile['height_cm'] or 'любой'}\n"
            f"Уведомления: {'включены' if profile['active'] else 'выключены'}")

def parse_setting(command: str, value: str):
    if command == "/city":
        return "city", value.strip()[:90]
    if command == "/gender":
        selected = GENDER_CHOICES.get(value.strip().casefold())
        return ("gender", selected) if selected is not None else None
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
        [{"text":"🔎 Проверить источники","callback_data":"scan:now"}],
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
            lines.append(f"{status} {following} {title}\nКод источника: {chat_id}")
        if not lines:
            return "Источников пока нет. Администратор может добавить публичный канал командой /addsource."
        return ("Источники:\n"+ "\n".join(lines[:35])+
                "\n\nДля подписки: /follow с кодом источника. Для отключения: /unfollow с кодом источника."+
                ("\nДля управления: /enable или /disable с кодом источника." if user_id==self.owner_id else ""))

    def handle_private(self, message: dict):
        user_id=message.get("from",{}).get("id")
        if not isinstance(user_id,int): return
        text=(message.get("text") or "").strip()
        if not text.startswith("/"):
            prompt=self.db.profile_prompt(user_id)
            if prompt:
                parsed=parse_setting("/"+prompt,text)
                if parsed is None:
                    if prompt == "gender":
                        self.api.send(user_id,"Выберите пол кнопкой или напишите: мужской, женский, любой.",
                                      reply_markup=gender_menu())
                    else:
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
            self.api.send(user_id,f"✅ Добавлен публичный канал @{channel}. Код источника: {channel_id}. Проверю доступность при ближайшей проверке.")
        elif cmd=="/publicstatus":
            if user_id != self.owner_id:
                return
            rows=self.db.public_status()
            lines=[
                f"{x[0]} | последний пост №{x[1]} | на странице: {x[6]}, новых: {x[7]}, подошло: {x[8]}\n"
                f"Проверка: {readable_time(x[4])} | успешная: {readable_time(x[5])}\n"
                f"Ошибка: {source_error(x[2])}"
                for x in rows
            ]
            self.api.send(user_id,"\n\n".join(lines)[:3800] or "Публичных каналов пока нет.")
        elif cmd=="/start":
            self.db.register(user_id)
            intro = HELP
            if user_id == self.owner_id and self.owner_id > 0:
                intro += ADMIN_HELP
            elif self.owner_id <= 0:
                intro += f"\n\nВаш идентификатор в Telegram: {user_id}. Укажите его в настройке BOT_OWNER_ID."
            self.api.send(user_id,intro,reply_markup=menu())
        elif cmd=="/help": self.api.send(user_id,HELP + (ADMIN_HELP if user_id==self.owner_id and self.owner_id>0 else ""),reply_markup=menu())
        elif cmd=="/suggest":
            if arg: self.submit_suggestion(user_id,arg)
            else:
                self.db.set_waiting_for_suggestion(user_id,True)
                self.api.send(user_id,"Пришлите ссылку на публичный Telegram-канал.")
        elif cmd=="/admin":
            if user_id!=self.owner_id: return
            counts=self.db.admin_counts()
            feedback=self.db.feedback_stats()
            deliveries=self.db.delivery_stats()
            self.api.send(user_id,
                "🛡 Админка\nПользователей: {}\nИсточников: {}\nПубликаций: {}\nОшибок: {}\nПредложений: {}".format(
                    counts["users"],counts["sources"],counts["posts"],counts["errors"],counts["pending"]) +
                "\n👍 Подходит: {}  👎 Не подходит: {}".format(
                    feedback.get("relevant",0),feedback.get("irrelevant",0)) +
                "\nДоставлено: {} | ждут: {} | ошибок: {}".format(
                    deliveries.get("sent",0),deliveries.get("pending",0),deliveries.get("failed",0)),
                reply_markup={"inline_keyboard":[
                    [{"text":"📥 Предложения","callback_data":"admin:queue:0"}],
                    [{"text":"📡 Состояние источников","callback_data":"admin:sources"}]
                ]})
        elif cmd=="/profile":
            self.db.register(user_id)
            self.api.send(user_id,profile_text(self.db.profile(user_id)),reply_markup=profile_menu())
        elif cmd in ("/city","/gender","/age","/height"):
            if not arg:
                field=cmd[1:]
                self.db.set_profile_prompt(user_id,field)
                prompts={"city":"Напишите город, например Москва.",
                         "gender":"Выберите пол: мужской, женский или любой.",
                         "age":"Сколько вам лет? Напишите число от 4 до 95.",
                         "height":"Укажите рост в сантиметрах, от 100 до 230."}
                self.api.send(user_id,prompts[field]+" Для отмены /cancel",
                              reply_markup=gender_menu() if field=="gender" else None)
                return
            parsed=parse_setting(cmd,arg)
            if parsed is None:
                self.api.send(user_id,
                    "Выберите мужской, женский или любой." if cmd=="/gender"
                    else "Неверное значение. Используйте /help",
                    reply_markup=gender_menu() if cmd=="/gender" else None)
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
                self.api.send(user_id,"Укажите числовой код источника из /sources.");return
            if cmd in ("/enable","/disable"):
                if user_id!=self.owner_id:
                    self.api.send(user_id,"Доступно только администратору.");return
                ok=self.db.enable_source(chat_id,cmd=="/enable")
                self.api.send(user_id,"Источник обновлён." if ok else "Источник недоступен. Проверьте его наличие в каталоге.")
            else:
                if not self.db.source(chat_id) or not self.db.source(chat_id)[4]:
                    self.api.send(user_id,"Источник с таким кодом не найден.");return
                self.db.register(user_id)
                self.db.follow(user_id,chat_id,cmd=="/follow")
                self.api.send(user_id,"Подписка изменена.")
        elif cmd=="/scan":
            self.request_scan(user_id)
        elif cmd=="/castings":
            self.refresh_user(user_id)
            self.flush()
            rows=self.db.latest(user_id)
            lines=[f"🎬 {title}\n{body[:190]}\n{url or ''}" for title,body,url in rows]
            self.api.send(user_id,"\n\n".join(lines)[:3800] if lines else empty_castings_text(self.db))
        elif cmd=="/selftest":
            if user_id!=self.owner_id or self.owner_id<=0:
                return
            from app.casting_quality import actionable
            from app.matcher import match_post
            from app.config import Profile
            example="Кастинг в Москве. Ищем актрису 25-35 лет для съёмок рекламы."
            profile=Profile(city="Москва",gender="female",age=30,
                            height_cm=170,kids_ok=False,
                            include_keywords=[],exclude_keywords=[])
            eligible,reason=actionable(example)
            result=match_post(example,profile)
            state="✅ Успешно" if eligible and result.matched else "❌ Ошибка фильтра"
            self.api.send(user_id,"🧪 Самопроверка\n"+
                state+"\nДоставка ответа: успешно (это сообщение)"+
                "\nФильтрация: "+reason+
                "\nПроверка профиля: "+("совпадение" if result.matched else "не совпало")+
                "\nБаза: подключена"+
                "\nИсточники и QR: отдельно смотрите /ready"+
                "\nТестовые объявления в базу не записывались.")
        elif cmd=="/delete_me":
            self.api.send(user_id,
                "Удалить вашу анкету, подписки, оценки и историю доставок? "
                "Публикации других пользователей останутся в общей базе.",
                reply_markup={"inline_keyboard":[[
                    {"text":"Да, удалить мои данные","callback_data":"privacy:confirm"},
                    {"text":"Отмена","callback_data":"privacy:cancel"}
                ]]})
        elif cmd=="/guardian":
            if user_id==self.owner_id and self.owner_id>0:
                self.api.send(user_id,render_quality(inspect_quality(self.db)))
        elif cmd=="/ready":
            self.api.send(user_id,readiness_text(self.db))
        elif cmd=="/status":
            states=self.db.public_status()
            checked=sum(1 for row in states if row[4])
            failed=sum(1 for row in states if row[2])
            self.api.send(user_id,
                f"Бот работает. В каталоге {len(self.db.sources())} источников. "
                f"Веб-проверка проводилась для {checked} из {len(states)} публичных источников. "
                f"С ошибками: {failed}.\n"
                "Подборка обновляется после проверки публичных страниц. "
                "Не все Telegram-группы предоставляют сообщения через веб-страницы.")
        else: self.api.send(user_id,HELP)

    def request_scan(self, user_id: int):
        self.db.register(user_id)
        info=self.db.public_status()
        if info and all(row[2] for row in info):
            self.api.send(user_id,"Все публичные источники сейчас недоступны. "
                "Повторный запрос не обойдёт ограничения Telegram. Подробности: /ready")
            return
        count, retry_after = self.db.request_manual_scan()
        if count:
            self.api.send(user_id,
                f"🔎 Запланирована проверка {count} публичных источников. "
                "Проверка выполняется последовательно; найденные подходящие объявления "
                "поступят отдельными сообщениями. Результат можно проверить через /status.")
        else:
            wait = max(1, (retry_after+59)//60)
            self.api.send(user_id,
                f"Повторная проверка пока недоступна. Следующая попытка примерно через {wait} мин. "
                "Автоматическая проверка продолжает работать.")

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
            self.api.send(user_id,"Этот канал уже рассматривался. Статус: "+suggestion_status(status))

    def handle_callback(self, event: dict):
        user_id=(event.get("from") or {}).get("id")
        if not isinstance(user_id,int): return
        data=event.get("data","")
        callback_id=event.get("id")
        if callback_id:
            try: self.api.call("answerCallbackQuery",callback_query_id=callback_id)
            except TelegramError: pass
        if data=="privacy:confirm":
            self.db.delete_user_data(user_id)
            self.api.send(user_id,"Ваш профиль, подписки и история уведомлений удалены. "
                                 "Команда /start создаст новую анкету.")
        elif data=="privacy:cancel":
            self.api.send(user_id,"Удаление отменено.")
        elif data=="scan:now":
            self.request_scan(user_id)
        elif data=="suggest:start":
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
                         "gender":"Выберите пол: мужской, женский или любой."}
                self.api.send(user_id,prompts[field]+" Отмена: /cancel",
                              reply_markup=gender_menu() if field=="gender" else None)
        elif data.startswith("gender:set:"):
            gender=data.partition("gender:set:")[2]
            if gender not in GENDER_LABELS:
                return
            self.db.set_profile_prompt(user_id,None)
            self.db.update_profile(user_id,"gender",gender)
            self.api.send(user_id,"✅ Пол: "+GENDER_LABELS[gender]+"\n"+
                          profile_text(self.db.profile(user_id)),reply_markup=profile_menu())
        elif data=="menu:home":
            self.api.send(user_id,HELP + (ADMIN_HELP if user_id==self.owner_id and self.owner_id>0 else ""),reply_markup=menu())
        elif data=="menu:profile":
            self.db.register(user_id)
            self.api.send(user_id,profile_text(self.db.profile(user_id)))
        elif data=="menu:castings":
            self.refresh_user(user_id)
            self.flush()
            rows=self.db.latest(user_id)
            self.api.send(user_id,"\n\n".join(
                "🎬 {}\n{}\n{}".format(title,body[:180],url or "") for title,body,url in rows)[:3700]
                if rows else empty_castings_text(self.db))
        elif data=="admin:sources":
            if user_id != self.owner_id:
                return
            rows=self.db.public_status()
            if not rows:
                self.api.send(user_id,"Источники пока не добавлены.")
            else:
                self.api.send(user_id,
                    "📡 Проверка источников. Ошибок: {} из {}.\n\n".format(
                        sum(bool(x[2]) for x in rows),len(rows))+
                    "\n\n".join(
                        "{}: {}\nПроверка: {}".format(
                            x[0],source_error(x[2]) if x[2] else "Страница доступна",
                            readable_time(x[4])
                        ) for x in rows
                    )[:3500]
                )
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
            self.api.send(user_id,"Канал @{}: {}".format(username,suggestion_status(status)))
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
                              f"Код источника: {chat['id']}\nЧтобы разрешить обработку: /enable {chat['id']}")
            except TelegramError as exc:
                log.warning("Owner notification failed: %s",exc.code)

    def refresh_user(self, user_id: int, limit: int = 10) -> int:
        """Recheck recently archived posts after a profile change without web access."""
        self.db.register(user_id)
        profile_data = self.db.profile(user_id)
        if not profile_data or not profile_data["active"]:
            return 0
        profile = Profile(
            city=profile_data["city"], gender=profile_data["gender"],
            age=profile_data["age"], height_cm=profile_data["height_cm"],
            kids_ok=False, include_keywords=[],
            exclude_keywords=["набор закрыт","уже набрали","итоги кастинга"],
        )
        queued = 0
        for chat_id,message_id,title,body,url,posted_at in self.db.recent_posts():
            if queued >= limit:
                break
            if not self.db.following(user_id,chat_id):
                continue
            if match_post(body,profile).matched:
                self.db.queue(user_id,chat_id,message_id)
                queued += 1
        return queued

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
        eligible, reason = actionable(body)
        if not eligible:
            return
        # Archive each fresh actionable post even when no subscriber currently fits.
        self.db.record_post(chat_id,message_id,source[1],body,
                            link_for(chat,message_id),message.get("date"))
        for uid,city,gender,age,height in self.db.subscribers(chat_id):
            profile=Profile(city=city,gender=gender,age=age,height_cm=height,
                            kids_ok=False,include_keywords=[],exclude_keywords=[
                            "набор закрыт","уже набрали","итоги кастинга"])
            result=match_post(body,profile)
            if result.matched:
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
                    self.db.mark_delivery(uid,chat_id,message_id,"pending",str(exc.code))

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
        client_enabled=os.getenv("CLIENT_SCAN_ENABLED","false").casefold() in ("true","1","yes")
        client_task=None
        if client_enabled:
            # This is an explicit opt-in. Never infer account access from BOT_TOKEN.
            api_id=os.getenv("TELEGRAM_API_ID","").strip()
            api_hash=os.getenv("TELEGRAM_API_HASH","").strip()
            session=os.getenv("TELEGRAM_SESSION","").strip()
            if not (api_id.isdigit() and api_hash and session):
                log.error("CLIENT_SCAN_ENABLED=true but authorized client settings are missing")
            else:
                client_task=asyncio.create_task(run_authorized_reader(
                    db,receiver,api_id=int(api_id),api_hash=api_hash,session=session,
                    interval=int(os.getenv("CLIENT_SCAN_INTERVAL_SECONDS","1800")),
                    lookback_hours=int(os.getenv("PUBLIC_LOOKBACK_HOURS","48"))))
        guardian_enabled=os.getenv('GUARDIAN_ENABLED','false').casefold() in ('true','1','yes')
        guardian_task=asyncio.create_task(run_guardian(
            db,api,owner,interval=int(os.getenv('GUARDIAN_INTERVAL_SECONDS','3600')),
            alerts=os.getenv('GUARDIAN_ALERTS','false').casefold() in ('true','1','yes'))
        ) if guardian_enabled else None
        web_pilot=None
        web_task=None
        if os.getenv("WEB_QR_PILOT_ENABLED","false").lower() in ("1","true","yes") and owner>0:
            web_pilot=OwnerWebPilot(db,api,owner)
            web_task=asyncio.create_task(web_pilot.run())
            log.info("Owner-only Telegram Web QR pilot enabled; no automatic login")
        public_task=None
        if not client_enabled:
            public_task=asyncio.create_task(monitor_public(
                db,receiver,interval_seconds=int(os.getenv("PUBLIC_SCAN_INTERVAL_SECONDS","1800")),
                lookback_hours=int(os.getenv("PUBLIC_LOOKBACK_HOURS","48"))))
        while True:
            try:
                updates=await asyncio.to_thread(api.get_updates,db.cursor()+1)
                for update in updates:
                    try:
                        handled=False
                        msg=update.get("message") or {}
                        sender=(msg.get("from") or {}).get("id")
                        chat=msg.get("chat") or {}
                        cmd=(msg.get("text") or "").strip().split(" ",1)[0].split("@")[0].lower()
                        allowed={"/webconnect","/webqr","/webstatus","/websource","/websources","/webscan","/webcastings","/webdisconnect"}
                        if chat.get("type")=="private" and cmd in allowed:
                            handled=True
                            if sender!=owner or owner<=0:
                                api.send(sender,"Функция доступна только владельцу в пилоте.")
                            elif web_pilot is None:
                                api.send(sender,"Эксперимент Telegram Web пока выключен на сервере.")
                            elif cmd=="/webconnect":
                                api.send(sender,
                                   "Вход на облачном сервере предоставляет доступ к Telegram-аккаунту. "
                                   "QR и браузерная сессия чувствительны. Читаются только вручную выбранные "
                                   "одобренные публичные источники. Отключение: /webdisconnect. "
                                   "Подтверждение:",reply_markup={"inline_keyboard":[[
                                      {"text":"Подключить","callback_data":"web:consent"},
                                      {"text":"Отмена","callback_data":"web:cancel"}]]})
                            elif cmd=="/webqr":
                                api.send(sender,await web_pilot.qr() if web_pilot.store.consented()
                                         else "Сначала /webconnect")
                            elif cmd=="/webstatus":
                                api.send(sender,await web_pilot.status())
                            elif cmd=="/websource":
                                try:
                                    name=web_pilot.store.select_source((msg.get("text") or "").partition(" ")[2])
                                    api.send(sender,"Источник выбран: @"+name)
                                except ValueError as exc:
                                    api.send(sender,str(exc))
                            elif cmd=="/websources":
                                api.send(sender,"Источники: "+", ".join("@"+r[0] for r in web_pilot.store.sources()))
                            elif cmd=="/webscan":
                                api.send(sender,await web_pilot.scan())
                            elif cmd=="/webcastings":
                                rows=web_pilot.store.latest()
                                api.send(sender,"\n\n".join(
                                  f"@{n}: {body[:220]}\nhttps://t.me/{n}/{mid}"
                                  for n,mid,body,ts in rows) or "Доставленных объявлений пока нет")
                            elif cmd=="/webdisconnect":
                                api.send(sender,"Удалить локальную сессию и личные данные?",
                                   reply_markup={"inline_keyboard":[[
                                     {"text":"Удалить","callback_data":"web:delete"},
                                     {"text":"Отмена","callback_data":"web:cancel"}]]})
                        callback=update.get("callback_query") or {}
                        data=callback.get("data") or ""
                        caller=(callback.get("from") or {}).get("id")
                        if data.startswith("web:") or data.startswith("webfb:"):
                            handled=True
                            if caller==owner and web_pilot is not None:
                                if data=="web:consent":
                                    web_pilot.store.consent()
                                    api.send(caller,"Подтверждено. Нажмите /webqr.")
                                elif data=="web:delete":
                                    api.send(caller,await web_pilot.disconnect())
                                elif data=="web:cancel":
                                    api.send(caller,"Отменено.")
                                elif data.startswith("webfb:"):
                                    parts=data.split(":")
                                    if len(parts)==4:
                                        try:
                                            mid=int(parts[3])
                                            ok=web_pilot.store.add_feedback(parts[2],mid,
                                              "relevant" if parts[1]=="yes" else "irrelevant")
                                            api.send(caller,"Оценка сохранена." if ok else "Публикация не найдена.")
                                        except ValueError:
                                            pass
                        if not handled:
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
        tasks=[task for task in (locals().get("public_task"),
                                  locals().get("client_task"),
                                  locals().get("guardian_task"),
                                  locals().get("web_task")) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks,return_exceptions=True)
        if locals().get('web_pilot') is not None and web_pilot.started:
            await web_pilot.reader._context.close()
        db.close()

def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())

if __name__=="__main__":main()

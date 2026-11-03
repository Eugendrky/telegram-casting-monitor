"""Bot API casting receiver. No user login, dialog scraping, or historical reads."""
from __future__ import annotations
import asyncio
import logging
import os
import re
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
from app.web_user_manager import WebUserManager
from app.web_owner_store import PILOT_SOURCES
from app.quality_guardian import inspect as inspect_quality, render as render_quality, run_guardian

log = logging.getLogger("casting.bot")
HELP = (
    "🎬 Ваши кастинги\n\n"
    "Нажмите «✨ Актуальные кастинги», чтобы посмотреть подходящие объявления. "
    "Отклонённые вами объявления повторно не показываются.\n\n"
    "Для сбора подключите аккаунт один раз и отправьте боту ссылки на публичные группы "
    "или их @имена. "
    "Бот проверяет их автоматически и присылает совпадения с вашим профилем.\n\n"
    "📋 Меню внизу чата: кастинги, группы, проверка и статус.\n"
    "Профиль: /profile — город, пол, возраст и рост."
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
        [{"text":"✨ Актуальные кастинги","style":"primary","callback_data":"menu:castings"}],
        [{"text":"➕ Добавить группы","callback_data":"web:add"},
         {"text":"📋 Мои группы","callback_data":"web:sources"}],
        [{"text":"🔎 Проверить группы","callback_data":"web:scan"},
         {"text":"📊 Статус сбора","callback_data":"web:status"}],
        [{"text":"🔐 Подключить аккаунт","callback_data":"web:connect"}],
        [{"text":"📨 Предложить канал","callback_data":"suggest:start"},
         {"text":"👤 Профиль","callback_data":"menu:profile"}]
    ]}

def looks_like_group_list(text: str) -> bool:
    """Recognize an explicit list of Telegram source links/usernames in any chat state."""
    cleaned = re.sub(r"(?m)^\\s*[•*–-]+\\s*", "", text or "").strip()
    tokens = [part for part in re.split(r"[\\s,;]+", cleaned) if part]
    if not tokens:
        return False
    return all(
        token.startswith("@") or re.search(r"(?:^|//)(?:www\\.)?(?:t\\.me|telegram\\.me)/", token, re.I)
        for token in tokens
    )

def castings_view_messages(pilot, limit: int = 5) -> list[tuple[str, dict]]:
    """Return a concise current view of delivered matches with their feedback actions."""
    sources = pilot.store.sources()
    if not sources:
        return [("Список групп пуст. Добавьте публичные группы через меню — бот начнёт проверку автоматически.",
                 {"inline_keyboard":[[{"text":"➕ Добавить группы","callback_data":"web:add"}] ]})]
    rows = pilot.store.latest(limit=limit)
    if not rows:
        failed = [row for row in sources if row[5]]
        if failed:
            msg = "Пока нет доставленных совпадений. Не все группы удалось прочитать — откройте «Мои группы» для причин и статуса."
            buttons = [[{"text":"📋 Показать группы","callback_data":"web:sources"},
                        {"text":"📊 Статус сбора","callback_data":"web:status"}]]
        else:
            msg = "Подходящих объявлений пока нет. Бот продолжает проверять выбранные группы автоматически."
            buttons = [[{"text":"🔎 Проверить группы","callback_data":"web:scan"},
                        {"text":"📋 Мои группы","callback_data":"web:sources"}]]
        return [(msg, {"inline_keyboard":buttons})]
    result=[]
    for name,mid,body,ts in rows:
        text=f"🎬 @{name}\n\n{body[:3000]}\n\nhttps://t.me/{name}/{mid}"
        markup={"inline_keyboard":[[
            {"text":"👍 Подходит","callback_data":f"webfb:yes:{name}:{mid}"},
            {"text":"👎 Не подходит","callback_data":f"webfb:no:{name}:{mid}"}]]}
        result.append((text,markup))
    return result

def web_sources_report(pilot, scan_result: str | None = None) -> tuple[str, dict]:
    rows=pilot.store.sources()
    if not rows:
        body="Ваш список групп пока пуст. Нажмите «➕ Добавить группы» и отправьте username списком."
        return body,{"inline_keyboard":[[
            {"text":"➕ Добавить группы","callback_data":"web:add"},
            {"text":"↩️ В меню","callback_data":"web:menu"}]]}
    lines=[]
    for name,last_id,next_scan,checked,successful,error,visible,new,truncated in rows:
        if error:
            state=f"⚠️ Последняя попытка: {readable_time(checked)}; {error}"
            if successful:
                state += f"\nПоследняя успешная: {readable_time(successful)}"
        elif successful:
            state=(f"✅ Успешно: {readable_time(successful)}; видно постов: {visible}, "
                   f"новых: {new}")
            if truncated:
                state += "; Telegram показал не всю историю"
        elif checked:
            state=f"⚠️ Последняя попытка: {readable_time(checked)}, успеха пока нет"
        else:
            state="🕒 Ожидает первой проверки"
        lines.append(f"@{name}\n{state}")
    heading="📋 Ваши группы для сбора:\n\n"
    if scan_result:
        heading="🔎 Результат проверки: "+scan_result+"\n\n"
    keyboard=[[{"text":f"Убрать @{row[0]}","callback_data":f"web:remove:{row[0]}"}]
              for row in rows[:20]]
    keyboard.extend([
        [{"text":"➕ Добавить группы","callback_data":"web:add"},
         {"text":"🔎 Проверить группы","callback_data":"web:scan"}],
        [{"text":"↩️ В меню","callback_data":"web:menu"}]
    ])
    return heading+"\n\n".join(lines),{"inline_keyboard":keyboard}

def menu_reply_keyboard():
    return {"keyboard":[[{"text":"📋 Меню"}]],
            "resize_keyboard":True,"is_persistent":True,
            "input_field_placeholder":"Нажмите «Меню» для команд"}

class Receiver:
    def __init__(self, api: BotAPI, db: Database, owner_id: int):
        self.api,self.db,self.owner_id=api,db,owner_id
        self.web_pilot=None

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
        if text.casefold() in ("📋 меню", "меню"):
            self.api.send(user_id,HELP + (ADMIN_HELP if user_id==self.owner_id and self.owner_id>0 else ""),reply_markup=menu())
            return
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
            self.api.send(user_id,"Быстрый доступ к меню:",reply_markup=menu_reply_keyboard())
            self.api.send(user_id,intro,reply_markup=menu())
        elif cmd=="/help":
            self.api.send(user_id,"Кнопка меню закреплена внизу чата.",reply_markup=menu_reply_keyboard())
            self.api.send(user_id,HELP + (ADMIN_HELP if user_id==self.owner_id and self.owner_id>0 else ""),reply_markup=menu())
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
                message="Этот канал уже включён в каталог. Каталог означает, что источник разрешён, " \
                        "но сам по себе не подтверждает доступ аккаунта сборщика к публикациям."
                if user_id==self.owner_id and self.web_pilot is not None and channel in PILOT_SOURCES:
                    state=next((row for row in self.web_pilot.store.sources()
                                if row[0]==channel),None)
                    if state is None:
                        message += (f"\n@{channel} ещё не выбран в Telegram Web. "
                                    f"После подтверждения доступа выберите его: /websource @{channel}, "
                                    "затем запустите /webscan.")
                    elif state[5]:
                        message += (f"\nПоследняя проверка не получила доступ к @{channel}: {state[5]}. "
                                    "Если заявка на вступление ещё рассматривается, дождитесь одобрения; "
                                    f"затем повторите /websource @{channel} и запустите /webscan.")
                    elif state[4]:
                        message += f"\nДоступ к @{channel} уже проверен; последняя проверка прошла успешно."
                    else:
                        message += (f"\n@{channel} выбран, но успешная проверка доступа ещё не завершилась. "
                                    "Проверьте /webstatus.")
                self.api.send(user_id,message)
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
        web_users=None
        web_portal=None
        web_restore_task=None
        web_group_prompt=set()
        if os.getenv("WEB_QR_PILOT_ENABLED","false").lower() in ("1","true","yes") and owner>0:
            web_users=WebUserManager(db,api,owner)
            auth_base=os.getenv('WEB_AUTH_BASE_URL','').strip()
            if auth_base:
                from app.web_auth_portal import AuthPortal
                web_portal=AuthPortal(web_users,auth_base,
                    asyncio.get_running_loop(),port=int(os.getenv('PORT','8080')))
                web_portal.start()
                web_users.auth_portal=web_portal
            receiver.web_pilot=web_users.get(owner)
            web_restore_task=asyncio.create_task(web_users.restore())
            log.info("Consent-gated per-user Telegram Web collection enabled")
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
                        sender_username=(msg.get("from") or {}).get("username")
                        chat=msg.get("chat") or {}
                        message_text=(msg.get("text") or "").strip()
                        cmd=message_text.split(" ",1)[0].split("@")[0].lower()
                        source_message = (chat.get("type")=="private" and isinstance(sender,int)
                                          and not message_text.startswith("/")
                                          and looks_like_group_list(message_text))
                        if source_message and db.awaiting_suggestion(sender):
                            # An explicit suggestion flow takes priority over auto-adding a source.
                            db.set_profile_prompt(sender,None)
                            web_group_prompt.discard(sender)
                        if source_message and not db.awaiting_suggestion(sender):
                            # Handle pasted groups before a stale profile prompt can consume them.
                            handled=True
                            web_group_prompt.discard(sender)
                            db.set_profile_prompt(sender,None)
                            if web_users is None:
                                api.send(sender,"Добавление групп сейчас недоступно. Попробуйте позже.")
                            elif not web_users.is_allowed(sender,sender_username):
                                api.send(sender,
                                    "Сборщик доступен только пользователям с разрешённым доступом."
                                )
                            else:
                                pilot=web_users.get(sender,create=False)
                                if pilot is None or not pilot.store.consented():
                                    api.send(sender,
                                        "Распознал список групп. Чтобы начать сбор, подключите аккаунт один раз. "
                                        "После подключения отправьте этот список ещё раз.",
                                        reply_markup={"inline_keyboard":[[
                                            {"text":"🔐 Подключить аккаунт","callback_data":"web:connect"}]]})
                                else:
                                    try:
                                        names=pilot.store.select_sources(message_text)
                                        await web_users.activate(sender)
                                        report,markup=web_sources_report(pilot)
                                        api.send(sender,
                                            "✅ Добавлено групп: {}\\n{}\\n\\nПроверка начнётся автоматически.\\n\\n{}".format(
                                                len(names),", ".join("@"+name for name in names),report),
                                            reply_markup=markup)
                                    except ValueError as exc:
                                        api.send(sender,str(exc))
                        if (not handled and chat.get("type")=="private" and isinstance(sender,int)
                                and sender in web_group_prompt):
                            handled=True
                            web_group_prompt.discard(sender)
                            if cmd=="/cancel":
                                api.send(sender,"Добавление групп отменено.",reply_markup=menu())
                            elif web_users is None or not web_users.is_allowed(sender,sender_username):
                                api.send(sender,"Подключение недоступно. Обратитесь к администратору бота.")
                            else:
                                pilot=web_users.get(sender,create=False)
                                try:
                                    if pilot is None or not pilot.store.consented():
                                        raise ValueError("Сначала подключите аккаунт через кнопку «🔐 Подключить аккаунт».")
                                    names=pilot.store.select_sources(message_text)
                                    await web_users.activate(sender)
                                    text=("✅ Добавлено групп: {}\n{}\n\n".format(
                                        len(names),", ".join("@"+name for name in names))+
                                        "Проверка начнётся автоматически. Можно запустить её кнопкой ниже.")
                                    api.send(sender,text,reply_markup=web_sources_report(pilot)[1])
                                except ValueError as exc:
                                    if pilot is not None and pilot.store.consented():
                                        web_group_prompt.add(sender)
                                    api.send(sender,str(exc)+"\nПопробуйте ещё раз или отправьте /cancel.",
                                        reply_markup={"inline_keyboard":[[
                                            {"text":"Отмена","callback_data":"web:cancel"}]]})
                        admin_web_commands={"/weballow","/webdeny","/weballowed"}
                        if not handled and chat.get("type")=="private" and cmd in admin_web_commands:
                            handled=True
                            if sender!=owner or owner<=0:
                                api.send(sender,"Управлять списком доступа может только администратор.")
                            elif web_users is None:
                                api.send(sender,"Подключение Telegram Web пока выключено на сервере.")
                            elif cmd=="/weballow":
                                try:
                                    username=web_users.allow((msg.get("text") or "").partition(" ")[2])
                                    api.send(sender,f"Доступ добавлен для @{username}. Попросите пользователя отправить /webconnect в личном чате с ботом.")
                                except ValueError as exc:
                                    api.send(sender,str(exc))
                            elif cmd=="/webdeny":
                                try:
                                    username,user_id,removed=web_users.deny((msg.get("text") or "").partition(" ")[2])
                                    if removed and user_id is not None:
                                        await web_users.disconnect(user_id)
                                    api.send(sender,
                                        f"Доступ @{username} отозван; сохранённая сессия удалена."
                                        if removed and user_id is not None else
                                        f"Доступ @{username} удалён из списка."
                                        if removed else f"@{username} не было в списке доступа.")
                                except ValueError as exc:
                                    api.send(sender,str(exc))
                            else:
                                rows=web_users.allowed_users()
                                api.send(sender,"Разрешённые пользователи:\n"+"\n".join(
                                    f"@{name}"+(f" · ID {uid}" if uid else " · ещё не подключался")
                                    for name,uid,added in rows) if rows else "Список доступа пуст.")
                        allowed={"/webconnect","/webqr","/webauth","/webstatus","/websource","/webremove","/websources","/webscan","/webcastings","/webdisconnect"}
                        if not handled and chat.get("type")=="private" and cmd in allowed:
                            handled=True
                            if web_users is None:
                                api.send(sender,"Подключение Telegram Web пока выключено на сервере.")
                            elif not web_users.is_allowed(sender,sender_username):
                                api.send(sender,"Подключение доступно только пользователям из списка разрешённых. Обратитесь к администратору бота.")
                            elif cmd=="/webconnect":
                                db.register(sender)
                                pilot=web_users.get(sender)
                                if pilot.store.consented():
                                    try:
                                        await web_users.activate(sender)
                                        api.send(sender,"Ваше подключение уже подтверждено. Продолжить: /webstatus или /webqr.")
                                    except ValueError as exc:
                                        api.send(sender,str(exc))
                                else:
                                    api.send(sender,
                                       "Подключение даст серверу доступ к вашему Telegram Web. "
                                       "Сессия, объявления из выбранных источников и результаты проверок "
                                       "хранятся на сервере до /webdisconnect. Сборщик обращается только "
                                       "к разрешённым и выбранным источникам; тексты личных чатов не запрашивает. "
                                       "Продолжая, вы разрешаете эту обработку. Отозвать доступ можно здесь "
                                       "и в Telegram → Настройки → Устройства. Подтвердить подключение?",
                                       reply_markup={"inline_keyboard":[[
                                          {"text":"Подключить свой аккаунт","callback_data":"web:consent"},
                                          {"text":"Отмена","callback_data":"web:cancel"}]]})
                            else:
                                pilot=web_users.get(sender,create=False)
                                if pilot is None or not pilot.store.consented():
                                    api.send(sender,"Сначала подключите свой аккаунт: /webconnect.")
                                else:
                                    try:
                                        await web_users.activate(sender)
                                        if cmd=="/webqr":
                                            api.send(sender,await pilot.qr())
                                        elif cmd=="/webstatus":
                                            report,markup=web_sources_report(pilot)
                                            api.send(sender,(await pilot.status())+"\n\n"+report,
                                                reply_markup=markup)
                                        elif cmd=="/webauth":
                                            api.send(sender,await pilot.auth_link())
                                        elif cmd=="/websource":
                                            name=pilot.store.select_source((msg.get("text") or "").partition(" ")[2])
                                            report,markup=web_sources_report(pilot)
                                            api.send(sender,"✅ Группа добавлена: @"+name+
                                                ". Автопроверка начнётся в ближайший цикл.\n\n"+report,
                                                reply_markup=markup)
                                        elif cmd=="/webremove":
                                            raw=(msg.get("text") or "").partition(" ")[2]
                                            removed=pilot.store.remove_source(raw)
                                            name=normalize_channel(raw)
                                            report,markup=web_sources_report(pilot)
                                            api.send(sender,("✅ Группа удалена: @"+name+"\n\n" if removed else
                                                ("Группы @"+name+" не было в списке.\n\n"))+report,
                                                reply_markup=markup)
                                        elif cmd=="/websources":
                                            report,markup=web_sources_report(pilot)
                                            api.send(sender,report,reply_markup=markup)
                                        elif cmd=="/webscan":
                                            result=await pilot.scan()
                                            report,markup=web_sources_report(pilot,result)
                                            api.send(sender,report,reply_markup=markup)
                                        elif cmd=="/webcastings":
                                            rows=pilot.store.latest()
                                            api.send(sender,"\n\n".join(
                                              f"@{n}: {body[:220]}\nhttps://t.me/{n}/{mid}"
                                              for n,mid,body,ts in rows) or "Доставленных объявлений пока нет")
                                        elif cmd=="/webdisconnect":
                                            api.send(sender,"Удалить сессию Telegram Web и данные сборщика?",
                                               reply_markup={"inline_keyboard":[[
                                                 {"text":"Удалить","callback_data":"web:delete"},
                                                 {"text":"Отмена","callback_data":"web:cancel"}]]})
                                    except ValueError as exc:
                                        api.send(sender,str(exc))
                        callback=update.get("callback_query") or {}
                        data=callback.get("data") or ""
                        caller=(callback.get("from") or {}).get("id")
                        caller_username=(callback.get("from") or {}).get("username")
                        if data.startswith("web:") or data.startswith("webfb:") or data=="menu:castings":
                            handled=True
                            callback_chat=(callback.get('message') or {}).get('chat') or {}
                            if (web_users is not None and isinstance(caller,int)
                                    and callback_chat.get('type')=='private'
                                    and callback_chat.get('id')==caller
                                    and web_users.is_allowed(caller,caller_username)):
                                if data=="menu:castings":
                                    pilot=web_users.get(caller,create=False)
                                    if pilot is None or not pilot.store.consented():
                                        api.send(caller,
                                            "Сначала подключите аккаунт и выберите группы. Это нужно, чтобы показать ваши актуальные кастинги.",
                                            reply_markup={"inline_keyboard":[[
                                                {"text":"🔐 Подключить аккаунт","callback_data":"web:connect"}]]})
                                    else:
                                        try:
                                            await web_users.activate(caller)
                                            if not await pilot.verified():
                                                api.send(caller,
                                                    "Telegram ещё не подтвердил подключение. Завершите вход, затем нажмите «✨ Актуальные кастинги» снова.",
                                                    reply_markup={"inline_keyboard":[[
                                                        {"text":"🔐 Статус подключения","callback_data":"web:status"}]]})
                                            else:
                                                for text,markup in castings_view_messages(pilot):
                                                    api.send(caller,text,reply_markup=markup)
                                        except ValueError as exc:
                                            api.send(caller,str(exc))
                                elif data=="web:menu":
                                    web_group_prompt.discard(caller)
                                    api.send(caller,HELP,reply_markup=menu())
                                elif data=="web:connect":
                                    pilot=web_users.get(caller)
                                    if pilot.store.consented():
                                        try:
                                            await web_users.activate(caller)
                                            api.send(caller,await pilot.status(),reply_markup=menu())
                                        except ValueError as exc:
                                            api.send(caller,str(exc))
                                    else:
                                        api.send(caller,
                                            "Подключение позволит сборщику читать только выбранные вами группы "
                                            "из Telegram Web. Сессия и результаты хранятся на сервере до отключения. "
                                            "Продолжить?",
                                            reply_markup={"inline_keyboard":[[
                                                {"text":"Подключить мой аккаунт","callback_data":"web:consent"},
                                                {"text":"Отмена","callback_data":"web:cancel"}]]})
                                elif data=="web:add":
                                    pilot=web_users.get(caller,create=False)
                                    if pilot is None or not pilot.store.consented():
                                        api.send(caller,"Сначала подключите аккаунт кнопкой ниже.",
                                            reply_markup={"inline_keyboard":[[
                                                {"text":"🔐 Подключить аккаунт","callback_data":"web:connect"},
                                                {"text":"↩️ В меню","callback_data":"web:menu"}]]})
                                    else:
                                        try:
                                            await web_users.activate(caller)
                                            web_group_prompt.add(caller)
                                            api.send(caller,
                                                "Пришлите публичные группы одним сообщением: по одному @username "
                                                "на строке, через пробел, запятую или точку с запятой.\n\n"
                                                "Можно отправить ссылки t.me. Закрытые приглашения не поддерживаются.",
                                                reply_markup={"inline_keyboard":[[
                                                    {"text":"Отмена","callback_data":"web:cancel"}]]})
                                        except ValueError as exc:
                                            api.send(caller,str(exc))
                                elif data=="web:sources":
                                    pilot=web_users.get(caller,create=False)
                                    if pilot is None or not pilot.store.consented():
                                        api.send(caller,"Подключите Telegram-аккаунт, чтобы увидеть свой список групп.",
                                            reply_markup={"inline_keyboard":[[
                                                {"text":"🔐 Подключить аккаунт","callback_data":"web:connect"}]]})
                                    else:
                                        try:
                                            await web_users.activate(caller)
                                            report,markup=web_sources_report(pilot)
                                            api.send(caller,report,reply_markup=markup)
                                        except ValueError as exc:
                                            api.send(caller,str(exc))
                                elif data=="web:status":
                                    pilot=web_users.get(caller,create=False)
                                    if pilot is None or not pilot.store.consented():
                                        api.send(caller,"Сборщик не подключён. Нажмите «🔐 Подключить аккаунт».",
                                            reply_markup=menu())
                                    else:
                                        try:
                                            await web_users.activate(caller)
                                            report,markup=web_sources_report(pilot)
                                            api.send(caller,(await pilot.status())+"\n\n"+report,
                                                reply_markup=markup)
                                        except ValueError as exc:
                                            api.send(caller,str(exc))
                                elif data=="web:scan":
                                    pilot=web_users.get(caller,create=False)
                                    if pilot is None or not pilot.store.consented():
                                        api.send(caller,"Сначала подключите Telegram-аккаунт.",reply_markup=menu())
                                    else:
                                        try:
                                            await web_users.activate(caller)
                                            result=await pilot.scan()
                                            report,markup=web_sources_report(pilot,result)
                                            api.send(caller,report,reply_markup=markup)
                                        except ValueError as exc:
                                            api.send(caller,str(exc))
                                elif data.startswith("web:remove:"):
                                    pilot=web_users.get(caller,create=False)
                                    try:
                                        name=normalize_channel(data.partition("web:remove:")[2])
                                        removed=(pilot.store.remove_source(name)
                                                 if pilot is not None and pilot.store.consented() else False)
                                        report,markup=web_sources_report(pilot) if pilot else (
                                            "Сборщик не подключён.",menu())
                                        api.send(caller,("✅ Группа удалена: @"+name+"\n\n" if removed else
                                            ("Группы @"+name+" нет в списке.\n\n"))+report,
                                            reply_markup=markup)
                                    except ValueError as exc:
                                        api.send(caller,str(exc))
                                elif data=="web:consent":
                                    try:
                                        db.register(caller)
                                        await web_users.consent(caller)
                                        api.send(caller,
                                            "Подключение подтверждено. Завершите вход через /webqr, "
                                            "затем кнопки меню станут доступны.",reply_markup=menu())
                                    except ValueError as exc:
                                        api.send(caller,str(exc))
                                elif data=="web:delete":
                                    api.send(caller,await web_users.disconnect(caller))
                                elif data=="web:cancel":
                                    web_group_prompt.discard(caller)
                                    api.send(caller,"Отменено.")
                                elif data.startswith("webfb:"):
                                    parts=data.split(":")
                                    if len(parts)==4:
                                        try:
                                            mid=int(parts[3])
                                            pilot=web_users.get(caller,create=False)
                                            ok=(pilot.store.add_feedback(parts[2],mid,
                                              "relevant" if parts[1]=="yes" else "irrelevant")
                                              if pilot is not None else False)
                                            api.send(caller,"Оценка сохранена." if ok else "Публикация не найдена.")
                                        except ValueError:
                                            pass
                            elif isinstance(caller,int) and callback_chat.get('type')=='private':
                                api.send(caller,"Сборщик групп доступен пользователям, которых добавил администратор.")
                            if callback.get('id'):
                                await asyncio.to_thread(api.call,'answerCallbackQuery',callback_query_id=callback['id'])
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
                                  locals().get("web_restore_task")) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks,return_exceptions=True)
        if locals().get('web_users') is not None:
            await web_users.close()
        if locals().get('web_portal') is not None:
            await asyncio.to_thread(web_portal.close)
        db.close()

def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())

if __name__=="__main__":main()

import html
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.constants import ChatType
from telegram.constants import ParseMode
from telegram.error import TimedOut
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logger = logging.getLogger("kiev_avto_bot")

TOKEN = os.getenv("BOT_TOKEN")
TARGET_CHAT_RAW = os.getenv("TARGET_CHAT", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
BLOCKLIST_FILE = Path("blocked_users.json")
REQUEST_CONNECT_TIMEOUT = float(os.getenv("REQUEST_CONNECT_TIMEOUT", "10"))
REQUEST_READ_TIMEOUT = float(os.getenv("REQUEST_READ_TIMEOUT", "25"))
REQUEST_WRITE_TIMEOUT = float(os.getenv("REQUEST_WRITE_TIMEOUT", "25"))
REQUEST_POOL_TIMEOUT = float(os.getenv("REQUEST_POOL_TIMEOUT", "10"))


class Step:
    NUMBER = "number"
    TYPE = "type"
    DESCRIPTION = "description"
    CONFIRM = "confirm"


@dataclass(frozen=True)
class UiText:
    cancel: str = "❌ Скасувати"
    restart: str = "🔁 Почати заново"
    send: str = "✅ Відправити"
    edit_number: str = "✏️ Змінити номер"
    edit_type: str = "✏️ Змінити тип"
    edit_desc: str = "✏️ Змінити опис"


TXT = UiText()

PROBLEM_TYPES = [
    ["Світло / електрика"],
    ["Рідини / оливи"],
    ["Колеса / ходова"],
    ["Салон / кузов"],
    ["Інше"],
]

CANCEL_KB = ReplyKeyboardMarkup(
    [[TXT.cancel], [TXT.restart]],
    resize_keyboard=True,
)

CONFIRM_KB = ReplyKeyboardMarkup(
    [
        [TXT.send],
        [TXT.edit_number, TXT.edit_type],
        [TXT.edit_desc],
        [TXT.cancel],
    ],
    resize_keyboard=True,
)

PLATE_RE = re.compile(r"[A-ZА-ЯІЇЄ0-9]{5,10}")


def parse_admin_ids(raw: str) -> set[int]:
    if not raw:
        return set()
    result: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            result.add(int(chunk))
        except ValueError:
            logger.warning("Invalid admin id in ADMIN_IDS: %s", chunk)
    return result


ADMIN_IDS = parse_admin_ids(ADMIN_IDS_RAW)


def load_blocked_users() -> set[int]:
    if not BLOCKLIST_FILE.exists():
        return set()
    try:
        payload = json.loads(BLOCKLIST_FILE.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to load blocked users file")
        return set()

    if not isinstance(payload, list):
        return set()

    result: set[int] = set()
    for item in payload:
        try:
            result.add(int(item))
        except Exception:
            continue
    return result


def save_blocked_users(blocked: set[int]) -> None:
    data = sorted(blocked)
    BLOCKLIST_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def blocked_users(context: ContextTypes.DEFAULT_TYPE) -> set[int]:
    users = context.application.bot_data.get("blocked_users")
    if isinstance(users, set):
        return users
    users = load_blocked_users()
    context.application.bot_data["blocked_users"] = users
    return users


def is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id in ADMIN_IDS)


def is_blocked(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return False
    return user.id in blocked_users(context)


def normalize_plate(value: str) -> str:
    return (value or "").strip().upper().replace(" ", "").replace("-", "")


def looks_like_plate(value: str) -> bool:
    return bool(PLATE_RE.fullmatch(normalize_plate(value)))


def now_local_str() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")


def sender_label(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "невідомий"
    if user.username:
        return f"@{user.username}"
    full_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    return f"{full_name} (id:{user.id})" if full_name else f"id:{user.id}"


def get_target_chat() -> int | None:
    if not TARGET_CHAT_RAW:
        return None
    try:
        return int(TARGET_CHAT_RAW)
    except ValueError:
        logger.error("TARGET_CHAT must be integer, got: %s", TARGET_CHAT_RAW)
        return None


def reset_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    context.user_data["step"] = Step.NUMBER


def build_preview_html(context: ContextTypes.DEFAULT_TYPE) -> str:
    plate = html.escape(context.user_data.get("number", "-"))
    issue_type = html.escape(context.user_data.get("type", "-"))
    description = html.escape(context.user_data.get("description", "-"))

    return (
        "🧾 <b>Перевірте заявку перед відправкою:</b>\n"
        f"🚗 <b>Авто:</b> <code>{plate}</code>\n"
        f"📌 <b>Тип:</b> {issue_type}\n"
        f"📝 <b>Опис:</b> {description}\n\n"
        f"Якщо все ок - натисніть <b>{html.escape(TXT.send)}</b>."
    )


def build_dispatch_html(context: ContextTypes.DEFAULT_TYPE, update: Update) -> str:
    plate = html.escape(context.user_data.get("number", "-"))
    issue_type = html.escape(context.user_data.get("type", "-"))
    description = html.escape(context.user_data.get("description", "-"))
    sender = html.escape(sender_label(update))
    created_at = html.escape(now_local_str())

    return (
        "🛠 <b>Нова заявка</b>\n"
        f"🕒 <b>Час:</b> {created_at}\n"
        f"👤 <b>Від:</b> {sender}\n"
        f"🚗 <b>Авто:</b> <code>{plate}</code>\n"
        f"📌 <b>Тип:</b> {issue_type}\n"
        f"📝 <b>Опис:</b> {description}"
    )


async def safe_reply(
    update: Update,
    text: str,
    *,
    reply_markup=None,
    parse_mode: str | None = None,
) -> None:
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)


async def reject_if_blocked(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if is_admin(update):
        return False
    if not is_blocked(update, context):
        return False
    await safe_reply(update, "Доступ до бота обмежено.")
    return True


async def ask_for_number(update: Update) -> None:
    await safe_reply(
        update,
        "Введіть державний номер авто.\n"
        "Приклад: <code>110987</code>.",
        reply_markup=CANCEL_KB,
        parse_mode=ParseMode.HTML,
    )


async def ask_for_type(update: Update) -> None:
    await safe_reply(
        update,
        "Оберіть тип проблеми:",
        reply_markup=ReplyKeyboardMarkup(PROBLEM_TYPES, one_time_keyboard=True, resize_keyboard=True),
    )


async def ask_for_description(update: Update) -> None:
    await safe_reply(
        update,
        "📝 Опишіть проблему.\n\n"
        "Приклади:\n\n"
        "💡 Світло / електрика\n"
        "• перегоріла ліва лампа\n"
        "• не працює стоп-сигнал\n"
        "• перегорів запобіжник\n\n"
        "🛢 Рідини / оливи\n"
        "• долити антифриз\n"
        "• долити омивач\n"
        "• низький рівень оливи\n\n"
        "🛞 Колеса / ходова\n"
        "• спустило колесо\n"
        "• потрібна підкачка колеса\n"
        "• стукає стійка\n\n"
        "🚗 Салон / кузов\n"
        "• брудний салон\n"
        "• подряпина на дверях\n"
        "• пошкоджений бампер",
        reply_markup=ReplyKeyboardRemove(),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_blocked(update, context):
        return
    reset_flow(context)
    await safe_reply(
        update,
        "Вітаю! Я допоможу створити заявку для команди сервісу.",
        reply_markup=CANCEL_KB,
    )
    await ask_for_number(update)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_blocked(update, context):
        return

    extra = ""
    if is_admin(update):
        extra = (
            "\n\nАдмін-команди:\n"
            "/ban <user_id> - заблокувати користувача\n"
            "/unban <user_id> - розблокувати користувача\n"
            "/banlist - список заблокованих"
        )

    await safe_reply(
        update,
        "Команди:\n"
        "/start - почати створення заявки\n"
        "/cancel - скасувати поточну заявку\n"
        "/chatid - показати chat id\n"
        "/help - показати підказку"
        f"{extra}",
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_blocked(update, context):
        return
    context.user_data.clear()
    await safe_reply(
        update,
        "Скасовано. Напишіть /start, щоб почати знову.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_blocked(update, context):
        return
    reset_flow(context)
    await safe_reply(update, "Починаємо заново.", reply_markup=CANCEL_KB)
    await ask_for_number(update)


async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_blocked(update, context):
        return
    chat = update.effective_chat
    if chat:
        await safe_reply(update, f"Chat ID: <code>{chat.id}</code>", parse_mode=ParseMode.HTML)


def parse_user_id_arg(args: list[str]) -> int | None:
    if not args:
        return None
    try:
        return int(args[0].strip())
    except ValueError:
        return None


async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await safe_reply(update, "Команда доступна лише адміну.")
        return

    user_id = parse_user_id_arg(context.args)
    if user_id is None:
        await safe_reply(update, "Використання: /ban <user_id>")
        return

    blocked = blocked_users(context)
    blocked.add(user_id)
    save_blocked_users(blocked)
    await safe_reply(update, f"Користувача {user_id} заблоковано.")


async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await safe_reply(update, "Команда доступна лише адміну.")
        return

    user_id = parse_user_id_arg(context.args)
    if user_id is None:
        await safe_reply(update, "Використання: /unban <user_id>")
        return

    blocked = blocked_users(context)
    if user_id in blocked:
        blocked.remove(user_id)
        save_blocked_users(blocked)
        await safe_reply(update, f"Користувача {user_id} розблоковано.")
        return

    await safe_reply(update, f"Користувач {user_id} не був у блок-листі.")


async def banlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await safe_reply(update, "Команда доступна лише адміну.")
        return

    blocked = sorted(blocked_users(context))
    if not blocked:
        await safe_reply(update, "Блок-лист порожній.")
        return

    lines = "\n".join(str(uid) for uid in blocked[:200])
    suffix = "\n..." if len(blocked) > 200 else ""
    await safe_reply(update, f"Заблоковані ID ({len(blocked)}):\n{lines}{suffix}")


async def send_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target_chat = get_target_chat()
    message = build_dispatch_html(context, update)

    if target_chat is None:
        logger.warning("TARGET_CHAT is empty or invalid. Printing request to stdout.")
        print(message)
    else:
        try:
            await context.bot.send_message(
                chat_id=target_chat,
                text=message,
                parse_mode=ParseMode.HTML,
            )
        except TimedOut:
            logger.warning("Timeout during send_message, retrying once.")
            await context.bot.send_message(
                chat_id=target_chat,
                text=message,
                parse_mode=ParseMode.HTML,
            )


async def handle_number_step(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    plate = normalize_plate(text)
    context.user_data["number"] = plate
    context.user_data["step"] = Step.TYPE

    if not looks_like_plate(plate):
        await safe_reply(
            update,
            "⚠️ Номер виглядає незвично. Якщо все ок - продовжуйте.",
            reply_markup=CANCEL_KB,
        )
    await ask_for_type(update)


async def handle_type_step(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    allowed = {row[0] for row in PROBLEM_TYPES}
    if text not in allowed:
        await safe_reply(
            update,
            "Оберіть тип кнопкою нижче 👇",
            reply_markup=ReplyKeyboardMarkup(PROBLEM_TYPES, one_time_keyboard=True, resize_keyboard=True),
        )
        return

    context.user_data["type"] = text
    context.user_data["step"] = Step.DESCRIPTION
    await ask_for_description(update)


async def handle_description_step(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    cleaned = text.strip()
    if len(cleaned) < 3:
        await safe_reply(update, "Опис надто короткий. Додайте, будь ласка, більше деталей.")
        return
    context.user_data["description"] = cleaned
    context.user_data["step"] = Step.CONFIRM
    await safe_reply(
        update,
        build_preview_html(context),
        parse_mode=ParseMode.HTML,
        reply_markup=CONFIRM_KB,
    )


async def handle_confirm_step(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if text == TXT.edit_number:
        context.user_data["step"] = Step.NUMBER
        await safe_reply(update, "Введіть номер авто ще раз:", reply_markup=CANCEL_KB)
        return

    if text == TXT.edit_type:
        context.user_data["step"] = Step.TYPE
        await ask_for_type(update)
        return

    if text == TXT.edit_desc:
        context.user_data["step"] = Step.DESCRIPTION
        await ask_for_description(update)
        return

    if text != TXT.send:
        await safe_reply(update, "Оберіть дію кнопкою нижче 👇", reply_markup=CONFIRM_KB)
        return

    try:
        await send_request(update, context)
        await safe_reply(
            update,
            "Готово ✅ Заявку відправлено. Для нової заявки натисніть /start.",
            reply_markup=ReplyKeyboardRemove(),
        )
        context.user_data.clear()
    except Exception:
        logger.exception("Failed to send request")
        await safe_reply(
            update,
            "⚠️ Не вдалося відправити заявку. Спробуйте ще раз за хвилину.",
            reply_markup=CONFIRM_KB,
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    if not update.message or not update.message.text:
        return
    if await reject_if_blocked(update, context):
        return

    text = update.message.text.strip()

    if text == TXT.cancel:
        await cancel(update, context)
        return
    if text == TXT.restart:
        await restart(update, context)
        return

    step = context.user_data.get("step")
    if not step:
        await safe_reply(
            update,
            "Щоб почати, використайте /start.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if step == Step.NUMBER:
        await handle_number_step(update, context, text)
        return
    if step == Step.TYPE:
        await handle_type_step(update, context, text)
        return
    if step == Step.DESCRIPTION:
        await handle_description_step(update, context, text)
        return
    if step == Step.CONFIRM:
        await handle_confirm_step(update, context, text)
        return

    logger.warning("Unknown step '%s'. Resetting flow.", step)
    await restart(update, context)


def main() -> None:
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN не задано в змінних середовища.")

    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .connect_timeout(REQUEST_CONNECT_TIMEOUT)
        .read_timeout(REQUEST_READ_TIMEOUT)
        .write_timeout(REQUEST_WRITE_TIMEOUT)
        .pool_timeout(REQUEST_POOL_TIMEOUT)
        .build()
    )
    app.bot_data["blocked_users"] = load_blocked_users()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("chatid", chatid))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(CommandHandler("banlist", banlist))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

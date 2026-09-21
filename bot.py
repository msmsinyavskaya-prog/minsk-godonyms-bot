import json
import os
import re
from html import escape

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    setup_application,
)
from rapidfuzz import fuzz


BOT_TOKEN = os.getenv("BOT_TOKEN")
DATA_FILE = os.getenv("DATA_FILE", "streets.json")

if not BOT_TOKEN:
    raise RuntimeError(
        "Не задан BOT_TOKEN. Добавьте переменную окружения BOT_TOKEN в Render."
    )

with open(DATA_FILE, "r", encoding="utf-8") as f:
    STREETS = json.load(f)


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(
        r"\b(улица|ул|проспект|пр-т|переулок|пер|площадь|пл)\b",
        " ",
        text,
    )
    text = re.sub(r"[^а-яa-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


NORMALIZED = {
    item["name"]: normalize(item["name"])
    for item in STREETS
}


def find_matches(query: str, limit: int = 5):
    q = normalize(query)

    if not q:
        return []

    scored = []

    for item in STREETS:
        name = item["name"]
        normalized_name = NORMALIZED[name]

        score = fuzz.WRatio(q, normalized_name)

        if q in normalized_name or normalized_name in q:
            score = max(score, 92)

        scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)

    return [
        (score, item)
        for score, item in scored[:limit]
        if score >= 48
    ]


def clean_description(text: str) -> str:
    text = text or ""

    text = re.sub(r"\s*\[[^\]]+\]", "", text)
    text = re.sub(r"\s*\(\s*[BbВв]\s*\)", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)

    return text.strip()


def format_entry(item: dict) -> str:
    name = escape(item.get("name", "").strip())
    district = escape(item.get("district", "").strip())
    description = escape(
        clean_description(item.get("description", ""))
    )

    paragraphs = [f"<b>{name}</b>"]

    if district:
        paragraphs.append(f"Улица находится в {district}.")

    if description:
        paragraphs.append(description)

    return "\n\n".join(paragraphs)


def split_text(text: str, limit: int = 3900):
    if len(text) <= limit:
        return [text]

    parts = []
    current = ""

    for paragraph in text.split("\n"):
        if len(current) + len(paragraph) + 1 > limit:
            if current:
                parts.append(current)

            current = paragraph
        else:
            current = f"{current}\n{paragraph}".strip()

    if current:
        parts.append(current)

    return parts


dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "🗺️ Здравствуйте! Добро пожаловать в исследовательский бот "
        "«Минск: годонимы как зеркало истории Великой Отечественной войны»!\n\n"
        "Хотите узнать больше об истории улиц Минска? "
        "Я помогу вам найти информацию о годонимах, связанных "
        "с героями и событиями Великой Отечественной войны.\n\n"
        "🔎 Введите название улицы, которая вас интересует.\n\n"
        "Например:\n"
        "• Панфилова\n"
        "• Гастелло\n"
        "• Матусевича\n"
        "• Казинца\n\n"
        "Введите название улицы, и начнём исследование! 🇧🇾"
    )


@dp.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "<b>Как пользоваться ботом</b>\n\n"
        "1. Напишите название улицы полностью или частично.\n"
        "2. Бот выполнит поиск даже при небольших опечатках.\n"
        "3. Если найдено несколько похожих названий, выберите нужное кнопкой.\n\n"
        "Команды: /start, /help, /count."
    )


@dp.message(Command("count"))
async def count_command(message: Message):
    await message.answer(
        f"В базе бота: <b>{len(STREETS)}</b> записей из предоставленной таблицы."
    )


@dp.message(F.text)
async def street_search(message: Message):
    query = message.text.strip()
    matches = find_matches(query)

    if not matches:
        await message.answer(
            "Не удалось найти подходящую улицу.\n"
            "Попробуйте ввести только основную часть названия, "
            "например «Гастелло» или «Панфилова»."
        )
        return

    best_score, best = matches[0]

    if best_score >= 88:
        for part in split_text(format_entry(best)):
            await message.answer(part)
        return

    buttons = []

    for score, item in matches[:5]:
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{item['name']} ({round(score)}%)",
                    callback_data=f"street:{STREETS.index(item)}",
                )
            ]
        )

    await message.answer(
        "Я нашёл несколько похожих названий. Выберите нужную улицу:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@dp.callback_query(F.data.startswith("street:"))
async def street_callback(callback: CallbackQuery):
    try:
        index = int(callback.data.split(":", 1)[1])
        item = STREETS[index]

    except (ValueError, IndexError):
        await callback.answer(
            "Запись не найдена.",
            show_alert=True,
        )
        return

    await callback.answer()

    for part in split_text(format_entry(item)):
        await callback.message.answer(part)


# --------------------------------------------------
# WEBHOOK ДЛЯ RENDER
# --------------------------------------------------

WEBHOOK_PATH = "/telegram/webhook"


async def health_check(request):
    return web.Response(
        text="Minsk Godonyms Bot is running!"
    )


async def on_startup(bot: Bot):
    render_url = os.getenv("RENDER_EXTERNAL_URL")

    if not render_url:
        raise RuntimeError(
            "Переменная RENDER_EXTERNAL_URL не найдена."
        )

    webhook_url = f"{render_url}{WEBHOOK_PATH}"

    await bot.set_webhook(
        url=webhook_url,
        drop_pending_updates=True,
    )

    print(f"Webhook установлен: {webhook_url}")


async def on_shutdown(bot: Bot):
    await bot.delete_webhook()

    print("Webhook удалён.")


async def main():
    bot = Bot(BOT_TOKEN)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()

    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)

    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )

    webhook_handler.register(
        app,
        path=WEBHOOK_PATH,
    )

    setup_application(
        app,
        dp,
        bot=bot,
    )

    port = int(os.getenv("PORT", "10000"))

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port,
    )

    await site.start()

    print(f"Web server запущен на порту {port}")

    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())

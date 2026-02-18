# Avto Fix Bot

Telegram-бот для швидкого створення заявок по авто і відправки їх у робочий чат.

## Можливості

- Покроковий сценарій: номер -> тип проблеми -> опис -> підтвердження
- Редагування будь-якого поля перед відправкою
- Безпечне форматування повідомлень (HTML escape)
- Команди: `/start`, `/help`, `/cancel`, `/chatid`

## Налаштування

1. Створіть `.env`:

```env
BOT_TOKEN=your_bot_token
TARGET_CHAT=your_chatid
ADMIN_IDS=123456789,987654321
REQUEST_CONNECT_TIMEOUT=10
REQUEST_READ_TIMEOUT=25
REQUEST_WRITE_TIMEOUT=25
REQUEST_POOL_TIMEOUT=10
```

2. Встановіть залежності:

```bash
pip install -r requirements.txt
```

3. Запуск:

```bash
python index.py
```

## Нотатки

- Якщо `TARGET_CHAT` порожній або некоректний, заявка не губиться: бот виведе її в stdout.
- Час заявки формується у локальній часовій зоні системи.
- `ADMIN_IDS` - список Telegram user id через кому. Лише ці користувачі можуть керувати блокуванням.
- Блоклист зберігається у `blocked_users.json`.
- Таймаути Telegram API можна змінити через `REQUEST_*_TIMEOUT` у `.env`.

## Антиспам: блокування користувачів

Адмін-команди:

- `/ban <user_id>` - додати користувача в блоклист
- `/unban <user_id>` - прибрати користувача з блоклиста
- `/banlist` - показати поточний блоклист

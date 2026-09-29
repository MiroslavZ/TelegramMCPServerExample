# Telegram MCP-сервер

Инструмент `send_message(text)` отправляет готовый текст в один заранее настроенный
Telegram-чат. Агент не выбирает получателя: `API_TOKEN` и `CHAT_ID` читаются из `.env`
этой директории или переменных окружения (окружение имеет приоритет).
Существующий `.env` не перезаписывайте; образец — `.env.example`.
Бот должен иметь доступ к чату; в личном чате сначала откройте диалог с ботом и отправьте `/start`.

Из корня проекта:

```powershell
.\TelegramMCPServerExample\venv\Scripts\python.exe -m TelegramMCPServerExample.server
```

Либо из директории сервера:

```powershell
cd TelegramMCPServerExample
.\venv\Scripts\python.exe server.py
```

Зависимости при создании нового окружения: `python -m pip install -r requirements.txt`.
MCP endpoint по умолчанию: `http://127.0.0.1:8002/mcp`.
`MCP_HOST`, `MCP_PORT`, `MCP_ACCESS_TOKEN`, `MCP_ALLOWED_HOSTS` и
`MCP_ALLOWED_ORIGINS` поддерживаются локальным модулем `http_server.py`. Для доступа по сети
задайте отдельный `MCP_ACCESS_TOKEN` и передавайте `Authorization: Bearer ...`.
Токен Telegram не используется для доступа к MCP.

Сервер можно перенести в отдельный репозиторий целиком: код и зависимости
находятся в этой директории. Установите её `requirements.txt` и запускайте
`python server.py`; файлы проекта агента для запуска не требуются.

Текст отправляется через официальный [Telegram Bot API sendMessage](https://core.telegram.org/bots/api#sendmessage),
без `parse_mode`. Разбиение сохраняет все символы и порядок, учитывает UTF-16 и
предпочитает границы строк и слов. Лимит части — 4096 единиц UTF-16;
лимит входного текста — 200 000 символов. Между частями выдерживается пауза 1,1 с.
Вся отправка ограничена 75 с, включая паузы и API-запросы. При достижении этого
срока возвращаются уже подтверждённые идентификаторы. Если срок истёк во время
запроса, доставка текущей части неизвестна; во время паузы — она ещё не отправлялась.

При успехе возвращаются `status=sent`, `message_ids`, `total_parts`, `sent_parts`.
При ошибке MCP-ответ содержит `isError=true` и `structuredContent` с теми же
счётчиками, `error`, `failed_part` (номер части с 1) и `delivery_uncertain`.
При `429` также возвращается `retry_after`, если Telegram его предоставил.
После первой ошибки последующие части не отправляются, подтверждённые сообщения
не удаляются. При таймауте или некорректном ответе текущая часть могла дойти:
повторять весь отчёт без проверки чата нельзя. Автоматических повторов POST нет.

Проверка без реальных сообщений и без доступа к сети из корня проекта:

```powershell
.\TelegramMCPServerExample\venv\Scripts\python.exe -m unittest tests.test_telegram_server -v
```

"""Отправка plain-text сообщений через Telegram Bot API без повторов POST."""

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2 as httpx
from dotenv import dotenv_values


MESSAGE_LIMIT = 4096
MAX_TEXT_LENGTH = 200_000
PART_DELAY_SECONDS = 1.1
SEND_TIMEOUT_SECONDS = 75


class _RedactBotToken(logging.Filter):
    """HTTP-клиент пишет URL в INFO; токен Telegram является частью URL."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = re.sub(r"/bot[^/\s'\"?]+", "/bot<redacted>", message)
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True


for _logger_name in ("httpx2", "httpcore2.http11", "httpcore2.http2", "httpcore2.proxy"):
    logging.getLogger(_logger_name).addFilter(_RedactBotToken())


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(repr=False)
    chat_id: str = field(repr=False)

    @classmethod
    def from_env(cls) -> "Settings":
        # Не переносим настройки одного сервера в окружение соседнего сервера.
        values = dotenv_values(Path(__file__).with_name(".env"))
        return cls(
            bot_token=os.environ.get("API_TOKEN", values.get("API_TOKEN") or "").strip(),
            chat_id=os.environ.get("CHAT_ID", values.get("CHAT_ID") or "").strip(),
        )


class DeliveryError(Exception):
    """Содержит подтверждённую часть результата, даже при прерванной отправке."""

    def __init__(self, result: dict[str, Any]):
        super().__init__(result["error"])
        self.result = result


def split_text(text: str) -> list[str]:
    """Разбить без потерь, считая символы вне BMP за две единицы UTF-16."""
    parts: list[str] = []
    start = 0
    while start < len(text):
        end, units = start, 0
        while end < len(text):
            width = 2 if ord(text[end]) > 0xFFFF else 1
            if units + width > MESSAGE_LIMIT:
                break
            units += width
            end += 1
        if end < len(text):
            # Сохраняем разделитель в предыдущей части. Разрыв близко к началу
            # породил бы много маленьких сообщений, поэтому ищем во второй половине.
            middle = start + (end - start) // 2
            boundary = text.rfind("\n", middle, end)
            if boundary < 0:
                boundary = next((i for i in range(end - 1, middle - 1, -1) if text[i].isspace()), -1)
            if boundary >= middle:
                end = boundary + 1
        parts.append(text[start:end])
        start = end
    return parts


def create_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30, follow_redirects=False)


def _failure(
    message: str, total: int, sent: list[int], *, part: int | None = None,
    uncertain: bool = False, retry_after: int | None = None,
) -> DeliveryError:
    result: dict[str, Any] = {
        "status": "partial" if sent else "unknown" if uncertain else "failed",
        "message_ids": list(sent),
        "total_parts": total,
        "sent_parts": len(sent),
        "failed_part": part,
        "delivery_uncertain": uncertain,
        "error": message,
    }
    if retry_after is not None:
        result["retry_after"] = retry_after
    return DeliveryError(result)


async def send_text(text: str) -> dict[str, Any]:
    """Последовательно отправить текст в настроенный чат, остановившись при ошибке."""
    if not text.strip() or len(text) > MAX_TEXT_LENGTH:
        raise _failure(f"Текст должен содержать от 1 до {MAX_TEXT_LENGTH} символов и не быть пустым.", 0, [])
    if any(0xD800 <= ord(char) <= 0xDFFF for char in text):
        raise _failure("Текст содержит некорректные Unicode-символы.", 0, [])
    parts = split_text(text)
    if any(not part.strip() for part in parts):
        raise _failure("Одна из частей содержит только пробелы. Сократите длинные пробельные фрагменты.", len(parts), [])
    settings = Settings.from_env()
    if not settings.bot_token or not settings.chat_id:
        raise _failure("Задайте API_TOKEN и CHAT_ID в TelegramMCPServerExample/.env.", len(parts), [])
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", settings.bot_token):
        raise _failure("API_TOKEN имеет неверный формат токена Telegram-бота.", len(parts), [])

    message_ids: list[int] = []
    deadline = asyncio.get_running_loop().time() + SEND_TIMEOUT_SECONDS
    async with create_client() as client:
        for part_number, part in enumerate(parts, start=1):
            in_flight = False
            try:
                async with asyncio.timeout_at(deadline):
                    if part_number > 1:
                        await asyncio.sleep(PART_DELAY_SECONDS)
                    in_flight = True
                    response = await client.post(
                        f"https://api.telegram.org/bot{settings.bot_token}/sendMessage",
                        json={"chat_id": settings.chat_id, "text": part},
                    )
                    in_flight = False
            except TimeoutError:
                raise _failure(
                    "Исчерпано общее время отправки. Сохранены подтверждённые message_ids; "
                    + ("текущая часть могла быть доставлена. Проверьте чат перед повторной отправкой."
                       if in_flight else "текущая и последующие части не отправлялись."),
                    len(parts), message_ids, part=part_number, uncertain=in_flight,
                ) from None
            except httpx.RequestError:
                raise _failure(
                    "Не получено подтверждение Telegram из-за сетевой ошибки или таймаута. "
                    "Текущая часть могла быть доставлена; проверьте чат перед повторной отправкой.",
                    len(parts), message_ids, part=part_number, uncertain=True,
                ) from None
            try:
                payload = response.json()
            except ValueError:
                payload = None

            if isinstance(payload, dict) and payload.get("ok") is False:
                code = payload.get("error_code", response.status_code)
                error = {
                    400: "Telegram отклонил запрос. Проверьте CHAT_ID, доступ бота к чату и текст.",
                    401: "Telegram отклонил токен бота. Проверьте API_TOKEN.",
                    403: "Бот не может писать в чат: проверьте его права и блокировку.",
                    404: "Telegram не нашёл запрошенный ресурс. Проверьте настройки бота.",
                    429: "Превышен лимит отправки Telegram. Последующие части не отправлялись.",
                }.get(code if type(code) is int else None, "Telegram отклонил отправку сообщения.")
                parameters = payload.get("parameters")
                retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
                if type(retry_after) is not int or retry_after < 0:
                    retry_after = None
                raise _failure(error, len(parts), message_ids, part=part_number, retry_after=retry_after)

            result = payload.get("result") if isinstance(payload, dict) else None
            message_id = result.get("message_id") if isinstance(result, dict) else None
            if not (response.status_code == 200 and isinstance(payload, dict)
                    and payload.get("ok") is True and type(message_id) is int and message_id >= 0):
                raise _failure(
                    "Telegram вернул неожиданный ответ без подтверждённого message_id. "
                    "Проверьте чат перед повторной отправкой.",
                    len(parts), message_ids, part=part_number, uncertain=True,
                )
            message_ids.append(message_id)

    return {
        "status": "sent", "message_ids": message_ids,
        "total_parts": len(parts), "sent_parts": len(message_ids),
    }

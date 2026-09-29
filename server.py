"""MCP-сервер Telegram: Streamable HTTP на /mcp, порт 8002."""

import json
from typing import Annotated

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

if __package__:
    from . import telegram_api
else:
    import telegram_api


mcp = MCPServer("telegram", instructions=(
    "Отправка готового отчёта пользователю в заранее настроенный Telegram-чат. "
    "В send_message.text передавай полный фактический текст после получения результатов "
    "всех необходимых инструментов других серверов. Сохраняй ссылки на источники. "
    "Большой текст автоматически делится на сообщения. "
    "Отправка имеет внешний эффект: не вызывай инструмент повторно после успеха. "
    "При частичной отправке или неопределённом результате сообщи пользователю "
    "подтверждённые message_ids и ошибку; повтор всего текста может создать дубликаты."
))


@mcp.tool(annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True,
))
async def send_message(
    text: Annotated[str, Field(
        description="Полный готовый текст отчёта для пользователя, без Markdown/HTML разметки; ссылки допустимы",
        min_length=1, max_length=telegram_api.MAX_TEXT_LENGTH,
    )],
) -> CallToolResult:
    """Отправить текст ботом в настроенный чат и вернуть подтверждённые message_ids.

    Части до 4096 единиц UTF-16 отправляются последовательно. Ошибка останавливает
    отправку и возвращает sent_parts, total_parts, failed_part, delivery_uncertain.
    Общий срок отправки — 75 с. Не повторяйте вызов после partial или неопределённой
    доставки: timeout может означать доставку, повтор создаст дубликаты.
    """
    try:
        result = await telegram_api.send_text(text)
    except telegram_api.DeliveryError as error:
        return CallToolResult(
            is_error=True, structured_content=error.result,
            content=[TextContent(type="text", text=json.dumps(error.result, ensure_ascii=False))],
        )
    return CallToolResult(
        structured_content=result,
        content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
    )


if __name__ == "__main__":
    if __package__:
        from .http_server import run
    else:
        from http_server import run

    run(mcp)

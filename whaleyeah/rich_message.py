from __future__ import annotations

import re

from typing import Any

from telegram import Bot, Message, ReplyParameters


InputRichMessage = dict[str, Any]
RICH_MESSAGE_MAX_LENGTH = 32768


_FENCED_CODE_RE = re.compile(r"(^|\n)(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"(?<!`)`(?!`)")


def _rich_text_to_plain(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_rich_text_to_plain(item) for item in value)
    if isinstance(value, dict):
        parts = []
        for key in ("text", "alternative_text", "expression", "label"):
            if key in value:
                parts.append(_rich_text_to_plain(value[key]))
        return "".join(parts)
    return ""


def _rich_block_to_plain(block: Any) -> str:
    if isinstance(block, str):
        return block
    if isinstance(block, list):
        return "\n".join(filter(None, (_rich_block_to_plain(item) for item in block)))
    if not isinstance(block, dict):
        return ""

    parts = []
    if "text" in block:
        parts.append(_rich_text_to_plain(block["text"]))
    if "expression" in block:
        parts.append(str(block["expression"]))
    if "blocks" in block:
        parts.append(_rich_block_to_plain(block["blocks"]))
    if "items" in block:
        parts.extend(_rich_block_to_plain(item.get("blocks", [])) for item in block["items"] if isinstance(item, dict))
    if "cells" in block:
        for row in block["cells"]:
            parts.append("\t".join(_rich_text_to_plain(cell.get("text", "")) for cell in row if isinstance(cell, dict)))
    if "caption" in block:
        parts.append(_rich_text_to_plain(block["caption"]))

    return "\n".join(part for part in parts if part)


def extract_message_text(message: Message) -> str:
    text = (message.text or message.caption or "").strip()
    if text:
        return text

    rich_message = getattr(message, "rich_message", None)
    if rich_message is None:
        rich_message = getattr(message, "api_kwargs", {}).get("rich_message")
    if hasattr(rich_message, "to_dict"):
        rich_message = rich_message.to_dict()
    if isinstance(rich_message, dict):
        return _rich_block_to_plain(rich_message.get("blocks", [])).strip()

    return ""


def _is_odd(value: int) -> bool:
    return value % 2 == 1


def complete_markdown_draft(markdown: str) -> str:
    """Best-effort closure for partial LLM Markdown streamed as a draft."""
    if not markdown:
        return markdown

    completed = markdown

    fences = _FENCED_CODE_RE.findall(completed)
    if _is_odd(len(fences)):
        completed += f"\n{fences[-1][1]}"

    if _is_odd(len(_INLINE_CODE_RE.findall(completed))):
        completed += "`"

    for marker in ("**", "__", "~~"):
        if _is_odd(completed.count(marker)):
            completed += marker

    if completed.count("[") > completed.count("]"):
        completed += "]"

    last_link_start = completed.rfind("](")
    if last_link_start != -1 and completed.find(")", last_link_start) == -1:
        completed += ")"

    return completed


def markdown_to_rich_message(
    markdown: str,
    *,
    draft: bool = False,
    is_rtl: bool | None = None,
    skip_entity_detection: bool | None = None,
) -> InputRichMessage:
    rich_message: InputRichMessage = {
        "markdown": complete_markdown_draft(markdown) if draft else markdown,
    }
    if is_rtl is not None:
        rich_message["is_rtl"] = is_rtl
    if skip_entity_detection is not None:
        rich_message["skip_entity_detection"] = skip_entity_detection
    return rich_message


async def send_rich_message_draft(
    bot: Bot,
    *,
    chat_id: int,
    draft_id: int,
    markdown: str,
    message_thread_id: int | None = None,
    is_rtl: bool | None = None,
    skip_entity_detection: bool | None = None,
    **api_kwargs: Any,
) -> bool:
    data = {
        "chat_id": chat_id,
        "draft_id": draft_id,
        "message_thread_id": message_thread_id,
        "rich_message": markdown_to_rich_message(
            markdown,
            draft=True,
            is_rtl=is_rtl,
            skip_entity_detection=skip_entity_detection,
        ),
        **api_kwargs,
    }
    return bool(await bot._post("sendRichMessageDraft", data=data))  # type: ignore[attr-defined]


async def send_rich_message(
    bot: Bot,
    *,
    chat_id: int | str,
    markdown: str,
    message_thread_id: int | None = None,
    reply_parameters: ReplyParameters | dict[str, Any] | None = None,
    is_rtl: bool | None = None,
    skip_entity_detection: bool | None = None,
    **api_kwargs: Any,
) -> Message:
    data = {
        "chat_id": chat_id,
        "message_thread_id": message_thread_id,
        "reply_parameters": reply_parameters,
        "rich_message": markdown_to_rich_message(
            markdown,
            is_rtl=is_rtl,
            skip_entity_detection=skip_entity_detection,
        ),
        **api_kwargs,
    }
    result = await bot._post("sendRichMessage", data=data)  # type: ignore[attr-defined]
    return Message.de_json(result, bot)


async def reply_rich_message(
    message: Message,
    *,
    markdown: str,
    is_rtl: bool | None = None,
    skip_entity_detection: bool | None = None,
    **api_kwargs: Any,
) -> Message:
    direct_messages_topic = getattr(message, "direct_messages_topic", None)
    if direct_messages_topic is not None:
        api_kwargs.setdefault("direct_messages_topic_id", direct_messages_topic.topic_id)

    business_connection_id = getattr(message, "business_connection_id", None)
    if business_connection_id is not None:
        api_kwargs.setdefault("business_connection_id", business_connection_id)

    return await send_rich_message(
        message.get_bot(),
        chat_id=message.chat_id,
        markdown=markdown,
        message_thread_id=getattr(message, "message_thread_id", None),
        reply_parameters=ReplyParameters(message_id=message.id),
        is_rtl=is_rtl,
        skip_entity_detection=skip_entity_detection,
        **api_kwargs,
    )

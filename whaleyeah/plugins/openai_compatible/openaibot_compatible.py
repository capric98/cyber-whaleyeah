import asyncio
import base64
import logging
import mimetypes
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

import httpx

from humanfriendly import parse_size
from openai import AsyncOpenAI
from telegram import Message, Update
from telegram.ext import CommandHandler, ContextTypes

from whaleyeah.plugins.media_extractor import extract_media, AttachmentTooLargeError, AttachmentDownloadError
from whaleyeah.rich_message import (
    RICH_MESSAGE_MAX_LENGTH,
    reply_rich_message,
    send_rich_message_draft,
)


logger = logging.getLogger(__name__)

_oai_bots: dict[str, "OpenAICompatibleBot"] = {}

MessageFormat = Literal["chat", "responses"]


def remove_credentials(content: str, credentials: list[str]) -> str:
    for credential in credentials:
        content = content.replace(credential, "*" * len(credential))
    return content


async def xgg_pb_link(text: str, title: str = str(uuid.uuid4())) -> str:
    if len(title) > 40:
        title = title[:36] + "..."
    text = f"# {title}\n" + text

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            url="https://shz.al/",
            files={
                "c": text,
                "e": "3d",
                "p": "true",
            },
        )

        resp.raise_for_status()

        resp = resp.json()
        url = resp["manageUrl"]
        path = url.split("shz.al/")[-1]

    return f"https://shz.al/a/{path}"


async def tg_typing_manager(
    long_task_coroutine,
    periodic_task_func,
    interval_seconds: int,
    long_task_name: str = "ManagedLongTask",
    poller_name: str = "PeriodicPoller",
):
    long_task = asyncio.create_task(long_task_coroutine, name=long_task_name)
    poller_task_exception = None

    async def _poller():
        nonlocal poller_task_exception
        try:
            while not long_task.done():
                await asyncio.sleep(interval_seconds)
                if not long_task.done():
                    await periodic_task_func()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            poller_task_exception = e

    poller_task = asyncio.create_task(_poller(), name=poller_name)

    try:
        return await long_task
    finally:
        if not poller_task.done():
            poller_task.cancel()
            try:
                await poller_task
            except asyncio.CancelledError:
                pass

        if poller_task_exception:
            raise poller_task_exception


def _command_from_message(msg: Message) -> str | None:
    source = msg.caption or msg.text
    if not source:
        return None
    command = source.split(" ", maxsplit=1)[0]
    return command.removeprefix("/").split("@", maxsplit=1)[0]


def _strip_command_text(text: str, command: str, bot_name: str) -> str:
    return text.removeprefix(f"/{command}").removeprefix(bot_name).strip()



def _guess_api_type(config: dict[str, Any]) -> MessageFormat:
    configured = config.get("api_type", config.get("message_format"))
    if configured:
        configured = str(configured).lower()
        if configured in {"chat", "chat_completion", "chat_completions", "chat-completions"}:
            return "chat"
        if configured in {"responses", "response", "new"}:
            return "responses"

    if config.get("use_responses_api") is False:
        return "chat"

    return "responses"


class OpenAICompatibleBot:
    def __init__(self, config: dict[str, Any]) -> None:
        self.api_key = config["api_key"]
        self.model = config["model"]
        self.command = config.get("command", "openai")
        self.endpoint = config.get("endpoint", config.get("base_url"))
        self.api_type = _guess_api_type(config)
        self.create_params = dict(config.get("create_params", {}))
        self.stream = bool(config.get("stream", True))

        self.memory: dict[str, list[Any]] = {}
        self.mem_queue: list[None | str] = [None] * int(config.get("memory_size", 10))

        self.whitelist_chat_ids: list[int] = config.get("whitelist_chat", [])
        self.whitelist_cache: dict[int, bool] = {}

        self.max_attach_size = parse_size(config.get("max_attachment_size", "10MiB"))
        self.enable_vision = bool(
            config.get(
                "enable_vision",
                config.get("multimodal", config.get("support_vision", True)),
            )
        )

        client_kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.endpoint:
            client_kwargs["base_url"] = self.endpoint
        self.client = AsyncOpenAI(**client_kwargs)

    def remember(self, id: str, messages: list[Any]) -> None:
        if id not in self.memory:
            if victim_id := self.mem_queue.pop(0):
                self.memory.pop(victim_id, None)
            self.mem_queue.append(id)

        self.memory[id] = messages

    def _history(self, id: str) -> list[Any]:
        return list(self.memory.get(id, []))

    async def _build_message_from_tg_message(
        self,
        message: Message,
        reply_target_for_errors: Message,
        is_command: bool = False,
        command: str = "",
    ) -> dict[str, Any] | None:
        # Pre-check vision availability if message contains image attachments
        has_image = False
        if message.photo:
            has_image = True
        elif message.document:
            doc_type = message.document.mime_type or mimetypes.guess_type(message.document.file_name or "")[0]
            if doc_type and doc_type.startswith("image/"):
                has_image = True

        if has_image:
            if not self.enable_vision:
                await reply_target_for_errors.reply_text(text="当前模型未启用图片输入，请改用文字提问。")
                return None

        # Determine allowed media types based on model support
        allowed_types: set[str] = set()
        if self.enable_vision:
            allowed_types = {"photo", "document"}

        try:
            extracted = await extract_media(
                message=message,
                max_attach_size=self.max_attach_size,
                allowed_types=allowed_types,
                is_command=is_command,
                command=command,
            )
        except AttachmentTooLargeError as e:
            await reply_target_for_errors.reply_text(str(e))
            return None
        except AttachmentDownloadError as e:
            error_str = f"failed to get attachment file: {e}"
            logger.warning(error_str)
            await reply_target_for_errors.reply_text(remove_credentials(error_str, message.get_bot().token.split(":")))
            return None

        # Check that we only extract images for document attachments
        if extracted.attachments:
            for att in extracted.attachments:
                if att.type == "document" and not att.mime_type.startswith("image/"):
                    await reply_target_for_errors.reply_text(text="尚不支持图片以外的文件哦😭")
                    return None

        # Build message dictionary based on api_type
        # Since the bot only supports a single image at a time (from original code structure),
        # we check if there's any image attachment and build accordingly.
        image_att = next((att for att in extracted.attachments if att.mime_type.startswith("image/")), None)

        if image_att:
            encoded = base64.b64encode(image_att.bytes).decode("ascii")
            image_data_url = f"data:{image_att.mime_type};base64,{encoded}"

            if self.api_type == "responses":
                content_list = []
                if extracted.text:
                    content_list.append({"type": "input_text", "text": extracted.text})
                content_list.append({"type": "input_image", "image_url": image_data_url})
                return {"role": "user", "content": content_list}
            else:
                content_list = []
                if extracted.text:
                    content_list.append({"type": "text", "text": extracted.text})
                content_list.append({"type": "image_url", "image_url": {"url": image_data_url}})
                return {"role": "user", "content": content_list}
        else:
            if extracted.text:
                if self.api_type == "responses":
                    return {"role": "user", "content": [{"type": "input_text", "text": extracted.text}]}
                else:
                    return {"role": "user", "content": extracted.text}
            return None

    def _request_messages(
        self,
        message: dict[str, Any],
        memory_message: dict[str, Any],
        id: str,
        fallback_history: list[Any] | None = None,
    ) -> tuple[list[Any], list[Any]]:
        history = self._history(id)
        if not history and fallback_history:
            history = list(fallback_history)
        return [*history, message], [*history, memory_message]

    async def _stream_response_text(self, request_messages: list[Any]) -> AsyncIterator[str]:
        create_params = dict(self.create_params)
        create_params.pop("stream", None)

        if self.api_type == "responses":
            if self.stream:
                stream = await self.client.responses.create(
                    model=self.model,
                    input=request_messages,
                    stream=True,
                    **create_params,
                )
                async for event in stream:
                    event_type = getattr(event, "type", None)
                    if event_type == "response.output_text.delta":
                        delta_val = getattr(event, "delta", None)
                        if delta_val is not None:
                            yield delta_val
                    elif event_type == "error":
                        raise RuntimeError(getattr(event, "message", "OpenAI streaming error"))
            else:
                resp = await self.client.responses.create(
                    model=self.model,
                    input=request_messages,
                    **create_params,
                )
                if resp.output_text:
                    yield resp.output_text
            return

        if self.stream:
            stream = await self.client.chat.completions.create(
                messages=request_messages,
                model=self.model,
                stream=True,
                **create_params,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
        else:
            resp = await self.client.chat.completions.create(
                messages=request_messages,
                model=self.model,
                **create_params,
            )
            if resp.choices and resp.choices[0].message.content:
                yield resp.choices[0].message.content

    async def _is_allowed(self, update: Update) -> bool:
        sender = update.effective_user
        if not sender:
            return False

        if sender.id in self.whitelist_cache:
            return self.whitelist_cache[sender.id]

        allowed = False
        try:
            for gid in self.whitelist_chat_ids:
                admins = await update.get_bot().get_chat_administrators(chat_id=gid)
                if admins and sender.id in [it.user.id for it in admins]:
                    allowed = True
                    break
        except Exception as e:
            logger.warning(f"failed to get admin list: {e}")

        self.whitelist_cache[sender.id] = allowed
        return allowed

    async def callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if not msg:
            return

        if update.edited_message:
            await msg.reply_text("暂不支持更新对话内容！请重新发送更新后的对话内容。")
            return

        command = _command_from_message(msg)
        if not command:
            return

        if not await self._is_allowed(update):
            return

        reply_target = msg.reply_to_message if msg.reply_to_message else msg

        # Validate that command has text
        source = msg.caption or msg.text
        effective_text = _strip_command_text(source, command, msg.get_bot().name) if source else ""
        if not effective_text:
            if msg.caption:
                await reply_target.reply_text(f"食用方式：/{command}@{update.get_bot().name.removeprefix('@')} 你好")
            else:
                await reply_target.reply_text(f"食用方式：/{command} 你好")
            return

        memory_id = ""
        fallback_history: list[Any] = []
        if msg.reply_to_message:
            memory_id = f"{reply_target.chat_id}<-{reply_target.id}"
            if memory_id not in self.memory:
                replied_msg = await self._build_message_from_tg_message(reply_target, reply_target)
                if replied_msg:
                    fallback_history.append(replied_msg)

        api_message = await self._build_message_from_tg_message(msg, reply_target, is_command=True, command=command)
        if not api_message:
            return

        logger.debug(api_message)

        await reply_target.reply_chat_action("typing")

        bot = update.get_bot()
        chat_id = reply_target.chat_id
        draft_id = update.update_id
        thread_id = getattr(reply_target, "message_thread_id", None)
        resp_text = ""
        last_draft_time = 0.0
        last_typing_time = time.time()

        request_messages, memory_messages = self._request_messages(api_message, api_message, memory_id, fallback_history)

        try:
            async for delta in self._stream_response_text(request_messages):
                resp_text += delta
                current_time = time.time()

                if current_time - last_typing_time > 4.5:
                    last_typing_time = current_time
                    asyncio.create_task(reply_target.reply_chat_action("typing"))

                if current_time - last_draft_time >= 0.5:
                    last_draft_time = current_time
                    temp_text = resp_text.strip()
                    if not temp_text:
                        continue
                    try:
                        await send_rich_message_draft(
                            bot,
                            chat_id=chat_id,
                            draft_id=draft_id,
                            markdown=temp_text[:RICH_MESSAGE_MAX_LENGTH],
                            message_thread_id=thread_id,
                        )
                    except Exception as e:
                        if not any(err in str(e) for err in ("Textdraft_peer_invalid", "Random_id_invalid")):
                            logger.warning(f"failed to send rich draft: {e}")
        except Exception as e:
            error_str = remove_credentials(f"{e}", bot.token.split(":"))
            resp_text += f"\n\n❌ 生成内容时出错:\n{error_str}"
            try:
                await send_rich_message_draft(
                    bot,
                    chat_id=chat_id,
                    draft_id=draft_id,
                    markdown=resp_text[:RICH_MESSAGE_MAX_LENGTH],
                    message_thread_id=thread_id,
                )
            except Exception:
                pass
            logger.error(e)

        if not resp_text.strip():
            resp_text = "API未返回错误信息，但回复为空。"

        resp_text = remove_credentials(resp_text.strip(), bot.token.split(":"))
        memory_messages.append({"role": "assistant", "content": resp_text})

        try:
            if len(resp_text) > RICH_MESSAGE_MAX_LENGTH:
                pb_url = await xgg_pb_link(resp_text, effective_text)
                logger.info(f"too long rich response, upload to pastebin: {pb_url}")
                sent_msg = await reply_target.reply_text(pb_url)
            else:
                try:
                    sent_msg = await reply_rich_message(reply_target, markdown=resp_text)
                except Exception as e:
                    logger.warning(f"failed to send rich message: {e}")
                    sent_msg = await reply_target.reply_text(resp_text)

            try:
                await send_rich_message_draft(
                    bot,
                    chat_id=chat_id,
                    draft_id=draft_id,
                    markdown="",
                    message_thread_id=thread_id,
                )
            except Exception:
                pass

            if sent_msg:
                self.remember(f"{sent_msg.chat_id}<-{sent_msg.id}", memory_messages)
        except Exception as e:
            logger.error(e)
            error_str = remove_credentials(f"{e}", bot.token.split(":"))
            await reply_target.reply_text(error_str)

        logger.debug(update)

    def get_callback(self) -> CommandHandler:
        return CommandHandler(command=self.command, callback=self.callback)


def get_handler(config: dict) -> CommandHandler:
    bot = OpenAICompatibleBot(config)
    _oai_bots[bot.command] = bot
    return bot.get_callback()

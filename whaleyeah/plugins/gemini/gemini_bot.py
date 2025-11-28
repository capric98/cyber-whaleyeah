import logging
import mimetypes

import asyncio
import httpx

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from google import genai
from google.genai import types as genai_types
from telegramify_markdown import markdownify

from whaleyeah.plugins.openai_compatible import xgg_pb_link, tg_typing_manager, remove_credentials


logger = logging.getLogger(__name__)


class GeminiBot:
    def __init__(self, config: dict) -> None:
        self.client  = genai.Client(api_key = config.get("api_key"))
        self.model   = config.get("model", "gemini-2.5-flash-preview-09-2025")
        self.command = config.get("command", "gemini")

        self.memory    = {}
        self.mem_queue: list[None|str] = [None] * int(config.get("memory_size", 10))

        self.whitelist_chat_ids: list[int] = config.get("whitelist_chat", [])
        self.whitelist_cache: dict[int, bool] = {}

    def remember(self, id: str, contents: list) -> None:
        if id not in self.memory:
            if victim_id:=self.mem_queue.pop(0):
                self.memory.pop(victim_id, None)
            self.mem_queue.append(id)

        self.memory[id] = contents

    async def generate_content(self, contents) -> genai_types.GenerateContentResponse | Exception:

        trial_count = 0
        response = Exception("failed to execute generate_content")

        while trial_count < 3:
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=contents,
                )
                return response
            except Exception as e:
                response = e
                logger.error(f"Error generating content: {e}")
                trial_count += 1
                await asyncio.sleep(trial_count)

        return response


gemini: GeminiBot = None # type: ignore


def get_handler(config: dict) -> CommandHandler:
    global gemini
    gemini = GeminiBot(config)

    return CommandHandler(
        command=config.get("command", "gemini"),
        callback=gemini_callback,
    )


async def get_url_bytes(url: str, timeout: float=10.0) -> bytes:
    async with httpx.AsyncClient() as client:
        response = await client.get(url, follow_redirects=True, timeout=timeout)
        response.raise_for_status()
        return response.content

async def gemini_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    msg  = update.effective_message
    if not msg: return

    if msg.caption:
        command = msg.caption.split(" ", maxsplit=1)[0]
    else:
        command = msg.text.split(" ", maxsplit=1)[0] # type: ignore


    command = command.removeprefix("/")
    command = command.split("@", maxsplit=1)[0]


    sender = update.effective_user
    if not sender: return
    if sender.id in gemini.whitelist_cache:
        if not gemini.whitelist_cache[sender.id]:
            return
    else:
        flag = False
        try:
            for gid in gemini.whitelist_chat_ids:
                if not flag:
                    xx = await update.get_bot().get_chat_administrators(chat_id=gid)
                    if xx:
                        xx = [it.user.id for it in xx]
                        if sender.id in xx:
                            flag = True
                            break
        except Exception as e:
            logger.warning(f"failed to get admin list: {e}")

        gemini.whitelist_cache[sender.id] = flag
        if not flag: return


    contents       = []
    reply_target   = msg
    memory_id      = ""
    effective_text = ""


    if msg.reply_to_message:
        reply_target = msg.reply_to_message
        memory_id    = f"msg {reply_target.id} in chat {reply_target.chat_id}"

        try:
            if not reply_target.from_user.is_bot: # type: ignore
                await reply_target.reply_text("这似乎不是bot发送的AI生成消息🤨！无法继续对话")
                return
        except:
            return

        if memory_id in gemini.memory:
            contents.extend(gemini.memory[memory_id])


    if msg.caption:
        attachment_urls = []

        if pic:=msg.photo:
            pic = list(pic)
            pic.sort(key=lambda v: v.width, reverse=True)
            pic = pic[0]
            pic_file = await pic.get_file()
            attachment_urls.append(pic_file.file_path)

        for atype in ["audio", "document"]:
            attachment = getattr(msg, atype, None)
            if attachment:
                afile = await attachment.get_file()
                attachment_urls.append(afile.file_path)


        effective_text = msg.caption.removeprefix(f"/{command}").removeprefix(msg.get_bot().name).strip()
        if not effective_text:
            await reply_target.reply_text(f"食用方式：/{command}@{update.get_bot().name.removeprefix('@')} 你好")
            return


        parts = [genai_types.Part.from_text(text=effective_text)]

        for url in attachment_urls:
            logger.debug(f"gemini attachment mode: file url -> {url}")

            attachment_bytes = await get_url_bytes(url) # type: ignore
            content_type = mimetypes.guess_type(url)[0] # type: ignore
            content_type = content_type if content_type else "image/jpeg" # default to jpeg, let google handle it XD
            logger.debug(f"fetched attachment size: {len(attachment_bytes)} bytes, content_type: {content_type}")

            parts.append(genai_types.Part.from_bytes(data=attachment_bytes, mime_type=content_type))


        contents.append(genai_types.UserContent(parts = parts))


    # if has msg.caption, msg.text is empty?
    if msg.text:
        logger.debug(f"gemini text mode: {msg.text}")
        effective_text = msg.text.removeprefix(f"/{command}").removeprefix(msg.get_bot().name).strip()
        if not effective_text:
            await reply_target.reply_text(f"食用方式：/{command}@{update.get_bot().name.removeprefix('@')} 你好")
            return

        contents.append(genai_types.UserContent(
            parts = [genai_types.Part.from_text(text=effective_text)]
        ))


    if contents:
        logger.debug(contents)

        async def reply_typing_wrapper():
            await reply_target.reply_chat_action("typing")

        await reply_typing_wrapper()

        try:

            resp: genai_types.GenerateContentResponse = await tg_typing_manager(
                gemini.generate_content(contents),
                reply_typing_wrapper,
                interval_seconds = 5,
            ) # type: ignore

            if isinstance(resp, Exception):
                error_str = remove_credentials(f"{resp}", update.get_bot().token.split(":"))
                await reply_target.reply_text("生成内容时出错:\n" + error_str)
                return


            resp_text: str = resp.text # type: ignore
            if len(markdown_resp := markdownify(resp_text)) > 4000:
                pb_url = await xgg_pb_link(resp_text, effective_text)
                logger.info(f"too long response from /{command}, upload to pastebin: {pb_url}")
                msg = await reply_target.reply_text(pb_url)
            else:
                msg = await reply_target.reply_markdown_v2(markdown_resp)

            if msg:
                contents.append(genai_types.ModelContent(resp_text))
                gemini.remember(f"msg {msg.id} in chat {msg.chat_id}", contents=contents)
        except Exception as e:
            logger.error(e)
            error_str = remove_credentials(f"{e}", update.get_bot().token.split(":"))
            await reply_target.reply_text(error_str)



    logger.debug(update)

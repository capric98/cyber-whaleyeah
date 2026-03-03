import logging
import mimetypes
import time

import asyncio
import httpx

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from google import genai
from google.genai import types as genai_types

from telegramify_markdown import markdownify
from humanfriendly import format_size, parse_size
from inflection import camelize

from whaleyeah.plugins.openai_compatible import xgg_pb_link, tg_typing_manager, remove_credentials


logger = logging.getLogger(__name__)


async def get_url_bytes(url: str, timeout: float=10.0) -> bytes:
    async with httpx.AsyncClient() as client:
        response = await client.get(url, follow_redirects=True, timeout=timeout)
        response.raise_for_status()
        return response.content


class GeminiBot:
    def __init__(self, config: dict) -> None:
        self.client  = genai.Client(api_key = config.get("api_key"))
        self.model   = config.get("model", "gemini-2.5-flash-preview-09-2025")
        self.command = config.get("command", "gemini")

        self.memory    = {}
        self.mem_queue: list[None|str] = [None] * int(config.get("memory_size", 10))

        self.whitelist_chat_ids: list[int] = config.get("whitelist_chat", [])
        self.whitelist_cache: dict[int, bool] = {}

        self.max_attach_size = parse_size(config.get("max_attachment_size", "10MiB"))


        gen_tools = []

        for tool_name in config.get("tools", []):

            tool_name: str  = tool_name.strip().lower()

            tool_camel = camelize(tool_name, uppercase_first_letter=True)
            tool_class = getattr(genai_types, tool_camel, None)
            tool_rname = tool_name.removeprefix("tool_")

            if tool_class is not None:
                try:
                    gen_tools.append(genai_types.Tool(**{tool_rname: tool_class}))
                except Exception as e:
                    logger.error(f"Failed to enable Gemini tool {tool_rname}: {e}")
                else:
                    logger.info(f"Enabling Gemini tool: {tool_rname} = {tool_class}")


        logger.debug(f"Gemini tools:\n  {'\n  '.join([str(tool) for tool in gen_tools])}")
        self.generate_config = genai_types.GenerateContentConfig(
            tools=gen_tools,
        )


    def remember(self, id: str, contents: list) -> None:
        if id not in self.memory:
            if victim_id:=self.mem_queue.pop(0):
                self.memory.pop(victim_id, None)
            self.mem_queue.append(id)

        self.memory[id] = contents


    def get_callback(self) -> CommandHandler:


        gemini = self
        async def gemini_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

            msg  = update.effective_message
            if not msg: return

            if update.edited_message:
                await msg.reply_text("暂不支持更新对话内容！请重新发送更新后的对话内容。")
                return

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
                        attachment_size = attachment.file_size
                        if attachment_size and attachment_size > gemini.max_attach_size:
                            await reply_target.reply_text(f"附件超出{format_size(gemini.max_attach_size, binary=True)}大小限制！")
                            return

                        try:
                            afile = await attachment.get_file()
                        except Exception as e:
                            error_str = f"failed to get attachment file: {e}"
                            logger.warning(error_str)
                            await reply_target.reply_text(remove_credentials(error_str, update.get_bot().token.split(":")))
                            return
                        else:
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

                    if len(attachment_bytes) > gemini.max_attach_size:
                        await reply_target.reply_text(f"附件超出{format_size(gemini.max_attach_size, binary=True)}大小限制！")
                        return

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

                await reply_target.reply_chat_action("typing")

                bot = update.get_bot()
                chat_id = reply_target.chat_id
                draft_id = f"draft_{update.update_id}"
                thread_id = getattr(reply_target, "message_thread_id", None)

                msg = None
                resp_text: str = ""
                resp_image: genai_types.Image | None = None
                last_draft_time = 0

                try:
                    stream = await gemini.client.aio.models.generate_content_stream(
                        model=gemini.model,
                        contents=contents,
                        config=gemini.generate_config,
                    )

                    async for chunk in stream:
                        if not chunk.candidates:
                            continue

                        for part in chunk.candidates[0].content.parts:
                            if part.inline_data is not None:
                                resp_image = part.as_image()
                            for part_attr_name in ["text", "executable_code", "code_execution_result"]:
                                part_attr_value = getattr(part, part_attr_name, None)
                                if part_attr_value is not None:
                                    if part_attr_name == "text":
                                        resp_text += f"{part_attr_value}"
                                    else:
                                        resp_text += f"\n{part_attr_value}\n"

                        current_time = time.time()
                        if current_time - last_draft_time > 1:
                            last_draft_time = current_time
                            temp_text = resp_text
                            if temp_text.count("```") % 2 != 0:
                                temp_text += "\n```"

                            try:
                                await bot.send_message_draft(
                                    chat_id=chat_id,
                                    draft_id=draft_id,
                                    text=markdownify(temp_text),
                                    parse_mode="MarkdownV2",
                                    message_thread_id=thread_id
                                )
                            except Exception:
                                try:
                                    await bot.send_message_draft(
                                        chat_id=chat_id,
                                        draft_id=draft_id,
                                        text=temp_text,
                                        message_thread_id=thread_id
                                    )
                                except Exception as e:
                                    logger.warning(f"failed to send draft: {e}")

                except Exception as e:
                    error_str = remove_credentials(f"{e}", bot.token.split(":"))
                    resp_text += f"\n\n❌ 生成内容时出错:\n{error_str}"
                    try:
                        await bot.send_message_draft(
                            chat_id=chat_id,
                            draft_id=draft_id,
                            text=resp_text,
                            message_thread_id=thread_id
                        )
                    except Exception:
                        pass

                try:
                    # Model may response an image only.
                    if resp_text:
                        # Covert to markdown, if failed or too long, send a pastebin link instead.
                        try:
                            markdown_resp = markdownify(resp_text)
                        except Exception as e:
                            logger.error(f"failed to markdownify: {e}")
                            markdown_resp = resp_text + " " * max(5000 - len(resp_text), 100)

                        # Send the reply.
                        if len(markdown_resp) > 4000:
                            pb_url = await xgg_pb_link(text=resp_text, title=effective_text)
                            logger.info(f"too long response, upload to pastebin: {pb_url}")
                            msg = await reply_target.reply_text(pb_url)
                        else:
                            try:
                                msg = await reply_target.reply_markdown_v2(markdown_resp)
                            except Exception:
                                msg = await reply_target.reply_text(resp_text)

                        # Clear draft
                        try:
                            await bot.send_message_draft(
                                chat_id=chat_id,
                                draft_id=draft_id,
                                text="",
                                message_thread_id=thread_id
                            )
                        except Exception:
                            pass

                    # If there's an image, send it as well.
                    if resp_image:
                        imsg = await reply_target.reply_photo(photo=resp_image.image_bytes) # type: ignore
                        msg = msg if msg else imsg # prefer text message as msg

                    # If reply successful, remember the conversation.
                    if msg and resp_text:
                        contents.append(genai_types.ModelContent(resp_text))
                        gemini.remember(f"msg {msg.id} in chat {msg.chat_id}", contents=contents)

                except Exception as e:
                    logger.error(e)
                    error_str = remove_credentials(f"{e}", update.get_bot().token.split(":"))
                    await reply_target.reply_text(error_str)



            logger.debug(update)


        return CommandHandler(
            command=self.command,
            callback=gemini_callback,
        )


def get_handler(config: dict) -> CommandHandler:
    gemini = GeminiBot(config)
    return gemini.get_callback()

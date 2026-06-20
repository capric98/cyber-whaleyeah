import logging
import time

import asyncio

from telegram import Update, Message
from telegram.ext import ContextTypes, CommandHandler

from google import genai
from google.genai import types as genai_types

from humanfriendly import parse_size
from inflection import camelize

from whaleyeah.plugins.openai_compatible import xgg_pb_link, remove_credentials
from whaleyeah.plugins.media_extractor import extract_media, AttachmentTooLargeError, AttachmentDownloadError
from whaleyeah.rich_message import RICH_MESSAGE_MAX_LENGTH, reply_rich_message, send_rich_message_draft


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


    async def _build_gemini_content(
        self,
        message: Message,
        reply_target_for_errors: Message,
        is_command: bool = False,
        command: str = "",
    ) -> genai_types.UserContent | None:
        try:
            extracted = await extract_media(
                message=message,
                max_attach_size=self.max_attach_size,
                allowed_types={"photo", "audio", "document"},
                is_command=is_command,
                command=command,
            )
        except AttachmentTooLargeError as e:
            await reply_target_for_errors.reply_text(str(e))
            return None
        except AttachmentDownloadError as e:
            error_str = f"failed to download attachment: {e}"
            logger.warning(error_str)
            await reply_target_for_errors.reply_text(remove_credentials(error_str, message.get_bot().token.split(":")))
            return None

        parts = []
        if extracted.text:
            parts.append(genai_types.Part.from_text(text=extracted.text))

        for att in extracted.attachments:
            parts.append(genai_types.Part.from_bytes(data=att.bytes, mime_type=att.mime_type))

        if not parts:
            return None

        return genai_types.UserContent(parts=parts)


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


            reply_target   = msg.reply_to_message if msg.reply_to_message else msg

            if msg.caption:
                effective_text = msg.caption.removeprefix(f"/{command}").removeprefix(msg.get_bot().name).strip()
            elif msg.text:
                effective_text = msg.text.removeprefix(f"/{command}").removeprefix(msg.get_bot().name).strip()
            else:
                effective_text = ""

            if not effective_text:
                await reply_target.reply_text(f"食用方式：/{command}@{update.get_bot().name.removeprefix('@')} 你好")
                return

            contents       = []
            memory_id      = ""

            if msg.reply_to_message:
                memory_id    = f"msg {reply_target.id} in chat {reply_target.chat_id}"

                if memory_id in gemini.memory:
                    contents.extend(gemini.memory[memory_id])
                else:
                    replied_content = await gemini._build_gemini_content(reply_target, reply_target)
                    if replied_content:
                        contents.append(replied_content)

            current_content = await gemini._build_gemini_content(msg, reply_target, is_command=True, command=command)
            if current_content:
                contents.append(current_content)


            if contents:
                logger.debug(contents)

                await reply_target.reply_chat_action("typing")

                bot = update.get_bot()
                chat_id = reply_target.chat_id
                draft_id = update.update_id
                thread_id = getattr(reply_target, "message_thread_id", None)

                msg = None
                resp_text: str = ""
                resp_image: genai_types.Image | None = None
                last_draft_time = 0
                last_typing_time = time.time()

                try:
                    stream = await gemini.client.aio.models.generate_content_stream(
                        model=gemini.model,
                        contents=contents,
                        config=gemini.generate_config,
                    )

                    async for chunk in stream:
                        if not chunk.candidates or not chunk.candidates[0].content or not chunk.candidates[0].content.parts:
                            continue

                        for part in chunk.candidates[0].content.parts:
                            if part.inline_data is not None:
                                resp_image = part.as_image()
                            for part_attr_name in ["text", "executable_code", "code_execution_result"]:
                                part_attr_value = getattr(part, part_attr_name, None)
                                if part_attr_value is not None:
                                    match part_attr_name:
                                        case "text":
                                            resp_text += f"{part_attr_value}"
                                        case "executable_code":
                                            resp_text += f"\n🛠️代码执行：\n```\n{part_attr_value.code}\n```\n"
                                        case "code_execution_result":
                                            resp_text += f"\n💻执行结果：\n{part_attr_value.output}\n"
                                        case _:
                                            resp_text += f"\n{part_attr_value}\n"

                        current_time = time.time()

                        if current_time - last_draft_time >= 0.5:
                            last_draft_time = current_time

                            if current_time - last_typing_time > 4.5:
                                last_typing_time = current_time
                                asyncio.create_task(reply_target.reply_chat_action("typing"))

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
                                # Some chat types do not accept streamed drafts; keep generation running.
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

                try:
                    # Model may response an image only.
                    if resp_text:
                        if len(resp_text) > RICH_MESSAGE_MAX_LENGTH:
                            pb_url = await xgg_pb_link(text=resp_text, title=effective_text)
                            logger.info(f"too long rich response, upload to pastebin: {pb_url}")
                            msg = await reply_target.reply_text(pb_url)
                        else:
                            try:
                                msg = await reply_rich_message(reply_target, markdown=resp_text)
                            except Exception as e:
                                logger.warning(f"failed to send rich message: {e}")
                                msg = await reply_target.reply_text(resp_text)

                        # Clear draft
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

                    # If there's an image, send it as well.
                    if resp_image:
                        imsg = await reply_target.reply_photo(photo=resp_image.image_bytes) # type: ignore
                        msg = msg if msg else imsg # prefer text message as msg

                    # If reply successful, remember the conversation.
                    if msg and resp_text:
                        contents.append(
                            genai_types.Content(
                                role="model",
                                parts=[genai_types.Part.from_text(text=resp_text)],
                            )
                        )
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

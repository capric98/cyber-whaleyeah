import logging
import mimetypes
import uuid

import asyncio
import httpx

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from telegram.request import HTTPXRequest

from openai import AsyncOpenAI
from telegramify_markdown import markdownify

logger = logging.getLogger(__name__)

oai_comp_dict = dict()

class OpenAICompBot:
    def __init__(self, config: dict) -> None:
        self._API_KEY   = config["api_key"]
        self._MODEL     = config["model"]
        self._mem_queue = [None] * config["memory_size"]
        self._memory    = {}
        self._wlchatids = config["whitelist_chat"]
        self._whitelist = {}
        self._endpoint  = config["endpoint"]
        self._command   = config["command"]

    @property
    def model(self) -> str:
        return self._MODEL
    @property
    def whitelist(self) -> dict:
        return self._whitelist
    @property
    def whitelist_chat_ids(self) -> list:
        return self._wlchatids

    def remember(self, messages: list, id: str) -> None:
        if id in self._memory:
            self._memory[id] = messages
        else:
            victim = self._mem_queue.pop(1)
            if victim: self._memory.__delitem__(victim)
            self._mem_queue.append(id)
            self._memory[id] = messages

    async def request(self, message: dict, id: str="") -> str:
        client = AsyncOpenAI(
            api_key=self._API_KEY,
            base_url=self._endpoint
        )

        messages = self._memory[id] if id in self._memory else []
        messages.append(message)

        resp = ""
        trial_count = 0
        think_flag  = False


        while not resp and trial_count<3:

            trial_count += 1

            if trial_count>1:
                logger.warning(f"/{self._command} get empty response, retry ({trial_count}/3)")

            try:
                stream = await client.chat.completions.create(
                    messages=messages,
                    model=self._MODEL,
                    stream=True,
                )
                async for chunk in stream:
                    if chunk.choices[0].delta.content:
                        if (not resp) and (chunk.choices[0].delta.content.startswith("<think>")): think_flag = True
                        if think_flag:
                            if "</think>" in chunk.choices[0].delta.content: think_flag = False
                        else:
                            resp += chunk.choices[0].delta.content
            except Exception as e:
                if resp: resp += f"\nError: {e}"
            finally:
                logger.debug(f"{self._MODEL} response (trial #{trial_count}): \"{resp}\"")


        if not resp: resp = "API未返回错误信息，但回复为空。"

        messages.append({
            "role": "assistant",
            "content": resp.strip(),
        })

        return resp, messages


oai = None

def get_handler(config: dict) -> CommandHandler:
    global oai_comp_dict

    oai = OpenAICompBot(config)
    oai_comp_dict[oai._command] = oai

    return CommandHandler(
        command=oai._command,
        callback=openai_callback,
    )

def remove_credentials(content: str, credentials: list[str]) -> str:
    for credential in credentials:
        content = content.replace(credential, "*"*len(credential))
    return content


async def xgg_pb_link(text: str, title: str=str(uuid.uuid4())) -> str:
    if len(title) > 40: title = title[:36] + "..."
    text = f"# {title}\n" + text

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            url   = "https://shz.al/",
            files = {
                "c": text,
                "e": "3d",
                "p": "true",
            },
        )

        resp.raise_for_status()

        resp = resp.json()
        url  = resp["manageUrl"]
        path = url.split("shz.al/")[-1]

    return f"https://shz.al/a/{path}"


async def tg_typing_manager(
    long_task_coroutine,
    periodic_task_func,
    interval_seconds: int,
    long_task_name: str = "ManagedLongTask",
    poller_name: str = "PeriodicPoller"
):

    long_task = asyncio.create_task(long_task_coroutine, name=long_task_name)

    long_task_final_result = None
    long_task_exception = None
    poller_task_exception = None

    async def _poller():
        try:
            while not long_task.done():
                await asyncio.sleep(interval_seconds)

                if not long_task.done():
                    await periodic_task_func()
                else:
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            nonlocal poller_task_exception
            poller_task_exception = e

    poller_task = asyncio.create_task(_poller(), name=poller_name)

    try:
        long_task_final_result = await long_task
    except asyncio.CancelledError:
        long_task_exception = asyncio.CancelledError() # 记录下来
    except Exception as e:
        long_task_exception = e # 记录下来


    if not poller_task.done():
        try:
            await poller_task
        except asyncio.CancelledError:
            pass

    if poller_task.done() and not poller_task.cancelled() and poller_task.exception() and not poller_task_exception:
        poller_task_exception = poller_task.exception()

    if long_task_exception:
        raise long_task_exception

    if poller_task_exception:
        raise poller_task_exception

    return long_task_final_result



async def openai_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # logger.debug(f"openai!! {update}")

    msg  = update.effective_message
    if not msg: return

    if msg.caption:
        command = msg.caption.split(" ", maxsplit=1)[0]
    else:
        command = msg.text.split(" ", maxsplit=1)[0]

    command = command.removeprefix("/")
    command = command.split("@", maxsplit=1)[0]

    global oai_comp_dict
    oai = oai_comp_dict[command]


    sender = update.effective_user
    if not sender: return
    if sender.id in oai.whitelist:
        if not oai.whitelist[sender.id]:
            return
    else:
        flag = False
        try:
            for gid in oai.whitelist_chat_ids:
                if not flag:
                    xx = await update.get_bot().get_chat_administrators(chat_id=gid)
                    if xx:
                        xx = [it.user.id for it in xx]
                        if sender.id in xx:
                            flag = True
                            break
        except Exception as e:
            logger.warning(f"failed to get admin list: {e}")

        oai.whitelist[sender.id] = flag
        if not flag: return


    memory_id    = ""
    message      = None
    reply_target = msg


    if msg.reply_to_message:
        reply_target = msg.reply_to_message
        memory_id    = f"{reply_target.chat_id}<-{reply_target.id}"

        try:
            if not reply_target.from_user.is_bot:
                await reply_target.reply_text("这似乎不是bot发送的OpenAI消息🤨！")
                return
        except:
            return


    if msg.caption:
        pic = msg.photo
        doc = msg.document
        if pic:
            pic = list(pic)
            pic.sort(key=lambda v: v.width, reverse=True)
            pic = pic[0]
        elif doc:
            pic = doc

            if not mimetypes.guess_type(pic.file_name)[0].startswith("image"):
                # TODO: handle other document type
                await reply_target.reply_text(text="尚不支持图片以外的文件哦😭")
        else:
            await reply_target.reply_text(text="尚不支持图片以外的文件哦😭")
            return

        effective_text = msg.caption.removeprefix(f"/{command}").removeprefix(msg.get_bot().name).strip()
        if not effective_text:
            await reply_target.reply_text(f"食用方式：/${command} 你好")
            return

        f = await pic.get_file()

        message = {
            "role": "user",
            "name": str(sender.id),
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"{f.file_path}"},
                },
                {
                    "type": "text",
                    "text": f"{effective_text}",
                },
            ],
        }


    if msg.text:
        effective_text = msg.text.removeprefix(f"/{command}").removeprefix(msg.get_bot().name).strip()
        if not effective_text:
            await reply_target.reply_text(f"食用方式：/{command} 你好")
            return

        message = {
            "role": "user",
            "name": str(sender.id),
            "content": effective_text,
        }


    if message:
        logger.debug(message)

        async def reply_typing_wrapper():
            await reply_target.reply_chat_action("typing")

        await reply_typing_wrapper()

        try:
            # resp, messages = await oai.request(message, memory_id)
            resp, messages = await tg_typing_manager(
                oai.request(message, memory_id),
                reply_typing_wrapper,
                interval_seconds = 5,
            )

            resp = remove_credentials(resp, update.get_bot().token.split(":"));
            if len(markdown_resp := markdownify(resp)) > 4000:
                pb_url = await xgg_pb_link(resp, effective_text)
                logger.info(f"too long response from {command}, upload to pastebin: {pb_url}")
                msg = await reply_target.reply_text(pb_url)
            else:
                msg = await reply_target.reply_markdown_v2(markdown_resp)

            if msg:
                oai.remember(messages, f"{msg.chat_id}<-{msg.id}")
        except Exception as e:
            logger.error(e)
            error_str = remove_credentials(f"{e}", update.get_bot().token.split(":"))
            await reply_target.reply_text(error_str)



    logger.debug(update)

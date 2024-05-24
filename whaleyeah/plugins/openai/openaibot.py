import logging
import re

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from openai import AsyncOpenAI
import telegramify_markdown

logger = logging.getLogger(__name__)

__COMMAND__ = "openai"


class OpenAIBot:
    def __init__(self, config: dict) -> None:
        self._API_KEY   = config["api_key"]
        self._MODEL     = config["model"]
        self._mem_queue = [None] * config["memory_size"]
        self._memory    = {}
        self._wlchatids = config["whitelist_chat"]
        self._whitelist = {}

    @property
    def model(self) -> str:
        return self._MODEL
    @property
    def whitelist(self) -> dict:
        return self._whitelist
    @property
    def whitelist_chat_ids(self) -> list:
        return self._wlchatids

    async def request(self, message: dict, id: str="") -> str:
        client = AsyncOpenAI(api_key=self._API_KEY)

        messages = self._memory[id] if id in self._memory else []
        messages.append(message)

        stream = await client.chat.completions.create(
            messages=messages,
            model=self._MODEL,
            stream=True,
        )

        resp = ""
        async for chunk in stream:
            if chunk.choices[0].delta.content:
                resp += chunk.choices[0].delta.content

        cmpl_id = chunk.id
        if not cmpl_id.startswith("chatcmpl-"):
            logger.warning(f"chat id: {cmpl_id}")
            cmpl_id = "chatcmpl-"+cmpl_id

        messages.append({
            "role": "assistant",
            "content": resp,
        })
        if cmpl_id in self._memory:
            self._memory[cmpl_id] = messages
        else:
            victim = self._mem_queue.pop(1)
            if victim: self._memory.__delitem__(victim)
            self._mem_queue.append(cmpl_id)
            self._memory[cmpl_id] = messages

        resp += f"\n`[[{cmpl_id}]]`"
        return resp

oai = None

def get_handler(config: dict) -> CommandHandler:
    global oai
    oai = OpenAIBot(config)

    return CommandHandler(
        command=__COMMAND__,
        callback=openai_callback,
    )

async def openai_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # logger.debug(f"openai!! {update}")

    msg  = update.effective_message
    if not msg: return


    global oai


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


    completion_id = ""
    message       = None
    reply_target  = msg


    if msg.reply_to_message:
        rmsg = msg.reply_to_message
        try:
            if not rmsg.from_user.is_bot:
                await msg.reply_text("被回复内容似乎不是bot发送的OpenAI消息🤨！")
                return
        except:
            return

        match = re.search(r"(?<=\[\[)chatcmpl-.*?(?=\]\])", rmsg.text)
        if match: completion_id = match.group(0)
        reply_target = rmsg

    if msg.caption:
        pic = msg.photo
        doc = msg.document
        if pic:
            pic = list(pic)
            pic.sort(key=lambda v: v.width, reverse=True)
            pic = pic[0]
        elif doc:
            pic = doc
            # TODO: handle other document type
        else:
            await msg.reply_text(text="尚不支持图片以外的文件哦😭")
            return

        f = await pic.get_file()
        effective_text = msg.caption.removeprefix(f"/{__COMMAND__}").removeprefix(msg.get_bot().name).strip()

        if not effective_text:
            await msg.reply_text("食用方式：/openai 你好")
            return

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
        effective_text = msg.text.removeprefix(f"/{__COMMAND__}").strip()
        message = {
            "role": "user",
            "name": str(sender.id),
            "content": effective_text,
        }


    if message:
        logger.debug(message)

        await msg.reply_chat_action("typing")

        try:
            resp = await oai.request(message, completion_id)
        except Exception as e:
            logger.error(e)
            await reply_target.reply_text(f"{e}")
        else:
            await reply_target.reply_markdown_v2(telegramify_markdown.convert(resp).replace("\n\n", "\n"))


    logger.debug(update)

import logging

from telegram import Update, Message
from telegram.ext import ContextTypes, CommandHandler
from telegram.constants import ParseMode

from openai import AsyncOpenAI
import telegramify_markdown

logger = logging.getLogger(__name__)

__COMMAND__ = "chat"


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

    def prepare_msgs(self, msg: Message, id: str="") -> list:
        messages = self._memory[id] if id in self._memory else []

        tg_msg = msg
        msg    = {"role": "user"}

        messages.append(msg)
        return messages

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


    if msg.reply_to_message:
        msg.reply_text("开发中！")
        return
    if msg.caption:
        msg.reply_text("开发中！")
        return
    if msg.text:
        effective_text = msg.text.removeprefix(f"/{__COMMAND__}").strip()
        message = {
            "role": "user",
            "name": str(sender.id),
            "content": effective_text,
        }

        logger.debug(message)

        resp = await oai.request(message, completion_id)
        await msg.reply_markdown_v2(telegramify_markdown.convert(resp).replace("\n\n", "\n"))


    logger.debug(update)

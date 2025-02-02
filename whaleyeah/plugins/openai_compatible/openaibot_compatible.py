import logging
import mimetypes

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from openai import AsyncOpenAI
from telegramify_markdown import markdownify

logger = logging.getLogger(__name__)

oai_comp_dict = dict()

class OpenAIBot:
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

        while resp=="" and trial_count<3:
            trial_count += 1
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

        if not resp: resp = "API未返回错误信息，但回复为空。"

        messages.append({
            "role": "assistant",
            "content": resp.strip(),
        })

        return resp, messages


oai = None

def get_handler(config: dict) -> CommandHandler:
    global oai_comp_dict

    oai = OpenAIBot(config)
    oai_comp_dict[oai._command] = oai

    return CommandHandler(
        command=oai._command,
        callback=openai_callback,
    )

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

        await reply_target.reply_chat_action("typing")

        try:
            resp, messages = await oai.request(message, memory_id)
        except Exception as e:
            logger.error(e)
            error_str = f"{e}"
            for token_part in update.get_bot().token.split(":"):
                error_str = error_str.replace(token_part, "*"*len(token_part))
            await reply_target.reply_text(error_str)
        else:
            msg = await reply_target.reply_markdown_v2(markdownify(resp))
            if msg:
                oai.remember(messages, f"{msg.chat_id}<-{msg.id}")


    logger.debug(update)

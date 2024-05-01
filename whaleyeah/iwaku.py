import asyncio
import logging

import jieba

from telegram import Update
from telegram.ext import MessageHandler, InlineQueryHandler, ContextTypes
from motor.motor_asyncio import AsyncIOMotorClient
from motor.motor_asyncio import AsyncIOMotorDatabase, AsyncIOMotorCollection

class MobClass:
    def __init__(self) -> None:
        self._database = None
        self._history  = None
        self._tokens   = None
    def __setattr__(self, name, value):
        self.__dict__[f"_{name}"] = value

    @property
    def database(self) -> AsyncIOMotorDatabase:
        return self._database
    @property
    def history(self) -> AsyncIOMotorCollection:
        return self._history
    @property
    def tokens(self) -> AsyncIOMotorCollection:
        return self._tokens

mob = MobClass()

logger   = logging.getLogger(__name__)

def iwaku_history_handler() -> MessageHandler:
    jieba.setLogLevel(logger.getEffectiveLevel())
    return MessageHandler(filters=None, callback=_iwaku_history_callback)
def iwaku_inline_handler() -> InlineQueryHandler:
    return InlineQueryHandler(callback=_iwaku_inline_callback)


def init_database(db_config):
    global mob

    try:
        client = AsyncIOMotorClient(db_config["uri"], io_loop=asyncio.get_event_loop())
        mob.database = client.get_database(db_config["db_name"])
        mob.history  = mob.database.get_collection("history")
        mob.tokens   = mob.database.get_collection("tokens")
    except Exception as e:
        logger.warning(f"Error: '{e}'")

def trim_tokens(tokens: list[str]) -> list[str]:
    return [v for v in tokens if len(v.encode())>1]


async def _iwaku_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context: logger.warning("context is None")

    prefix = []
    text   = ""

    msg = update.effective_message
    if msg:
        if msg.text:
            text = msg.text
        else:
            text = msg.caption
            if msg.audio: prefix = ["[音乐]", " "]
            if msg.document: prefix = ["[文件]", " "]
            if msg.animation: prefix = ["[动画]", " "]
            if msg.game: ["[游戏]", " "]
            if msg.photo: prefix = ["[图片]", " "]
            if msg.video: prefix = ["[视频]", " "]
            if msg.voice: prefix = ["[语音]", " "]

    if not text: return

    seg = await asyncio.to_thread(jieba.cut_for_search, text)
    seg = prefix + [s for s in seg]

    text = "".join(seg) # add prefix

    jmsg = msg.to_json()
    logger.debug(jmsg)
    logger.debug(text)
    # logger.info(sys.getsizeof(jmsg)) ~1KB


    try:
        mob_doc = {
            "from": msg.from_user.id,
            "chat": msg.chat_id,
            "mid": msg.id,
            "text": text,
            "date": msg.date,
            "json": jmsg, # original json
            "tokens": trim_tokens(seg),
        }
        if msg==update.edited_message:
            await mob.history.find_one_and_replace(
                {"$and": [
                    {
                        "from": msg.from_user.id,
                        "chat": msg.chat_id,
                        "mid": msg.id,
                    }
                ]},
                mob_doc,
            )
        else:
            await mob.history.insert_one(mob_doc)

    except Exception as e:
        logger.warning(f"failed to write history: {e}")


async def _iwaku_inline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # {"$and": [{"chat": msg.chat_id, "from": msg.from_user.id, "tokens": a, "tokens": b ...}]}
    pass

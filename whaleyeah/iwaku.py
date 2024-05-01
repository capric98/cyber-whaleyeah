import asyncio
import logging

import jieba

from telegram import Update, Message
from telegram.ext import MessageHandler, ContextTypes
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

def iwaku_handler() -> MessageHandler:
    jieba.setLogLevel(logger.getEffectiveLevel())
    return MessageHandler(filters=None, callback=_iwaku_callback)

async def _iwaku_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context: logger.warning("context is None")

    prefix = []
    text   = ""

    msg = update.effective_message
    if msg:
        if msg==update.edited_message: pass # TODO: handle edited message
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

    # content, date, user
    jmsg = msg.to_json()
    # logger.info(jmsg)
    # logger.info(sys.getsizeof(jmsg)) ~1KB
    logger.debug(text)

    mob_doc = {
        "from": msg.from_user.id,
        "chat": msg.chat_id,
        "mid": msg.id,
        "text": text,
        "date": msg.date,
        "json": jmsg, # original json
        "tokens": seg,
    }

    try:
        await mob.history.insert_one(mob_doc)
        # {"$and": [{"chat": msg.chat_id, "tokens": a, "tokens": b ...}]}
    except Exception as e:
        logger.warning(f"failed to write history: {e}")


    # try:
    #     mob_doc.pop("json")
    #     for token in seg:
    #         token = token.strip()
    #         if token:
    #             if await mob.tokens.count_documents({"token": token}, limit=1):
    #                 await mob.tokens.update_one(
    #                     {"token": token},
    #                     {"$push": {"messages": mob_doc}},
    #                 )
    #             else:
    #                 await mob.tokens.insert_one({
    #                     "token": token,
    #                     "messages": [mob_doc],
    #                 })
    #     # search {"$and": [{"token": token, "messages.mid": 134}]}
    # except Exception as e:
    #     logger.warning(f"failed to write tokens: {e}")



def init_database(db_config):
    global mob

    try:
        client = AsyncIOMotorClient(db_config["uri"], io_loop=asyncio.get_event_loop())
        mob.database = client.get_database(db_config["db_name"])
        mob.history  = mob.database.get_collection("history")
        mob.tokens   = mob.database.get_collection("tokens")
    except Exception as e:
        logger.warning(f"Error: '{e}'")
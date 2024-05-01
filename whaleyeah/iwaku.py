import asyncio
import logging

import jieba

from telegram import Update
from telegram.ext import MessageHandler, InlineQueryHandler, ContextTypes

from .database import mob


logger   = logging.getLogger(__name__)

def iwaku_history_handler() -> MessageHandler:
    jieba.setLogLevel(logger.getEffectiveLevel())
    return MessageHandler(filters=None, callback=_iwaku_history_callback)
def iwaku_inline_handler() -> InlineQueryHandler:
    return InlineQueryHandler(callback=_iwaku_inline_callback)


def trim_tokens(tokens: list[str]) -> list[str]:
    results = []
    tmemory = set()

    for v in tokens:
        v = v.strip()
        if len(v.encode())<=1: continue
        if v not in tmemory:
            tmemory.add(v)
            results.append(v)

    return results


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
    # {"$and": [{"chat": msg.chat_id, "from": msg.from_user.id}, {"tokens": a}, {"tokens": b}, ...]}
    pass

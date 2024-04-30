import asyncio
import logging

import jieba

from telegram import Update
from telegram.ext import MessageHandler, ContextTypes

logger = logging.getLogger(__name__)


def iwaku_handler() -> MessageHandler:
    jieba.setLogLevel(logger.getEffectiveLevel())
    return MessageHandler(filters=None, callback=_iwaku_callback)

async def _iwaku_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg  = update.effective_message

    if not msg: return
    if not context: logger.warning("Context is None.")
    if msg==update.edited_message: pass # TODO: handle edited message

    seg = await asyncio.to_thread(jieba.cut_for_search, msg.text)

    await msg.reply_text("/ ".join(seg))
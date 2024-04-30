import logging

from telegram import Update
from telegram.ext import MessageHandler, ContextTypes
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

def get_handler(config: dict) -> MessageHandler:
    logger.warn(DeprecationWarning("This plugin is for debug only!!!"))
    return MessageHandler(
        filters=None,
        callback=callback_func,
    )

async def callback_func(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg  = update.effective_message
    chat = msg.chat
    await msg.reply_markdown(
        f"`chat_type` = `{chat.type}`\n" +
        f"`chat_id` = `{chat.id}`\n" +
        f"`msg_id` = `{msg.id}`\n" +
        f"`from_user` = `{msg.from_user.id}`\n" +
        f"`is_forward` = {not msg.forward_origin==None}\n" +
        f"`is_edited` = {msg==update.edited_message}\n" +
        f"`content` = {msg.text}"
    )
    await chat.send_message("`Hello World!`", parse_mode=ParseMode.MARKDOWN_V2)

    logger.debug(update)
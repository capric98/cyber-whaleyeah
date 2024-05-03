import asyncio
import logging

from telegram import Update
from telegram.ext import MessageHandler, ContextTypes, filters
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)


def megaphone_handler() -> MessageHandler:
    return MessageHandler(filters=filters.COMMAND, callback=_megaphone_callback)

async def _megaphone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    logger.debug("_megaphone_callback!")

    msg = update.effective_message
    if msg.via_bot: return False
    if not msg.text: return False
    if not context: pass

    commands = msg.text.strip().split(" ", maxsplit=1)
    if not commands[0].startswith("/"): return False
    commands[0] = commands[0][1:]
    if commands[0].isascii(): return False


    S_text = f"<a href=\"tg://user?id={update.effective_sender.id}\">{update.effective_sender.full_name}</a>"
    if update.effective_message.reply_to_message:
        ouser = update.effective_message.reply_to_message.from_user
        O_text = f"<a href=\"tg://user?id={ouser.id}\">{ouser.full_name}</a>"
        if ouser==update.effective_sender: O_text="自己"
    elif update.effective_message.external_reply:
        O_text = update.effective_message.external_reply.origin.sender_user_name
    else:
        O_text = "自己"

    if len(commands)<2:
        commands = commands + [""]*(2-len(commands))
        if O_text=="自己": O_text=""

    await asyncio.gather(
        update.effective_message.delete(),
        update.get_bot().send_message(
            chat_id=update.effective_chat.id,
            text=f"{S_text} {commands[0]} {O_text} {commands[1]}".strip(),
            parse_mode=ParseMode.HTML,
        )
    )

    return True
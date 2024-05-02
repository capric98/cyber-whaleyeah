import asyncio
import html
import json
import logging
import math
import time

import jieba

from telegram import Update, InlineQueryResultArticle, InputTextMessageContent, ReplyParameters
from telegram.ext import MessageHandler, InlineQueryHandler, ContextTypes, filters

from .database import mob
from .megaphone import _megaphone_callback


SEARCH_PAGE_SIZE = 10


logger = logging.getLogger(__name__)


def iwaku_history_handler() -> MessageHandler:
    mob.history.create_index("tokens")
    jieba.setLogLevel(logger.getEffectiveLevel())
    return MessageHandler(filters=None, callback=_iwaku_history_callback)
def iwaku_inline_handler() -> InlineQueryHandler:
    return InlineQueryHandler(callback=_iwaku_inline_callback)
def iwaku_locate_handler() -> MessageHandler:
    return MessageHandler(filters=filters.COMMAND, callback=_iwaku_locate_callback)


def trim_tokens(tokens: list[str]) -> list[str]:
    results = []
    tmemory = set()

    for v in tokens:
        v = v.strip()
        if len(v.encode())<=1 and (not v.isalpha()): continue
        if v not in tmemory:
            tmemory.add(v)
            results.append(v)

    return results


async def _iwaku_history_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context: pass

    prefix = []
    text   = ""

    if update.effective_user.is_bot: return

    msg = update.effective_message
    if msg.via_bot: return
    if msg.forward_origin: return

    logger.debug(update)

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
    if text.startswith("/"):
        if await _megaphone_callback(update, context):
            return

    seg = await asyncio.to_thread(jieba.cut_for_search, text)
    seg = prefix + [s for s in seg]

    text = "".join(prefix) + text

    jmsg = update.to_json()
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

        # Unfortunately, a bot cannot get deleted messages.
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
    if not context: pass

    query = update.inline_query.query
    if not query: return

    query = query.split(" ")
    try:
        page  = int(query[-1])
        query = " ".join(query[:-1])
    except ValueError:
        page = 1
        query = " ".join(query)

    query        = query.strip()
    query_tokens = await asyncio.to_thread(jieba.cut_for_search, query)
    query_tokens = trim_tokens(query_tokens)

    if query_tokens:
        # check priviledge
        try:
            # update.chat_member
            xx = await update.get_bot().get_chat_administrators(chat_id=mob.GROUP_ID)
            logger.debug(xx)
            if xx is None:
                return
            xx = [it.user.id for it in xx]
            if update.inline_query.from_user.id not in xx:
                return
        except Exception as e:
            logger.warning(f"failed to check priviledge: {e}")
            return


        filter = {"$and": [{"tokens": v} for v in query_tokens]}
        logger.debug(filter)


        query_start_time = time.time()

        cursor = mob.history.find(filter)
        cursor = cursor.sort("date", -1)

        # total = mob.history.count_documents(filter)
        # docs  = await cursor.to_list(length=page*SEARCH_PAGE_SIZE)
        # docs  = await cursor.to_list(length=None)
        count = len(cursor)

        query_elapsed = time.time() - query_start_time
        logger.info(f"query for \"{query}\" in {1000*query_elapsed:.2f} ms")


        results = [
            InlineQueryResultArticle(
                id='info',
                title='Total:{}. Page {} of {}'.format(count, page, math.ceil(count / SEARCH_PAGE_SIZE)),
                # title='Total:{}. Page {} of ?'.format(count, page),
                input_message_content=InputTextMessageContent('/help')
            )
        ]

        for _ in range(SEARCH_PAGE_SIZE*(page-1), min(SEARCH_PAGE_SIZE*page+1, count)):
            doc = await cursor.next()

            doc_update = update.de_json(json.loads(doc["json"]), update.get_bot())
            message    = doc_update.effective_message
            eff_text   = message.text if message.text else message.caption
            logger.debug(doc_update)
            results.append(
                InlineQueryResultArticle(
                    id=message['id'],
                    title='{}'.format(eff_text[:100]),
                    description=message['date'].strftime("%Y-%m-%d").ljust(40) + message.from_user.full_name,
                    # input_message_content=InputTextMessageContent(
                    #     '{}<a href="{}">「From {}」</a>'.format(html.escape(eff_text), message['link'], message.from_user.name),parse_mode='html'
                    #     ) if
                    # message['link'] != '' or message['id'] < 0 else InputTextMessageContent(
                    #     '/locate {}'.format(message['id']))
                    input_message_content=InputTextMessageContent('/portal {} {}'.format(message.chat_id, message.id)) if message.id>0 else InputTextMessageContent(
                        # '{}<a href="{}">「From {}」</a>'.format(html.escape(eff_text), message['link'], message.from_user.name),parse_mode='html'
                        '{}<a>「From {}」</a>'.format(html.escape(eff_text), message.from_user.full_name),parse_mode='html'
                    ),
                )
            )

        await update.inline_query.answer(results)


async def _iwaku_locate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    msg = update.effective_message
    if not msg.via_bot: return
    if not context: pass

    commands = msg.text.strip().split(" ")
    logger.debug(commands)

    if commands[0].startswith("/portal"):
        bot = update.get_bot()
        # await bot.forward_message(chat_id=msg.chat_id, from_chat_id=commands[1], message_id=commands[2])

        to_chat = str(msg.chat_id)

        if to_chat!=str(mob.GROUP_ID):
            if str(mob.GROUP_ID).endswith(to_chat):
                to_chat = str(mob.GROUP_ID)
        if str(mob.GROUP_ID).endswith(commands[1]):
            commands[1] = str(mob.GROUP_ID)

        try:
            await asyncio.gather(
                update.message.delete(),
                bot.send_message(chat_id=msg.chat_id, text="^", reply_parameters=ReplyParameters(chat_id=commands[1], message_id=commands[2])),
            )
        except:
            pass

    else:
        await _megaphone_callback(update, context)
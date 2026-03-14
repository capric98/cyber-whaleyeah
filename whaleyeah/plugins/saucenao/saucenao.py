import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from telegramify_markdown import markdownify

from .saucenao_api import SauceNao

logger = logging.getLogger(__name__)
sauce  = None

_MAX_REPLY_ = 3



def get_handler(config: dict) -> CommandHandler:
    global sauce
    sauce = SauceNao(config["api_key"])

    return CommandHandler(
        command="saucenao",
        callback=saucenao_callback,
    )

def _reply_saucenao_results(results: list) -> str:
    # results = [v for v in results if isinstance(v, BasicSauce)]
    # logger.info(results)
    resp = f"1. {results[0].author}: [{results[0].index_name}]("+(results[0].urls[0] if results[0].urls else "")+")"
    for k in range(1, min(len(results), _MAX_REPLY_)):
        result = results[k]
        resp  += f"\n{k+1}. {result.author}: [{result.index_name}]("+ (result.urls[0] if result.urls else "") +")"

    return markdownify(resp)

async def saucenao_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    target_msg = msg;

    try:
        if msg and msg.reply_to_message:
            pic = msg.reply_to_message.photo
            doc = msg.reply_to_message.document
            target_msg = msg.reply_to_message
            if pic:
                list(pic).sort(key=lambda v: v.width, reverse=True)
                pic = pic[0]; await msg.delete()
            elif doc:
                pic = doc; await msg.delete()
            else:
                await msg.reply_text(text="没有发现图片文件哦🤨")
                return
        elif msg:
            # document says it will NOT handle caption
            pic = msg.photo
            doc = msg.document
            if pic:
                pic = list(pic)
                pic.sort(key=lambda v: v.width, reverse=True)
                pic = pic[0]
            elif doc:
                pic = doc
            else:
                await msg.reply_text(text="没有发现图片文件哦🤨")
                return
        else:
            await update.get_bot().send_message(
                chat_id=msg.chat_id,
                text="仅支持对图片消息回复！",
            )
            return

        if pic:
            f = await pic.get_file()
            resp = await sauce.from_url(f"{f.file_path}")
            if resp.results:
                await target_msg.reply_markdown_v2(_reply_saucenao_results(resp.results), disable_web_page_preview=True)
            else:
                await target_msg.reply_text("没有找到相似的图片哦😢")
        else:
            await msg.reply_text(text="没有发现图片文件哦🤨")
    except Exception as e:
        logger.warning(e)
        await msg.reply_text(text="服务器返回错误，请稍后再试",)


    logger.debug(update)
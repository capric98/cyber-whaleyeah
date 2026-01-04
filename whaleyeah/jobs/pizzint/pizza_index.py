import asyncio
import logging
import httpx

from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import MessageHandler, CallbackContext
from telegram.constants import ParseMode
from telegramify_markdown import markdownify


logger = logging.getLogger(__name__)
client = httpx.AsyncClient(
    headers={
        "accept": "application/json, text/plain, */*",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    },
    timeout=5,
)

pizzint_endpoint = "https://www.pizzint.watch/api/dashboard-data"


async def _get_pizza_index() -> tuple[int, dict, bool]:
    resp = (await client.get(pizzint_endpoint)).json()
    success = resp.get("success", False)
    defcon_level = resp.get("defcon_level", -1)

    return defcon_level, resp, success


def get_handler(config: dict):
    pizza_index_notify_chats = list(config.get("chat_id", []))
    defcon_level = -1


    async def callback(ctx: CallbackContext):
        nonlocal defcon_level

        current_defcon_level, resp, success = await _get_pizza_index()

        # failed to get pizza index, or defcon level is not updated
        # log warning and return
        if not success or current_defcon_level == -1:
            logger.warning(f"failed to get pizza index: {resp}")
            return

        # first time get defcon level, store but not notify
        # if defcon_level == -1:
        #     defcon_level = current_defcon_level
        #     return

        # lower defcon level means higher risk
        higher_risk = current_defcon_level < defcon_level

        if defcon_level != -1:
            trend_text = "上升" if higher_risk else "下降"
            resp_text  = f"披萨指数由 **DEFCON {defcon_level}** {trend_text}至 **DEFCON {current_defcon_level}**"
        else:
            resp_text = f"当前披萨指数：**DEFCON {current_defcon_level}**\n"

            current_beijing_time = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()

            defcon_at_time = resp.get("defcon_details", {}).get("at_time", current_beijing_time)
            defcon_at_time = datetime.fromisoformat(defcon_at_time)
            defcon_at_time = defcon_at_time.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")

            resp_text += f"*更新于北京时间 {defcon_at_time}*"

        resp_text += "\n*lower index means higher risk*"
        resp_text = markdownify(resp_text)


        if current_defcon_level!=defcon_level:
            try:
                notify_job_list = []
                for chat_id in pizza_index_notify_chats:
                    notify_job_list.append(ctx.bot.send_message(
                        chat_id=chat_id,
                        parse_mode=ParseMode.MARKDOWN_V2,
                        text=resp_text,
                ))
                await asyncio.gather(*notify_job_list)
            except Exception as e:
                logger.warning(f"failed to notify pizza index: {e}")
            finally:
                defcon_level = current_defcon_level


    return callback

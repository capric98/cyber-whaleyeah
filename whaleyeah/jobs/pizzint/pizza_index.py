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
pizza_index_desc = [
    "最高等级，大的要来了。",           # 1 -> emergency
    "临战等级，五角大楼通宵达旦。",      # 2 -> critical
    "警戒等级，可能出现紧张局势。",      # 3 -> alert
    "日常等级，一切正常。",            # 4 -> warning
    "最低等级，今日无事。",            # 5 -> normal
]


async def _get_pizza_index() -> tuple[int, dict, bool]:
    resp = (await client.get(pizzint_endpoint)).json()
    success = resp.get("success", False)
    defcon_level = resp.get("defcon_level", -1)

    return defcon_level, resp, success


def get_handler(config: dict):
    pizza_index_notify_chats = list(config.get("chat_id", []))
    pizza_index_desc = list(config.get("description", pizza_index_desc))
    pizza_index_desc.insert(0, "DEFCON等级从1~5，等级越低风险越高") # pizza index starts from 1

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

        resp_text = f"当前披萨指数：**DEFCON {current_defcon_level}**"

        if 0 < current_defcon_level < len(pizza_index_desc):
            trend_text = "\n披萨指数"

            if defcon_level != -1:
                # lower defcon level means higher risk
                higher_risk = current_defcon_level < defcon_level
                trend_text += "上升至" if higher_risk else "下降至"
            else:
                trend_text += "处于"

            trend_text += f"{pizza_index_desc[current_defcon_level]}"
            resp_text  += trend_text


        if defcon_level == -1:
            current_beijing_time = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()

            defcon_at_time = resp.get("defcon_details", {}).get("at_time", current_beijing_time)
            defcon_at_time = datetime.fromisoformat(defcon_at_time)
            defcon_at_time = defcon_at_time.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")

            resp_text += f"\n*更新于北京时间 {defcon_at_time}*"


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

import asyncio
import logging
import httpx

from telegram import Update
from telegram.ext import MessageHandler, CallbackContext
from telegram.constants import ParseMode


logger = logging.getLogger(__name__)
client = httpx.AsyncClient(
    headers={
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "cache-control": "no-cache",
        "origin": "https://live.bilibili.com",
        "pragma": "no-cache",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    },
    timeout=5,
)

live_info = {}
_blive_notify_chat = []


def get_handler(config: dict):
    global live_info, _blive_notify_chat
    for id in config["rooms"]: live_info[id] = {"live_status": 0}
    if isinstance(config["chat_id"], int): _blive_notify_chat = [config["chat_id"]]
    if isinstance(config["chat_id"], list): _blive_notify_chat = config["chat_id"]
    return callback

async def callback(ctx: CallbackContext):
    for k in live_info:
        if await _check_live(k):
            notify_list = []
            for chat_id in _blive_notify_chat:
                notify_list.append(ctx.bot.send_message(
                    chat_id=chat_id,
                    text=f"开始直播啦！https://live.bilibili.com/{k}"
                ))
            await asyncio.gather(*notify_list)
    pass

async def _check_live(room_id: int) -> bool:
    resp = (await client.get(f"https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo?room_id={room_id}&protocol=0,1&format=0,1,2&codec=0,1,2&qn=0&platform=web&ptype=8&dolby=5&panorama=1")).json()
    logger.debug(resp)

    if resp["code"]!=0 or not("data" in resp and "live_status" in resp["data"]):
        logger.warning(f"failed to get live info for {room_id}: {resp["message"]}")
    else:
        global live_info
        last_status = live_info[room_id]
        live_info[room_id] = resp["data"]
        if last_status["live_status"]!=1 and resp["data"]["live_status"]==1:
            return True

    return False
#!/usr/bin/env python3
# coding: utf-8
import argparse
import asyncio
import json
import logging

import jieba

from datetime import datetime

from whaleyeah import init_database, mob, trim_tokens
from telegram.ext import Application
from telegram import Message, Update, Chat, User

if __name__=="__main__":
    parser = argparse.ArgumentParser(
        prog="recover.py",
        description="Recovery history from telegram dumped json.",
        epilog="_(:з」∠)_",
    )

    parser.add_argument("-c", "--config", type=str, help="configuration json file", default="config.json")
    parser.add_argument("-f", "--dump", type=str, help="telegram json dump file", default="result.json")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)


    if "log_level" not in config: config["log_level"] = "info"

    logging.basicConfig(
        format="[%(levelname)s] %(asctime)s - %(name)s - %(message)s", level=logging.getLevelName(config["log_level"].upper()),
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logger = logging.getLogger(__name__)
    jieba.setLogLevel(logger.getEffectiveLevel())


    init_database(config["database"])
    bot = Application.builder().token(config["token"]).build().bot


    confirm = (input("WARNING: This will DROP ALL the history in the database! [Y/N]")).strip().upper()
    if confirm!="Y": exit(0)

    asyncio.run(mob.history.drop())

    logger.info("Loading dump file...")
    with open(args.dump, "r", encoding="utf-8") as f:
        data = json.load(f)
    logger.info("Loaded!")

    chat_id  = data["id"]
    messages = data["messages"]
    total    = len(messages)
    count    = 0
    lastp    = 0
    currp    = 0

    for msg in messages:

        count += 1
        currp = count / total * 100
        if currp-lastp>1:
            logger.info(f"{currp:5.2f}%...")
            lastp = currp

        if "forwarded_from" in msg: continue
        try:
            obj = Update(
                msg["id"],
                Message(
                    message_id=msg["id"],
                    date=datetime.fromtimestamp(float(msg["date_unixtime"])),
                    chat=Chat(
                        chat_id,
                        msg["type"],
                    ),
                    from_user=User(id=int(msg["from_id"][4:]), first_name=msg["from"], is_bot=False),
                    reply_to_message=None if "reply_to_message_id" not in msg else Message(
                        message_id=msg["reply_to_message_id"],
                        date=datetime.fromtimestamp(float(msg["date_unixtime"])),
                        chat=Chat(
                            chat_id,
                            msg["type"],
                        ),
                    ),
                    text=msg["text"],
                ),
            )
            # logger.info(obj)

            seg = jieba.cut_for_search(msg["text"])
            seg = [v for v in seg]

            mob_doc = {
                "from": obj.message.from_user.id,
                "chat": chat_id,
                "mid": msg["id"],
                "text": msg["text"],
                "date": obj.message.date,
                "json": obj.to_json(),
                "tokens": trim_tokens(seg),
            }
            asyncio.run(mob.history.insert_one(mob_doc))
        except:
            pass


    # asyncio.run(mob.database.)
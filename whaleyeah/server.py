import logging
import json

from os import PathLike

from telegram import Update
from telegram.ext import Application, ContextTypes
from telegram.ext import CommandHandler

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_markdown(
        "`Hello World!`"
    )

def serve_config(config: PathLike) -> None:
    with open(config, "r", encoding="utf-8") as f:
        config = json.load(f)

    logging.basicConfig(
        format="[%(levelname)s] %(asctime)s - %(name)s - %(message)s", level=logging.INFO
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logger = logging.getLogger(__name__)

    app = Application.builder().token(config["token"]).build()

    for plugin in config["plugins"]:
        logger.debug(f"Init plugin={plugin}.")
    # app.add_handler()

    app.add_handler(CommandHandler("start", start))

    logger.info(f"Listening {config["listen"]}")
    # app.run_polling(allowed_updates=Update.ALL_TYPES)

    def _get_url_path(url: str) -> str:
        url = url.split("://")[-1]
        return url.split("/", maxsplit=1)[-1]

    app.run_webhook(
        listen=config["listen"].split(":")[0],
        port=int(config["listen"].split(":")[1]),
        webhook_url=config["webhook"],
        url_path=_get_url_path(config["webhook"]),
        allowed_updates=Update.ALL_TYPES,
        secret_token=None if "secret" not in config else config["secret"],
    )

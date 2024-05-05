import logging
import json

from os import PathLike
from importlib import import_module

from telegram import Update
from telegram.ext import Application, ContextTypes
from telegram.ext import CommandHandler

from .iwaku import iwaku_history_handler, iwaku_inline_handler, iwaku_locate_handler, iwaku_plugins_copy
from .database import init_database


plugins_dict = {}

async def hello_world(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_markdown(
        "`Hello World!`"
    )

async def _helper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_markdown_v2(
        "Enabled Plugins:\n  \- `" + "`\n  \- `".join([k for k in plugins_dict]) + "`"
    )

def serve_config(config: PathLike) -> None:
    with open(config, "r", encoding="utf-8") as f:
        config = json.load(f)

    if "log_level" not in config: config["log_level"] = "info"

    logging.basicConfig(
        format="[%(levelname)s] %(asctime)s - %(name)s - %(message)s", level=logging.getLevelName(config["log_level"].upper()),
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logger = logging.getLogger(__name__)


    init_database(config["database"])

    app = Application.builder().token(config["token"]).build()
    app.add_handler(CommandHandler("start", hello_world))
    app.add_handler(CommandHandler("help", _helper))

    global plugins_dict
    plugins = config["plugins"]
    for (pname, pconf) in plugins.items():
        try:
            logger.info(f"Dynamically load plugin => {pname}...")
            plugin  = import_module(f"{__package__}.plugins.{pname}")
            handler = getattr(plugin, "get_handler")(pconf)

            # logger.info(f"Added handler '{handler}'.")
            app.add_handler(handler)
        except Exception as e:
            logger.warning(f"Error: '{e}'")
        else:
            plugins_dict[pname] = handler

    app.add_handler(iwaku_inline_handler())
    app.add_handler(iwaku_locate_handler())
    app.add_handler(iwaku_history_handler())
    iwaku_plugins_copy(plugins_dict)

    plugins_dict["iwaku"] = None
    
    
    for (jname, jconf) in config["jobs"].items():
        try:
            logger.info(f"Dynamically load job => {jname}...")
            
            kwargs = {}
            for (k, v) in jconf.items():
                if k=="type" or k=="settings": continue
                kwargs[k] = v
                
            logger.debug(f"kwargs: {kwargs}")

            
            plugin  = import_module(f"{__package__}.jobs.{jname}")
            handler = getattr(plugin, "get_handler")(jconf["settings"])

            app.job_queue.__getattribute__(f"run_{jconf["type"]}")(
                callback=handler,
                **kwargs,
            )
        except Exception as e:
            logger.warning(f"Error: '{e}'")

    # job_task = asyncio.create_task(app.job_queue.start())


    logger.info(f"Listening {config["listen"]}")
    app.run_webhook(
        listen=config["listen"].split(":")[0],
        port=int(config["listen"].split(":")[1]),
        webhook_url=config["webhook"],
        url_path=config["webhook"].split("://")[-1].split("/", maxsplit=1)[-1],
        allowed_updates=Update.ALL_TYPES,
        secret_token=None if "secret" not in config else config["secret"],
    )
    # app.run_polling(allowed_updates=Update.ALL_TYPES)
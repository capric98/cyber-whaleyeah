import asyncio
import logging

from motor.motor_asyncio import AsyncIOMotorClient
from motor.motor_asyncio import AsyncIOMotorDatabase, AsyncIOMotorCollection

class MobClass:
    def __init__(self) -> None:
        self._database = None
        self._history  = None
        self._tokens   = None
        self._GROUP_ID = None
    def __setattr__(self, name, value):
        self.__dict__[f"_{name}"] = value

    @property
    def database(self) -> AsyncIOMotorDatabase:
        return self._database
    @property
    def history(self) -> AsyncIOMotorCollection:
        return self._history
    @property
    def tokens(self) -> AsyncIOMotorCollection:
        return self._tokens
    @property
    def GROUP_ID(self) -> int:
        return self._GROUP_ID


mob = MobClass()
logger = logging.getLogger(__name__)

def init_database(db_config):
    global mob

    try:
        logging.getLogger("pymongo").setLevel(logging.WARNING)
        client = AsyncIOMotorClient(db_config["uri"], io_loop=asyncio.get_event_loop())
        mob.database = client.get_database(db_config["db_name"])
        mob.history  = mob.database.get_collection("history")
        mob.tokens   = mob.database.get_collection("tokens")

        mob.GROUP_ID = db_config["IWAKU_GROUP_ID"]
    except Exception as e:
        logger.warning(f"Error: '{e}'")
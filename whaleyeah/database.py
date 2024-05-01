import asyncio
import logging

from motor.motor_asyncio import AsyncIOMotorClient
from motor.motor_asyncio import AsyncIOMotorDatabase, AsyncIOMotorCollection

class MobClass:
    def __init__(self) -> None:
        self._database = None
        self._history  = None
        self._tokens   = None
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

mob = MobClass()
logger = logging.getLogger(__name__)

def init_database(db_config):
    global mob

    try:
        client = AsyncIOMotorClient(db_config["uri"], io_loop=asyncio.get_event_loop())
        mob.database = client.get_database(db_config["db_name"])
        mob.history  = mob.database.get_collection("history")
        mob.tokens   = mob.database.get_collection("tokens")
    except Exception as e:
        logger.warning(f"Error: '{e}'")
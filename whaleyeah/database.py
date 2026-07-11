import logging

from motor.motor_asyncio import AsyncIOMotorClient
from motor.motor_asyncio import AsyncIOMotorDatabase, AsyncIOMotorCollection


class MobClass:
    def __init__(self) -> None:
        self.database = None
        self.history = None
        self.tokens = None
        self.GROUP_ID = -1
        self.use_text_search = False

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

    @property
    def use_text_search(self) -> bool:
        return self._use_text_search


mob = MobClass()
logger = logging.getLogger(__name__)


def init_database(db_config: dict) -> None:
    # These values are configuration, so initialize them independently from
    # network/client setup. A database error must not reset authorization state.
    mob.GROUP_ID = db_config["IWAKU_GROUP_ID"]
    mob.use_text_search = db_config.get("use_text_search", False)

    try:
        logging.getLogger("pymongo").setLevel(logging.WARNING)
        # Motor binds to the running event loop lazily. Passing get_event_loop()
        # here fails before PTB starts its loop on Python 3.14.
        client = AsyncIOMotorClient(db_config["uri"])
        mob.database = client.get_database(db_config["db_name"])
        mob.history = mob.database.get_collection("history")
        mob.tokens = mob.database.get_collection("tokens")
    except Exception as exc:
        logger.exception("Failed to initialize database client: %s", exc)


async def init_database_indexes(_application=None) -> None:
    try:
        if mob.use_text_search:
            await mob.history.create_index(
                [("tokens", "text")],
                default_language="none",
                background=True,
            )
        else:
            await mob.history.create_index("tokens", background=True)
    except Exception as exc:
        logger.exception("Failed to initialize database indexes: %s", exc)

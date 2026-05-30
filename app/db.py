from contextlib import contextmanager
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

from .config import settings

pool = ConnectionPool(
    conninfo=settings.database_url,
    min_size=1,
    max_size=5,
    kwargs={"row_factory": dict_row},
    open=False,
)


def open_pool() -> None:
    pool.open()
    pool.wait()


def close_pool() -> None:
    pool.close()


@contextmanager
def conn():
    with pool.connection() as c:
        yield c

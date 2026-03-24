import asyncpg

from app.config import settings

# Module-level pool, set during app lifespan by init_pool().
# None until the app starts — get_db() asserts it is set before use.
pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global pool
    pool = await asyncpg.create_pool(
        settings.DATABASE_URL,
        min_size=2,
        max_size=10,
        # Required for Neon's connection pooler (PgBouncer in transaction mode).
        # PgBouncer does not support prepared statements; disabling the cache
        # prevents asyncpg from using them.
        statement_cache_size=0,
    )
    return pool


async def close_pool() -> None:
    if pool is not None:
        await pool.close()


async def get_db() -> asyncpg.Connection:
    assert pool is not None, "DB pool is not initialised — was init_pool() called?"
    async with pool.acquire() as conn:
        yield conn

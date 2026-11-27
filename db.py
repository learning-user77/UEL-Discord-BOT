import os
import asyncpg

pool: asyncpg.Pool | None = None


def _get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Add your Neon PostgreSQL connection string in Render Environment settings."
        )
    # Normalize postgres:// to postgresql:// for asyncpg compatibility
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


async def init_db() -> None:
    """Initialize the asyncpg connection pool and create tables if they do not exist."""
    global pool
    if pool is not None:
        return

    dsn = _get_database_url()
    ssl_arg = None
    if "sslmode=" not in dsn and "localhost" not in dsn and "127.0.0.1" not in dsn:
        ssl_arg = "require"

    # max_inactive_connection_lifetime=240 recycles idle connections before
    # Neon's 5-minute scale-to-zero cutoff to avoid stale-connection errors.
    pool = await asyncpg.create_pool(
        dsn=dsn,
        ssl=ssl_arg,
        min_size=1,
        max_size=5,
        command_timeout=30,
        max_inactive_connection_lifetime=240,
    )

    async with pool.acquire() as conn:
        # 1. Global Settings
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS global_config (
                guild_id BIGINT PRIMARY KEY,
                manager_role_id BIGINT,
                asst_role_id BIGINT,
                contract_channel_id BIGINT,
                free_agent_role_id BIGINT,
                window_open INTEGER DEFAULT 1,
                demand_limit INTEGER DEFAULT 3
            )
            """
        )

        # 2. Teams Table (scoped by guild_id for multi-server support)
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS teams (
                team_role_id BIGINT PRIMARY KEY,
                guild_id BIGINT NOT NULL DEFAULT 0,
                logo TEXT,
                roster_limit INTEGER DEFAULT 20,
                transaction_image TEXT
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_teams_guild_id ON teams (guild_id)"
        )

        # 3. Free Agents (composite key for multi-server isolation)
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS free_agents (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                region TEXT,
                position TEXT,
                description TEXT,
                timestamp TEXT,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )

        # 4. Player Stats (composite key for multi-server isolation)
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS player_stats (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                transfers INTEGER DEFAULT 0,
                demands INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )

        # Safe forward migrations for any older schemas
        await conn.execute(
            "ALTER TABLE global_config ADD COLUMN IF NOT EXISTS free_agent_role_id BIGINT"
        )
        await conn.execute(
            "ALTER TABLE global_config ADD COLUMN IF NOT EXISTS window_open INTEGER DEFAULT 1"
        )
        await conn.execute(
            "ALTER TABLE global_config ADD COLUMN IF NOT EXISTS demand_limit INTEGER DEFAULT 3"
        )
        await conn.execute(
            "ALTER TABLE teams ADD COLUMN IF NOT EXISTS guild_id BIGINT NOT NULL DEFAULT 0"
        )
        await conn.execute(
            "ALTER TABLE teams ADD COLUMN IF NOT EXISTS transaction_image TEXT"
        )


async def close_db() -> None:
    """Gracefully close the connection pool on bot shutdown."""
    global pool
    if pool is not None:
        await pool.close()
        pool = None


def _require_pool() -> asyncpg.Pool:
    if pool is None:
        raise RuntimeError("Database pool is not initialized. Call init_db() first.")
    return pool


# --- GLOBAL CONFIG ---

async def get_global_config(guild_id: int):
    row = await _require_pool().fetchrow(
        """
        SELECT guild_id, manager_role_id, asst_role_id, contract_channel_id,
               free_agent_role_id, window_open, demand_limit
        FROM global_config
        WHERE guild_id = $1
        """,
        guild_id,
    )
    return tuple(row) if row else None


async def upsert_global_config(
    guild_id: int,
    manager_role_id: int,
    asst_role_id: int,
    contract_channel_id: int,
    free_agent_role_id: int,
    window_open: int,
    demand_limit: int,
) -> None:
    await _require_pool().execute(
        """
        INSERT INTO global_config (
            guild_id, manager_role_id, asst_role_id, contract_channel_id,
            free_agent_role_id, window_open, demand_limit
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (guild_id) DO UPDATE SET
            manager_role_id = EXCLUDED.manager_role_id,
            asst_role_id = EXCLUDED.asst_role_id,
            contract_channel_id = EXCLUDED.contract_channel_id,
            free_agent_role_id = EXCLUDED.free_agent_role_id,
            window_open = EXCLUDED.window_open,
            demand_limit = EXCLUDED.demand_limit
        """,
        guild_id,
        manager_role_id,
        asst_role_id,
        contract_channel_id,
        free_agent_role_id,
        window_open,
        demand_limit,
    )


async def delete_global_config(guild_id: int) -> None:
    await _require_pool().execute(
        "DELETE FROM global_config WHERE guild_id = $1",
        guild_id,
    )


async def set_window_open(guild_id: int, status: int) -> None:
    await _require_pool().execute(
        "UPDATE global_config SET window_open = $1 WHERE guild_id = $2",
        status,
        guild_id,
    )


# --- TEAMS ---

async def get_team_data(role_id: int):
    row = await _require_pool().fetchrow(
        """
        SELECT team_role_id, logo, roster_limit, transaction_image
        FROM teams
        WHERE team_role_id = $1
        """,
        role_id,
    )
    return tuple(row) if row else None


async def get_all_teams(guild_id: int):
    rows = await _require_pool().fetch(
        """
        SELECT team_role_id, logo, roster_limit, transaction_image
        FROM teams
        WHERE guild_id = $1 OR guild_id = 0
        """,
        guild_id,
    )
    return [tuple(r) for r in rows]


async def upsert_team(
    team_role_id: int,
    guild_id: int,
    logo: str,
    roster_limit: int,
    transaction_image: str | None,
) -> None:
    await _require_pool().execute(
        """
        INSERT INTO teams (team_role_id, guild_id, logo, roster_limit, transaction_image)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (team_role_id) DO UPDATE SET
            guild_id = EXCLUDED.guild_id,
            logo = EXCLUDED.logo,
            roster_limit = EXCLUDED.roster_limit,
            transaction_image = EXCLUDED.transaction_image
        """,
        team_role_id,
        guild_id,
        logo,
        roster_limit,
        transaction_image,
    )


async def delete_team(team_role_id: int) -> None:
    await _require_pool().execute(
        "DELETE FROM teams WHERE team_role_id = $1",
        team_role_id,
    )


async def set_team_transaction_image(team_role_id: int, image_url: str | None) -> None:
    await _require_pool().execute(
        "UPDATE teams SET transaction_image = $1 WHERE team_role_id = $2",
        image_url,
        team_role_id,
    )


# --- FREE AGENTS ---

async def upsert_free_agent(
    guild_id: int,
    user_id: int,
    region: str,
    position: str,
    description: str,
    timestamp: str,
) -> None:
    await _require_pool().execute(
        """
        INSERT INTO free_agents (guild_id, user_id, region, position, description, timestamp)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (guild_id, user_id) DO UPDATE SET
            region = EXCLUDED.region,
            position = EXCLUDED.position,
            description = EXCLUDED.description,
            timestamp = EXCLUDED.timestamp
        """,
        guild_id,
        user_id,
        region,
        position,
        description,
        timestamp,
    )


async def delete_free_agent(guild_id: int, user_id: int) -> None:
    await _require_pool().execute(
        "DELETE FROM free_agents WHERE guild_id = $1 AND user_id = $2",
        guild_id,
        user_id,
    )


async def get_free_agents(guild_id: int):
    rows = await _require_pool().fetch(
        """
        SELECT user_id, region, position, description, timestamp
        FROM free_agents
        WHERE guild_id = $1
        ORDER BY timestamp DESC
        """,
        guild_id,
    )
    return [tuple(r) for r in rows]


# --- PLAYER STATS ---

async def get_player_stats(guild_id: int, user_id: int):
    p = _require_pool()
    row = await p.fetchrow(
        """
        SELECT user_id, transfers, demands
        FROM player_stats
        WHERE guild_id = $1 AND user_id = $2
        """,
        guild_id,
        user_id,
    )
    if not row:
        await p.execute(
            """
            INSERT INTO player_stats (guild_id, user_id, transfers, demands)
            VALUES ($1, $2, 0, 0)
            ON CONFLICT (guild_id, user_id) DO NOTHING
            """,
            guild_id,
            user_id,
        )
        return (user_id, 0, 0)
    return tuple(row)


async def update_stat(guild_id: int, user_id: int, stat_type: str, amount: int = 1) -> None:
    await get_player_stats(guild_id, user_id)
    if stat_type == "transfer":
        await _require_pool().execute(
            """
            UPDATE player_stats
            SET transfers = transfers + $1
            WHERE guild_id = $2 AND user_id = $3
            """,
            amount,
            guild_id,
            user_id,
        )
    elif stat_type == "demand":
        await _require_pool().execute(
            """
            UPDATE player_stats
            SET demands = demands + $1
            WHERE guild_id = $2 AND user_id = $3
            """,
            amount,
            guild_id,
            user_id,
        )


async def get_top_transfers(guild_id: int, limit: int = 15):
    rows = await _require_pool().fetch(
        """
        SELECT user_id, transfers
        FROM player_stats
        WHERE guild_id = $1
        ORDER BY transfers DESC
        LIMIT $2
        """,
        guild_id,
        limit,
    )
    return [tuple(r) for r in rows]
  

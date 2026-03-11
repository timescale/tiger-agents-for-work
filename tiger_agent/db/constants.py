import os

PG_MAX_POOL_SIZE: int = int(os.getenv("PG_MAX_POOL_SIZE", "10"))

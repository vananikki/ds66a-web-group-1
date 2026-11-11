import os
from pathlib import Path

import redis
from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_ROOT / ".env")

REDIS_URL = os.getenv("REDIS_URL")

if not REDIS_URL:
    raise RuntimeError("Chưa đặt biến môi trường REDIS_URL")

redis_client = redis.Redis.from_url(
    REDIS_URL,
    decode_responses=True,
)
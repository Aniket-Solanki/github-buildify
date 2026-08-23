"""
Centralized application configuration.

All environment variables, constants, and shared state are defined here
so that every module imports from a single source of truth.
"""

import ipaddress
import logging
import os
import platform
import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Optional

import redis
from dotenv import load_dotenv

# --- Environment & Logging ---
load_dotenv(dotenv_path=Path(__file__).parent.parent / '.env', override=True)

ENV = os.getenv("ENVIRONMENT", "development").lower()
LOG_LEVEL = logging.INFO if ENV == "production" else logging.DEBUG

logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
    handlers=[logging.StreamHandler()]
)

# Set Windows event loop policy if applicable
if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    logging.info({"message": "Set WindowsProactorEventLoopPolicy for Windows compatibility"})


# --- Application Constants ---
SESSION_COOKIE_NAME = "gitresume_session"
SESSION_EXPIRY_SECONDS = 604800  # 7 days
CACHE_TTL = 300  # Cache time-to-live in seconds
DDoS_WINDOW = 10  # Seconds for DDoS tracking
DDoS_MAX_REQUESTS = 50  # Max requests in DDoS window
CLOUDFLARE_ONLY = os.getenv("CLOUDFLARE_ONLY", "false").lower() == "true"

# Cloudflare IP ranges for middleware
CLOUDFLARE_IP_RANGES = [
    # IPv4
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    # IPv6
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32"
]
CLOUDFLARE_NETWORKS = [ipaddress.ip_network(cidr) for cidr in CLOUDFLARE_IP_RANGES]

# --- Global Caches (in-memory, for single-process use) ---
repository_cache = {}
request_counts = defaultdict(list)


# --- Redis Client ---
def get_redis_client() -> Optional[redis.Redis]:
    """Initialize and return a Redis client or None if connection fails."""
    try:
        use_ssl = os.getenv("REDIS_SSL", "false").lower() == "true"
        client = redis.Redis(
            host=os.getenv("REDIS_HOST"),
            port=int(os.getenv("REDIS_PORT")),
            decode_responses=True,
            username=os.getenv("REDIS_USERNAME"),
            password=os.getenv("REDIS_PASSWORD"),
            ssl=use_ssl,
            ssl_cert_reqs=None if use_ssl else None,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
            max_connections=20
        )
        client.ping()
        logging.info({"message": "Redis connection established successfully"})
        return client
    except Exception as e:
        logging.warning(
            {"message": "Redis connection failed, falling back to memory-based rate limiting", "error": str(e)})
        return None


redis_client = get_redis_client()

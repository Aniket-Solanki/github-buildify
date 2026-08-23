"""
Analytics module for tracking usage metrics in Redis.
"""

import asyncio
import logging
from typing import Dict, Any

from core.config import redis_client

logger = logging.getLogger(__name__)

# --- Analytics Counters ---
ANALYTICS_TOTAL_USERS_KEY = "analytics:total_users"
ANALYTICS_TOTAL_REPOS_KEY = "analytics:total_repos_analyzed"
ANALYTICS_TOTAL_REPO_SIZE_KEY = "analytics:total_repo_size_mb"
ANALYTICS_TOTAL_FILES_KEY = "analytics:total_files_analyzed"
ANALYTICS_MAX_REPO_SIZE_KEY = "analytics:max_repo_size_mb"
ANALYTICS_MAX_FILES_KEY = "analytics:max_files_in_repo"


async def increment_analytics_counter(key: str, unique_value: str = None):
    if not redis_client:
        return
    try:
        if unique_value:
            # Use a Redis set for unique values (e.g., users)
            await asyncio.to_thread(redis_client.sadd, key, unique_value)
        else:
            await asyncio.to_thread(redis_client.incr, key)
    except Exception as e:
        logger.error({"message": f"Failed to increment analytics counter {key}", "error": str(e)})


async def increment_repo_size_and_files(size_mb: float, file_count: int):
    if not redis_client:
        return
    try:
        await asyncio.to_thread(redis_client.incrbyfloat, ANALYTICS_TOTAL_REPO_SIZE_KEY, size_mb)
        await asyncio.to_thread(redis_client.incrby, ANALYTICS_TOTAL_FILES_KEY, file_count)
        
        # Track max repo size
        current_max_size = await asyncio.to_thread(redis_client.get, ANALYTICS_MAX_REPO_SIZE_KEY)
        if not current_max_size or float(size_mb) > float(current_max_size):
            await asyncio.to_thread(redis_client.set, ANALYTICS_MAX_REPO_SIZE_KEY, size_mb)
            
        # Track max file count
        current_max_files = await asyncio.to_thread(redis_client.get, ANALYTICS_MAX_FILES_KEY)
        if not current_max_files or int(file_count) > int(current_max_files):
            await asyncio.to_thread(redis_client.set, ANALYTICS_MAX_FILES_KEY, file_count)
    except Exception as e:
        logger.error({"message": "Failed to update analytics for repo size and files", "error": str(e)})


async def get_analytics() -> Dict[str, Any]:
    if not redis_client:
        return {
            "total_users": None, "total_repos_analyzed": None, "total_repo_size_mb": None, "total_files_analyzed": None,
            "avg_repo_size_mb": None, "avg_files_per_repo": None, "max_repo_size_mb": None, "max_files_in_repo": None
        }
        
    try:
        total_users = await asyncio.to_thread(redis_client.scard, ANALYTICS_TOTAL_USERS_KEY)
        total_repos = await asyncio.to_thread(redis_client.get, ANALYTICS_TOTAL_REPOS_KEY)
        total_repo_size = await asyncio.to_thread(redis_client.get, ANALYTICS_TOTAL_REPO_SIZE_KEY)
        total_files = await asyncio.to_thread(redis_client.get, ANALYTICS_TOTAL_FILES_KEY)
        max_repo_size = await asyncio.to_thread(redis_client.get, ANALYTICS_MAX_REPO_SIZE_KEY)
        max_files = await asyncio.to_thread(redis_client.get, ANALYTICS_MAX_FILES_KEY)
        
        total_repos_val = int(total_repos) if total_repos else 0
        total_repo_size_val = float(total_repo_size) if total_repo_size else 0.0
        total_files_val = int(total_files) if total_files else 0
        
        avg_repo_size = (total_repo_size_val / total_repos_val) if total_repos_val else 0.0
        avg_files = (total_files_val / total_repos_val) if total_repos_val else 0.0
        
        return {
            "total_users": total_users,
            "total_repos_analyzed": total_repos_val,
            "total_repo_size_mb": total_repo_size_val,
            "total_files_analyzed": total_files_val,
            "avg_repo_size_mb": avg_repo_size,
            "avg_files_per_repo": avg_files,
            "max_repo_size_mb": float(max_repo_size) if max_repo_size else 0.0,
            "max_files_in_repo": int(max_files) if max_files else 0
        }
    except Exception as e:
        logger.error({"message": "Failed to get analytics", "error": str(e)})
        return {
            "total_users": None, "total_repos_analyzed": None, "total_repo_size_mb": None, "total_files_analyzed": None,
            "avg_repo_size_mb": None, "avg_files_per_repo": None, "max_repo_size_mb": None, "max_files_in_repo": None
        }

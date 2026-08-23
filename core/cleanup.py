"""
Advanced background task to clean up temporary directories.
"""

import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path

from tools.utils import robust_rmtree
from core.config import repository_cache, CACHE_TTL

logger = logging.getLogger(__name__)

# UUID length is 36, typical session ID format
UUID_PATTERN = r'^[a-f0-9-]{36}$'
TEMP_DIR = Path(tempfile.gettempdir())
MAX_TEMP_AGE_HOURS = 24  # Clean up temp folders older than 24 hours


async def scan_and_clean_temp_dir():
    """Scans the system temp directory for orphaned GitResume clone folders."""
    logger.info({"message": "Starting temp directory scan"})
    count = 0
    try:
        current_time = time.time()
        # List all items in the temp dir
        for item in TEMP_DIR.iterdir():
            if item.is_dir() and len(item.name) >= 36:
                # Basic heuristic: look for dirs that look like they contain UUIDs
                # since we generate a UUID for session.local_path
                try:
                    stat = item.stat()
                    age_hours = (current_time - stat.st_mtime) / 3600
                    if age_hours > MAX_TEMP_AGE_HOURS:
                        logger.info({"message": f"Found orphaned temp dir, age {age_hours:.1f}h", "path": str(item)})
                        # Perform cleanup
                        await asyncio.to_thread(robust_rmtree, str(item))
                        count += 1
                except Exception as e:
                    logger.debug({"message": "Could not process temp dir item", "path": str(item), "error": str(e)})
    except Exception as e:
        logger.error({"message": "Failed during temp directory scan", "error": str(e)})
        
    if count > 0:
        logger.info({"message": "Temp directory scan complete", "removed_count": count})


async def periodic_cleanup():
    """Periodically clean up stale cache entries and temp files."""
    cleanup_interval = 300  # 5 minutes
    
    while True:
        try:
            await asyncio.sleep(cleanup_interval)
            current_time = time.time()
            
            # Clean up repository cache
            cutoff_time = current_time - CACHE_TTL
            old_keys = [k for k, (_, timestamp) in repository_cache.items() if timestamp < cutoff_time]
            for key in old_keys:
                repository_cache.pop(key, None)
            if old_keys:
                logger.info({"message": "Cleaned up repository cache", "removed_entries": len(old_keys)})
                
            # Scan for temp files (every few hours is best, but we'll do it every ~6 hours by checking intervals)
            # 6 hours = 360 minutes = 72 intervals of 5 minutes
            # Or just use a simple static counter
            if not hasattr(periodic_cleanup, 'counter'):
                periodic_cleanup.counter = 0
            
            periodic_cleanup.counter += 1
            if periodic_cleanup.counter >= 72:
                periodic_cleanup.counter = 0
                await scan_and_clean_temp_dir()
                
        except Exception as e:
            logger.error({"message": "Error in periodic cleanup", "error": str(e)})

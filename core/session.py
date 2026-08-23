"""
Session management: save, load, delete session data from Redis.
"""

import asyncio
import json
import logging
import re
from typing import Optional, Dict, Any

from pydantic import BaseModel, field_validator

from core.config import redis_client, SESSION_EXPIRY_SECONDS, SESSION_COOKIE_NAME

logger = logging.getLogger(__name__)


# --- Validators ---

def validate_github_url(url: str) -> bool:
    """Validate if the provided URL is a valid GitHub repository URL."""
    if not url or not isinstance(url, str) or re.search(r'[<>"\'\`]', url):
        return False
    pattern = r'^https://github\.com/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+/?$'
    return bool(re.match(pattern, url))


def validate_github_token(token: str) -> bool:
    """Validate if the provided GitHub token is in a valid format."""
    if not token or not isinstance(token, str):
        return False
    pattern = r'^[a-zA-Z0-9_]+$'
    return bool(re.match(pattern, token)) and len(token) >= 20


def sanitize_input(text: str, max_length: int = 1000) -> str:
    """Sanitize input string by removing dangerous characters and enforcing length limit."""
    if not text or not isinstance(text, str):
        return ""
    return re.sub(r'[<>"\'\`]', '', text.strip())[:max_length]


# --- Session Model ---

class GitHubSessionData(BaseModel):
    """Pydantic model for session data."""
    user_id: str
    access_token: Optional[str] = None
    github_token: Optional[str] = None
    redirect_url: Optional[str] = None
    session_id: str
    created_at: str
    repo_url: Optional[str] = None
    local_path: Optional[str] = None
    job_description: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    status: Optional[str] = None

    @field_validator('repo_url')
    def validate_repo_url(cls, v):
        if v and not validate_github_url(v):
            raise ValueError('Invalid GitHub repository URL')
        return v

    @field_validator('github_token')
    def validate_github_token_field(cls, v):
        if v and not validate_github_token(v):
            raise ValueError('Invalid GitHub token format')
        return v

    @field_validator('job_description')
    def validate_job_description(cls, v):
        if v:
            return sanitize_input(v, max_length=5000)
        return v

    model_config = {"arbitrary_types_allowed": True}


# --- Session CRUD ---

async def save_session(session: GitHubSessionData):
    """Save session data to Redis with expiry."""
    if not redis_client:
        raise RuntimeError("Redis is required for session management.")
    await asyncio.to_thread(
        redis_client.setex,
        f"session:{session.session_id}",
        SESSION_EXPIRY_SECONDS,
        session.model_dump_json()
    )


async def get_session(session_id: str) -> Optional[GitHubSessionData]:
    """Retrieve session data from Redis."""
    if not redis_client:
        return None
    data = await asyncio.to_thread(redis_client.get, f"session:{session_id}")
    if not data:
        return None
    try:
        return GitHubSessionData(**json.loads(data))
    except Exception as e:
        logger.warning({"message": "Invalid session data in Redis", "session_id": session_id, "error": str(e)})
        return None


async def delete_session(session_id: str):
    """Delete session data from Redis."""
    if redis_client:
        await asyncio.to_thread(redis_client.delete, f"session:{session_id}")


def set_session_cookie(response, session_id: str):
    """Set session cookie on the response."""
    from core.config import ENV
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        max_age=SESSION_EXPIRY_SECONDS,
        httponly=True,
        secure=ENV == "production",
        samesite="Lax"
    )


# --- Generation Data CRUD ---

async def get_generation_data(session_id: str, generation_id: str) -> Optional[dict]:
    """Retrieve generation data from Redis."""
    if not redis_client:
        return None
    data = await asyncio.to_thread(redis_client.get, f"generation:{session_id}:{generation_id}")
    if not data:
        return None
    try:
        return json.loads(data)
    except Exception as e:
        logger.warning(
            {"message": "Invalid generation data in Redis", "session_id": session_id, "generation_id": generation_id,
             "error": str(e)})
        return None


async def save_generation_data(session_id: str, generation_id: str, data: dict, expiry: int = 3600):
    """Save generation data to Redis with expiry."""
    if not redis_client:
        raise RuntimeError("Redis is required for generation management.")
    await asyncio.to_thread(
        redis_client.setex,
        f"generation:{session_id}:{generation_id}",
        expiry,
        json.dumps(data)
    )

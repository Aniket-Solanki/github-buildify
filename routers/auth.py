"""
Authentication routes for GitHub OAuth.
"""

import logging
import os
import urllib.parse
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from github import Github, GithubException

from core.session import GitHubSessionData, save_session, get_session, delete_session, set_session_cookie
from core.config import SESSION_COOKIE_NAME
from core.analytics import increment_analytics_counter, ANALYTICS_TOTAL_USERS_KEY

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/login")
async def login(request: Request):
    """Initiate GitHub OAuth login flow."""
    session_id = str(uuid.uuid4())
    state = str(uuid.uuid4())
    request.session["session_id"] = session_id
    request.session["oauth_state"] = state

    session = GitHubSessionData(
        user_id=str(uuid.uuid4()),
        session_id=session_id,
        created_at=datetime.now(timezone.utc).isoformat()
    )
    await save_session(session)
    await increment_analytics_counter(ANALYTICS_TOTAL_USERS_KEY, session_id)
    logger.info({"message": "Created new session for login", "session_id": session_id})

    github_client_id = os.getenv("GITHUB_CLIENT_ID")
    if not github_client_id:
        logger.error({"message": "GitHub Client ID not configured"})
        raise HTTPException(status_code=500, detail="Authentication service temporarily unavailable")

    callback_url = os.getenv("CALLBACK_URL")
    if not callback_url or "localhost" in callback_url and "vercel" in str(request.url):
        callback_url = str(request.url_for('callback'))
        from core.config import ENV
        if ENV == "production" and callback_url.startswith("http://"):
            callback_url = callback_url.replace("http://", "https://", 1)

    params = {
        "client_id": github_client_id,
        "redirect_uri": callback_url,
        "scope": "repo user",
        "state": state,
        "response_type": "code"
    }

    github_auth_url = f"https://github.com/login/oauth/authorize?{urllib.parse.urlencode(params)}"
    logger.info({"message": "Redirecting to GitHub OAuth", "session_id": session_id})
    response = RedirectResponse(github_auth_url)
    set_session_cookie(response, session_id)
    return response


@router.get("/callback")
async def callback(request: Request, code: str = None, state: str = None, error: str = None):
    """Handle GitHub OAuth callback."""
    logger.info({"message": "OAuth callback received", "query_params": dict(request.query_params)})

    if error:
        logger.error({"message": "OAuth error from GitHub", "error": error})
        return RedirectResponse(url="/?error=oauth_failed", status_code=302)

    if not code or not state:
        logger.error({"message": "Missing required OAuth parameters", "code": bool(code), "state": bool(state)})
        return RedirectResponse(url="/?error=missing_params", status_code=302)

    session_state = request.session.get("oauth_state")
    if not session_state or state != session_state:
        logger.error({"message": "Invalid OAuth state", "provided_state": state, "expected_state": session_state})
        return RedirectResponse(url="/?error=invalid_state", status_code=302)

    session_id = request.session.get("session_id")
    session = await get_session(session_id)
    if not session_id or not session:
        logger.error({"message": "Invalid session during OAuth callback", "session_id": session_id})
        return RedirectResponse(url="/?error=invalid_session", status_code=302)

    github_client_id = os.getenv("GITHUB_CLIENT_ID")
    github_client_secret = os.getenv("GITHUB_CLIENT_SECRET")
    if not github_client_id or not github_client_secret:
        logger.error({"message": "GitHub OAuth configuration incomplete"})
        return RedirectResponse(url="/?error=config_error", status_code=302)

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                "https://github.com/login/oauth/access_token",
                data={
                    "client_id": github_client_id,
                    "client_secret": github_client_secret,
                    "code": code,
                    "redirect_uri": os.getenv("CALLBACK_URL")
                },
                headers={"Accept": "application/json"}
            )
            response.raise_for_status()
            data = response.json()
            access_token = data.get("access_token")
            if not access_token:
                logger.error({"message": "No access token in OAuth response", "response": data})
                raise HTTPException(status_code=400, detail="Authentication failed")

            g = Github(access_token)
            user = g.get_user()
            session.access_token = access_token
            session.user_id = str(user.id)
            request.session["github_user"] = user.login
            request.session["authenticated"] = True
            await save_session(session)
            logger.info({"message": "User authenticated successfully", "session_id": session_id, "user": user.login})

            request.session.pop("oauth_state", None)
            response = RedirectResponse(url="/", status_code=302)
            set_session_cookie(response, session_id)
            return response
        except httpx.HTTPStatusError as e:
            logger.error(
                {"message": "OAuth token exchange failed", "status_code": e.response.status_code, "error": str(e),
                 "response_text": e.response.text})
            return RedirectResponse(url="/?error=token_exchange_failed", status_code=302)
        except GithubException as e:
            logger.error({"message": "GitHub user validation failed", "error": str(e)})
            return RedirectResponse(url="/?error=user_validation_failed", status_code=302)
        except Exception as e:
            logger.error({"message": "Unexpected OAuth error", "error": str(e)})
            return RedirectResponse(url="/?error=unexpected_error", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    """Log out the user and clear session."""
    session_id = request.session.get("session_id")
    if session_id:
        session = await get_session(session_id)
        if session and session.local_path:
            import asyncio
            from tools.utils import robust_rmtree
            try:
                await asyncio.to_thread(robust_rmtree, session.local_path)
            except Exception as e:
                logger.warning({"message": "Failed to clean up session files", "error": str(e)})
        await delete_session(session_id)
        logger.info({"message": "User logged out successfully", "session_id": session_id})

    request.session.clear()
    response = RedirectResponse(url="/")
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response

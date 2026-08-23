"""
FastAPI application to generate professional resumes from GitHub repositories using AI.
Optimized for production with modular design, robust logging, and efficient session management.
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

import ipaddress
from api_analytics.fastapi import Analytics
from fastapi import FastAPI, Request
from fastapi.responses import Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from github import Github, GithubException

from core.config import (
    ENV, LOG_LEVEL, SESSION_COOKIE_NAME, SESSION_EXPIRY_SECONDS, CACHE_TTL,
    DDoS_WINDOW, DDoS_MAX_REQUESTS, CLOUDFLARE_ONLY, CLOUDFLARE_NETWORKS,
    repository_cache, request_counts, redis_client
)
from core.analytics import get_analytics
from core.session import GitHubSessionData

# Import Routers
from routers.auth import router as auth_router
from routers.websocket import router as websocket_router
from routers.web import router as web_router

# Set up logging and templates
logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="templates")
static_dir = Path("static")
static_dir.mkdir(exist_ok=True)


async def validate_repository_access(repo_url: str, github_token: str = None) -> Dict[str, Any]:
    """Validate access to a GitHub repository."""
    if not repo_url.startswith("https://github.com/"):
        return {
            "success": False, "is_public": False,
            "error_message": "Invalid GitHub repository URL. Please use format: https://github.com/owner/repo",
            "error_code": "invalid_url", "owner": None, "repo_name": None
        }

    path_part = repo_url.replace("https://github.com/", "").strip("/")
    segments = path_part.split("/")
    if len(segments) < 2:
        return {
            "success": False, "is_public": False,
            "error_message": "Invalid repository path. Please provide both owner and repository name",
            "error_code": "invalid_path", "owner": None, "repo_name": None
        }

    owner, repo_name = segments[0], segments[1]
    repo_full_name = f"{owner}/{repo_name}"

    try:
        github_client = Github(github_token) if github_token else Github()
        repo_obj = github_client.get_repo(repo_full_name)
        logger.info({"message": "Successfully validated repository access", "repo": repo_url, "is_public": not repo_obj.private})
        return {
            "success": True, "is_public": not repo_obj.private, "error_message": None, "error_code": None,
            "owner": owner, "repo_name": repo_name
        }
    except GithubException as e:
        error_message = f"Failed to access repository: {str(e)}"
        if e.status == 404:
            error_message = "Repository not found or requires authentication. Please verify the URL and access permissions."
            if not github_token:
                error_message = "Repository is private or does not exist. For private repositories, a GitHub token is required."
        elif e.status == 403:
            error_message = "Access denied. You don't have permission to view this repository."
        elif e.status == 401:
            error_message = "Authentication failed. Please provide a valid GitHub token."
        
        return {
            "success": False, "is_public": False, "error_message": error_message, "error_code": getattr(e, 'status', 'unknown'),
            "owner": owner, "repo_name": repo_name
        }
    except Exception as e:
        return {
            "success": False, "is_public": False, "error_message": f"An unexpected error occurred: {str(e)}", "error_code": "unexpected_error",
            "owner": owner, "repo_name": repo_name
        }


async def get_cached_repository_validation(repo_url: str, github_token: str = None) -> Dict[str, Any]:
    """Retrieve or validate repository access, caching results."""
    cache_key = f"{repo_url}_{hash(github_token) if github_token else 'public'}"
    current_time = time.time()

    if cache_key in repository_cache:
        cached_result, timestamp = repository_cache[cache_key]
        if current_time - timestamp < CACHE_TTL:
            return cached_result

    result = await validate_repository_access(repo_url, github_token)
    repository_cache[cache_key] = (result, current_time)
    
    # Clean up stale cache entries
    if len(repository_cache) > 1000:
        cutoff_time = current_time - CACHE_TTL
        stale_keys = [k for k, (_, timestamp) in repository_cache.items() if timestamp < cutoff_time]
        for k in stale_keys:
            repository_cache.pop(k, None)
            
    return result


async def enforce_private_repo_auth(validation_result: Dict[str, Any], session_data: Optional[GitHubSessionData],
                                    github_token: Optional[str]) -> tuple[bool, Optional[str]]:
    """Ensure private repositories require authentication."""
    if not validation_result["success"]:
        return False, validation_result["error_message"]
    if not validation_result["is_public"] and not (session_data and (session_data.access_token or github_token)):
        return False, "Authentication required to access private repository. Please log in with GitHub."
    return True, None


from core.cleanup import periodic_cleanup


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle, including startup and shutdown."""
    logger.info({"message": "Application starting up"})

    if redis_client:
        try:
            ping_result = await asyncio.to_thread(redis_client.ping)
            logger.info({"message": "Redis connection validated on startup", "result": ping_result})
        except Exception as e:
            logger.warning({"message": "Redis validation failed on startup", "error": str(e)})

    cleanup_task = asyncio.create_task(periodic_cleanup())
    logger.info({"message": "Started periodic cleanup task"})

    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        logger.info({"message": "Application shutdown complete"})


# Initialize FastAPI app
app = FastAPI(
    title="GitHub to Resume Generator",
    description="Generate professional resumes from GitHub repositories using AI",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if ENV != "production" else None,
    redoc_url="/redoc" if ENV != "production" else None
)


def rate_limit_key_func(request: Request) -> str:
    """Generate rate limit key based on client IP."""
    return (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or
        request.headers.get("X-Real-IP") or
        request.client.host if request.client else "unknown"
    )

limiter = Limiter(
    key_func=rate_limit_key_func,
    default_limits=["10/hour", "2/minute"],
    storage_uri=(
        f"rediss://{os.getenv('REDIS_USERNAME', 'default')}:{os.getenv('REDIS_PASSWORD')}@{os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT')}"
        if redis_client and os.getenv("REDIS_SSL", "false").lower() == "true"
        else f"redis://{os.getenv('REDIS_USERNAME', 'default')}:{os.getenv('REDIS_PASSWORD')}@{os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT')}"
        if redis_client else None
    )
)
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
async def custom_rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Render a user-friendly rate limit error page."""
    return templates.TemplateResponse(
        "ratelimit.jinja",
        {"request": request, "error_code": 429,
         "error_message": str(exc.detail) if hasattr(exc, 'detail') else str(exc)},
        status_code=429
    )

import os as _os
if _os.getenv("API_ANALYTICS_KEY"):
    app.add_middleware(Analytics, api_key=_os.getenv("API_ANALYTICS_KEY"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    max_age=3600
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(SessionMiddleware, secret_key=_os.getenv("SESSION_SECRET_KEY"), session_cookie=SESSION_COOKIE_NAME,
                   max_age=SESSION_EXPIRY_SECONDS, same_site="lax", https_only=ENV == "production")

app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.middleware("http")
async def force_https_and_security_headers(request: Request, call_next):
    if "x-forwarded-proto" in request.headers:
        request.scope["scheme"] = request.headers["x-forwarded-proto"]
    if request.url.path == "/health":
        return await call_next(request)
    try:
        response = await call_next(request)
        response.headers.update({
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
            "Referrer-Policy": "strict-origin-when-cross-origin"
        })
        if ENV == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response(f"Internal Error: {str(e)}", status_code=500)

@app.middleware("http")
async def validate_requests(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    if not hasattr(request, 'method') or not request.method:
        return Response("Bad Request: Missing HTTP Method in Vercel Proxy", status_code=400)
    if request.url.path and any(
            suspicious in request.url.path.lower() for suspicious in ['../', '.env', 'config', 'admin']):
        return Response("Not Found", status_code=404)
    return await call_next(request)

@app.middleware("http")
async def ddos_protection_middleware(request: Request, call_next):
    client_ip = rate_limit_key_func(request)
    current_time = time.time()
    request_counts[client_ip] = [t for t in request_counts[client_ip] if current_time - t < DDoS_WINDOW]
    request_counts[client_ip].append(current_time)
    if len(request_counts[client_ip]) > DDoS_MAX_REQUESTS:
        return Response("Too many requests. Please try again later.", status_code=429)
    return await call_next(request)

if CLOUDFLARE_ONLY:
    @app.middleware("http")
    async def cloudflare_only_middleware(request: Request, call_next):
        client_ip = rate_limit_key_func(request)
        try:
            ip_obj = ipaddress.ip_address(client_ip)
            if not any(ip_obj in net for net in CLOUDFLARE_NETWORKS):
                return Response("Access denied. Please use the public site.", status_code=403)
        except Exception:
            return Response("Access denied.", status_code=403)
        return await call_next(request)

@app.get("/health")
async def health_check():
    """Return application health status."""
    active_sessions = 0
    if redis_client:
        try:
            cursor = 0
            session_pattern = "session:*"
            while True:
                cursor, keys = await asyncio.to_thread(redis_client.scan, cursor, match=session_pattern, count=100)
                active_sessions += len(keys)
                if cursor == 0:
                    break
        except Exception as e:
            logger.error({"message": "Failed to count active sessions in Redis", "error": str(e)})

    analytics = await get_analytics()
    return {
        "status": "healthy", "environment": ENV, "redis_connected": redis_client is not None,
        "active_sessions": active_sessions, "timestamp": datetime.now(timezone.utc).isoformat(),
        **analytics
    }

@app.get("/favicon.ico")
async def favicon(): return FileResponse(static_dir / "favicon-32x32.png")
@app.get("/favicon-16x16.png")
async def favicon_16(): return FileResponse(static_dir / "favicon-16x16.png")
@app.get("/favicon-32x32.png")
async def favicon_32(): return FileResponse(static_dir / "favicon-32x32.png")
@app.get("/apple-touch-icon.png")
async def apple_touch_icon(): return FileResponse(static_dir / "apple-touch-icon.png")


# --- Register Routers ---
app.include_router(auth_router)
app.include_router(websocket_router)
app.include_router(web_router)


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    error_context = {
        "request": request, "error_code": exc.status_code,
        "error_title": exc.detail if hasattr(exc, 'detail') and exc.detail else "Error",
        "error_message": str(exc.detail) if hasattr(exc, 'detail') and exc.detail else "An error occurred."
    }
    if exc.status_code == 404:
        return templates.TemplateResponse("404.jinja", error_context, status_code=404)
    elif exc.status_code == 429:
        return templates.TemplateResponse("ratelimit.jinja", error_context, status_code=429)
    return templates.TemplateResponse("500.jinja", error_context, status_code=exc.status_code)

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    error_context = {
        "request": request, "error_code": 500, "error_title": "Internal Server Error",
        "error_message": str(exc) or "An unexpected error occurred."
    }
    return templates.TemplateResponse("500.jinja", error_context, status_code=500)

@app.route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def fallback_route(request: Request, full_path: str):
    return templates.TemplateResponse("404.jinja", {"request": request}, status_code=404)

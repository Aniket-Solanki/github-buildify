"""
Web routes for standard pages and dynamic GitHub URLs.
"""

import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from core.analytics import increment_analytics_counter, ANALYTICS_TOTAL_USERS_KEY
from core.session import GitHubSessionData, get_session, save_session, set_session_cookie

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def build_index_context(request: Request, session_data: Optional[GitHubSessionData] = None, repo_url: str = "",
                        job_description: str = "", error: Optional[str] = None, path: str = "/") -> Dict[str, Any]:
    """Build context for rendering the index template."""
    is_authenticated = bool(session_data and session_data.access_token)
    return {
        "request": request,
        "repo_url": repo_url,
        "job_description_hidden": job_description,
        "job_description": job_description,
        "loading": False,
        "streaming": False,
        "result": None,
        "error": error,
        "pre_filled": bool(repo_url),
        "is_authenticated": is_authenticated,
        "github_token": session_data.github_token if session_data else None,
        "is_public": False,
        "current_path": path,
        "session_id": session_data.session_id if session_data else None
    }


def sanitize_input(text: str, max_length: int = 1000) -> str:
    """Sanitize input string by removing dangerous characters and enforcing length limit."""
    if not text or not isinstance(text, str):
        return ""
    return re.sub(r'[<>"\'\`]', '', text.strip())[:max_length]


def validate_github_token(token: str) -> bool:
    """Validate if the provided GitHub token is in a valid format."""
    if not token or not isinstance(token, str):
        return False
    pattern = r'^[a-zA-Z0-9_]+$'
    return bool(re.match(pattern, token)) and len(token) >= 20


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Render the home page."""
    session_id = request.session.get("session_id")
    session_data = await get_session(session_id) if session_id else None
    return templates.TemplateResponse("index.jinja", build_index_context(request, session_data))


async def dynamic_github_route_post(request: Request, path: str, repo_url: str, job_description_hidden: str, github_token: Optional[str]):
    """Handle POST requests for dynamic GitHub repository URLs."""
    from app import get_cached_repository_validation, enforce_private_repo_auth
    
    repo_url = sanitize_input(repo_url, 200)
    job_description = sanitize_input(job_description_hidden, 5000)
    github_token = sanitize_input(github_token, 100) if github_token and validate_github_token(github_token) else None

    session_id = request.session.get("session_id")
    session_data = await get_session(session_id) if session_id else None

    validation_result = await get_cached_repository_validation(repo_url, github_token or (
        session_data.access_token if session_data else None))
    is_allowed, error_msg = await enforce_private_repo_auth(validation_result, session_data, github_token)
    
    if not is_allowed:
        return templates.TemplateResponse("index.jinja", build_index_context(
            request=request,
            session_data=session_data,
            repo_url=repo_url,
            job_description=job_description,
            error=error_msg,
            path=f"/{path}"
        ))

    if not session_id or not session_data:
        session_id = str(uuid.uuid4())
        request.session["session_id"] = session_id
        session_data = GitHubSessionData(
            user_id=session_id,
            session_id=session_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            github_token=github_token,
            access_token=None,
            repo_url=repo_url,
            job_description=job_description.strip() or None,
            status="pending"
        )
        await increment_analytics_counter(ANALYTICS_TOTAL_USERS_KEY, session_id)
    else:
        session_data.repo_url = repo_url
        session_data.job_description = job_description.strip() or None
        if github_token:
            session_data.github_token = github_token
        session_data.created_at = datetime.now(timezone.utc).isoformat()
        session_data.status = "pending"
        session_data.result = None
        session_data.local_path = None

    dynamic_path_segment = path or "/".join(repo_url.replace("https://github.com/", "").split("/")[:2])
    session_data.redirect_url = f"/{dynamic_path_segment}"
    await save_session(session_data)

    logger.info({"message": "Resume generation session initiated", "session_id": session_id,
                 "is_authenticated": bool(session_data.access_token)})
                 
    response = templates.TemplateResponse("index.jinja", {
        **build_index_context(request, session_data, repo_url, job_description, path=f"/{dynamic_path_segment}"),
        "streaming": True,
        "is_public": validation_result.get("is_public", False)
    })
    set_session_cookie(response, session_id)
    return response


@router.post("/", response_class=HTMLResponse)
async def generate_resume(request: Request, repo_url: str = Form(...), job_description_hidden: str = Form(""),
                          github_token: Optional[str] = Form(None)):
    """Handle resume generation POST requests from home page."""
    return await dynamic_github_route_post(request, "", repo_url, job_description_hidden, github_token)


@router.get("/{path:path}", response_class=HTMLResponse)
async def dynamic_github_route(request: Request, path: str):
    """Handle dynamic GitHub repository URL routes."""
    from app import get_cached_repository_validation
    
    session_id = request.session.get("session_id")
    session_data = await get_session(session_id) if session_id else None
    
    if path == "export/pdf":
        if not session_data or not session_data.result:
            raise HTTPException(status_code=404, detail="No resume generated in this session")
            
        from tools.pdf_export import generate_resume_pdf
        from fastapi.responses import StreamingResponse
        import urllib.parse
        
        pdf_buffer = generate_resume_pdf(session_data.result)
        filename = f"{session_data.repo_url.split('/')[-1]}_resume.pdf" if session_data.repo_url else "resume.pdf"
        
        return StreamingResponse(
            pdf_buffer, 
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename*=utf-8''{urllib.parse.quote(filename)}"}
        )
        
    skip_paths = {
        "", "health", "favicon.ico", "favicon-16x16.png", "favicon-32x32.png",
        "apple-touch-icon.png", "static", "ws", "login", "callback", "logout",
        "docs", "redoc", "openapi.json"
    }

    if path in skip_paths:
        if path == "":
            return await home(request)
        logger.warning(
            {"message": "Access attempt to restricted path", "path": path})
        raise HTTPException(status_code=404, detail="Not found")

    segments = [segment for segment in path.split('/') if segment]
    if len(segments) < 2:
        logger.info({"message": "Invalid GitHub URL format", "path": path})
        domain = request.headers.get("host") or request.url.hostname or "this-site"
        return templates.TemplateResponse("index.jinja", build_index_context(
            request=request,
            repo_url="",
            job_description="",
            error=f"Invalid GitHub URL format. Expected format: {domain}/username/repository",
            path=f"/{path}"
        ))

    username, repo = sanitize_input(segments[0], 100), sanitize_input(segments[1], 100)
    if not username or not repo or not re.match(r'^[a-zA-Z0-9_.-]+$', username) or not re.match(r'^[a-zA-Z0-9_.-]+$',
                                                                                                repo):
        logger.warning({"message": "Invalid username or repository name", "username": username, "repo": repo})
        return templates.TemplateResponse("index.jinja", build_index_context(
            request=request,
            repo_url="",
            job_description="",
            error="Invalid repository name format",
            path=f"/{path}"
        ))

    github_url = f"https://github.com/{username}/{repo}"
    validation_result = await get_cached_repository_validation(github_url,
                                                               session_data.access_token if session_data else None)
    if not validation_result["success"]:
        return templates.TemplateResponse("index.jinja", build_index_context(
            request=request,
            repo_url=github_url,
            job_description="",
            error=validation_result["error_message"],
            path=f"/{path}"
        ))

    return templates.TemplateResponse("index.jinja", build_index_context(
        request=request,
        repo_url=github_url,
        job_description="",
        error=None,
        path=f"/{path}"
    ))


@router.post("/{path:path}", response_class=HTMLResponse)
async def dynamic_github_route_post_path(request: Request, path: str, repo_url: str = Form(...),
                                    job_description_hidden: str = Form(""), github_token: Optional[str] = Form(None)):
    """Handle POST requests for dynamic GitHub repository URLs."""
    return await dynamic_github_route_post(request, path, repo_url, job_description_hidden, github_token)

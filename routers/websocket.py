"""
WebSocket routes for real-time streaming of generation process.
"""

import asyncio
import json
import logging
import os
import re
from typing import Dict, Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from starlette.websockets import WebSocketState

from core.analytics import increment_analytics_counter, increment_repo_size_and_files, ANALYTICS_TOTAL_REPOS_KEY
from core.session import GitHubSessionData, get_session, save_session, get_generation_data, save_generation_data

from tools.git_operations import clone_repo_tool
from tools.gitingest import gitingest_tool
from tools.create_resume import create_resume_tool
from tools.utils import robust_rmtree

logger = logging.getLogger(__name__)

router = APIRouter()


async def process_resume_generation(websocket: WebSocket, session_data: GitHubSessionData, generation_id: str):
    """Process resume generation with repository cloning, analysis, and AI generation."""
    from app import get_cached_repository_validation  # import from app temporarily until moved
    
    repo_url = session_data.repo_url
    job_description = session_data.job_description
    github_token = session_data.github_token
    oauth_token = session_data.access_token
    path = session_data.redirect_url or "/"

    token_to_use = oauth_token or github_token or os.getenv("GITHUB_TOKEN")
    validation_result = await get_cached_repository_validation(repo_url, token_to_use)
    if not validation_result["success"]:
        error_message = validation_result["error_message"]
        logger.warning({"message": "Repository access denied", "repo": repo_url, "error": error_message, "session_id": session_data.session_id})
        await websocket.send_text(json.dumps({
            "type": "error",
            "content": f"Repository access error: {error_message}",
            "generation_id": generation_id
        }))
        await save_generation_data(session_data.session_id, generation_id, {"status": "error", "error": error_message})
        return False

    await websocket.send_text(json.dumps({"type": "status", "content": "📥 Cloning repository...", "generation_id": generation_id}))
    await save_generation_data(session_data.session_id, generation_id, {"status": "cloning"})

    owner = validation_result["owner"]
    repo_name = validation_result["repo_name"]

    clone_result = await clone_repo_tool(
        repo_url=repo_url,
        github_token=token_to_use,
        websocket=websocket,
        generation_id=generation_id,
        session_id=session_data.session_id
    )

    if clone_result and clone_result.get("success"):
        session_data.local_path = clone_result["local_path"]
        await save_session(session_data)
        
    if not clone_result or not clone_result["success"]:
        error_msg = clone_result.get("error", "Unknown cloning error") if clone_result else "Failed to clone repository"
        logger.error({"message": "Repository cloning failed", "repo": repo_url, "error": error_msg, "session_id": session_data.session_id})
        await websocket.send_text(json.dumps({"type": "error", "content": f"Failed to clone repository: {error_msg}", "generation_id": generation_id}))
        await save_generation_data(session_data.session_id, generation_id, {"status": "error", "error": error_msg})
        return False

    await websocket.send_text(json.dumps({"type": "status", "content": "📊 Analyzing repository structure...", "generation_id": generation_id}))
    await save_generation_data(session_data.session_id, generation_id, {"status": "analyzing"})

    ingest_result = await gitingest_tool(clone_result['local_path'])
    if not ingest_result["success"]:
        error_msg = ingest_result.get("error", "Unknown analysis error")
        logger.error({"message": "Repository analysis failed", "repo": repo_url, "error": error_msg, "session_id": session_data.session_id})
        await websocket.send_text(json.dumps({"type": "error", "content": f"Failed to analyze repository: {error_msg}", "generation_id": generation_id}))
        await save_generation_data(session_data.session_id, generation_id, {"status": "error", "error": error_msg})
        return False

    # --- Analytics: increment repos analyzed, repo size, and file count ---
    await increment_analytics_counter(ANALYTICS_TOTAL_REPOS_KEY)
    repo_size_mb = clone_result.get("repo_size_mb", 0)
    file_count = clone_result.get("file_count", 0)
    await increment_repo_size_and_files(repo_size_mb, file_count)

    await websocket.send_text(json.dumps({"type": "status", "content": "📝 Generating resume content with AI...", "generation_id": generation_id}))
    await save_generation_data(session_data.session_id, generation_id, {"status": "generating"})

    resume_result = await create_resume_tool(
        gitingest_summary=ingest_result['summary'],
        gitingest_tree=ingest_result['tree'],
        gitingest_content=ingest_result['content'],
        project_name=clone_result['repo_name'],
        job_description=job_description,
        websocket=websocket,
        generation_id=generation_id
    )

    if not resume_result["success"]:
        error_msg = resume_result.get("error", "Unknown generation error")
        logger.error({"message": "Resume generation failed", "repo": repo_url, "error": error_msg, "session_id": session_data.session_id})
        await websocket.send_text(json.dumps({"type": "error", "content": f"Failed to generate resume: {error_msg}", "generation_id": generation_id}))
        await save_generation_data(session_data.session_id, generation_id, {"status": "error", "error": error_msg})
        return False

    final_result = {
        "project_title": resume_result['project_title'],
        "tech_stack": resume_result['tech_stack'],
        "bullet_points": [str(bp) for bp in resume_result['bullet_points']],
        "additional_notes": resume_result.get('additional_notes', ''),
        "future_plans": resume_result.get('future_plans', ''),
        "potential_advancements": resume_result.get('potential_advancements', ''),
        "interview_questions": resume_result.get('interview_questions', []),
        "repo_info": {"name": clone_result['repo_name']}
    }

    await websocket.send_text(json.dumps({
        "type": "complete",
        "content": "Generation complete!",
        "result": final_result,
        "redirect_path": path,
        "generation_id": generation_id
    }))
    await save_generation_data(session_data.session_id, generation_id, {"status": "complete", "result": final_result})
    session_data.status = "complete"
    await save_session(session_data)
    logger.info({"message": "Resume generation completed successfully", "session_id": session_data.session_id, "repo": repo_url, "generation_id": generation_id})
    return True


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str, generation_id: str = Query(...)):
    """Handle WebSocket connections for resume generation."""
    logger.info({"message": "WebSocket connection initiated", "session_id": session_id, "generation_id": generation_id})

    if not re.match(r'^[a-f0-9-]{36}$', session_id) or not re.match(r'^[a-f0-9-]{36}$', generation_id):
        logger.error({"message": "Invalid ID format", "session_id": session_id, "generation_id": generation_id})
        await websocket.close(code=4003, reason="Invalid session or generation ID")
        return

    await websocket.accept()
    session_data = await get_session(session_id)
    if not session_data:
        await websocket.send_text(json.dumps({"type": "error", "content": "Invalid session ID", "generation_id": generation_id}))
        await websocket.close(code=4003)
        return

    existing_generation = await get_generation_data(session_id, generation_id)
    if existing_generation and existing_generation.get("status") == "complete":
        await websocket.send_text(json.dumps({
            "type": "complete",
            "content": "Generation complete!",
            "result": existing_generation.get("result"),
            "redirect_path": session_data.redirect_url or "/",
            "generation_id": generation_id
        }))
        await websocket.close(code=1000)
        return

    session_data.status = "active"
    await save_session(session_data)

    try:
        success = await process_resume_generation(websocket, session_data, generation_id)
        if not success:
            await websocket.close(code=4002)
    except WebSocketDisconnect:
        logger.warning({"message": "WebSocket disconnected by client", "session_id": session_id, "generation_id": generation_id})
        await save_generation_data(session_id, generation_id, {"status": "disconnected"})
        session_data.status = "disconnected"
        await save_session(session_data)
    except Exception as e:
        logger.error({"message": "Unrecoverable WebSocket error", "session_id": session_id, "generation_id": generation_id, "error": str(e), "type": type(e).__name__})
        await websocket.send_text(json.dumps({"type": "error", "content": f"Unexpected error: {str(e)}", "generation_id": generation_id}))
        await save_generation_data(session_id, generation_id, {"status": "error", "error": str(e)})
        session_data.status = "error"
        await save_session(session_data)
        await websocket.close(code=4000)
    finally:
        if session_data.local_path:
            try:
                logger.info({"message": "Cleaning up local repository files", "session_id": session_id, "path": session_data.local_path})
                await asyncio.to_thread(robust_rmtree, session_data.local_path)
                session_data.local_path = None
                await save_session(session_data)
            except Exception as e:
                logger.error({"message": "Failed to cleanup local files", "session_id": session_id, "error": str(e)})
        
        if session_data.status == "active":
            session_data.status = "complete"
            await save_session(session_data)
            
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close(code=1000)
        except Exception:
            pass  # Connection already closed

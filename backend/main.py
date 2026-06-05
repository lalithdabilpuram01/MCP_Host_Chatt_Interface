import logging
import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from backend.config import get_settings
from backend.logging_config import configure_logging
from backend.models import (
    ChatRequest,
    ChatResponse,
    PendingToolCall,
    RuntimeSettingsResponse,
    RuntimeSettingsUpdate,
)
from backend.services.groq_client import GroqChatError, GroqChatService
from backend.services.mcp_client import MCPHostClient, MCPConnectionError
from backend.services.settings_store import (
    delete_saved_settings,
    get_effective_settings,
    get_saved_settings,
    mask_secret,
    save_settings,
)


settings = get_settings()
LOG_FILE_PATH = configure_logging(settings)
logger = logging.getLogger(__name__)
app = FastAPI(title="MCP Client Chatbot API")
PENDING_APPROVALS: dict[str, dict[str, object]] = {}
CAPABILITY_CACHE: dict[str, object] = {"key": None, "expires_at": 0.0, "tools": [], "prompts": []}
CAPABILITY_CACHE_SECONDS = 15
APPROVAL_TTL_SECONDS = 600

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin, "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.exception(
            "Request failed method=%s path=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            duration_ms,
        )
        raise

    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "Request completed method=%s path=%s status=%s duration_ms=%.2f",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/tools")
async def tools() -> dict[str, object]:
    try:
        discovered, prompts = await _discover_capabilities()
    except MCPConnectionError as exc:
        logger.warning("Tool discovery failed for /api/tools: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"tools": discovered, "prompts": prompts}


def _settings_cache_key() -> str:
    effective = get_effective_settings()
    return "|".join(
        [
            effective.mcp_server_url or "",
            effective.mcp_server_command or "",
            effective.mcp_server_env_json or "",
            effective.mcp_transport,
            effective.mcp_servers_json or "",
        ]
    )


async def _discover_capabilities() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    cache_key = _settings_cache_key()
    now = time.monotonic()
    if CAPABILITY_CACHE["key"] == cache_key and now < float(CAPABILITY_CACHE["expires_at"]):
        logger.debug("Using cached MCP capabilities.")
        return CAPABILITY_CACHE["tools"], CAPABILITY_CACHE["prompts"]  # type: ignore[return-value]

    mcp_client = MCPHostClient(get_effective_settings())
    tools, prompts = await mcp_client.discover_capabilities()
    logger.info("Discovered MCP capabilities tools=%s prompts=%s", len(tools), len(prompts))
    CAPABILITY_CACHE.update(
        {
            "key": cache_key,
            "expires_at": now + CAPABILITY_CACHE_SECONDS,
            "tools": tools,
            "prompts": prompts,
        }
    )
    return tools, prompts


def _store_pending_approval(
    messages: list[dict[str, object]],
    pending_tool_calls: list[PendingToolCall],
) -> str:
    _cleanup_pending_approvals()
    approval_id = str(uuid4())
    PENDING_APPROVALS[approval_id] = {
        "messages": messages,
        "pending_tool_calls": pending_tool_calls,
        "created_at": time.monotonic(),
    }
    logger.info("Stored pending approval approval_id=%s calls=%s", approval_id, len(pending_tool_calls))
    return approval_id


def _cleanup_pending_approvals() -> None:
    now = time.monotonic()
    expired_ids = [
        approval_id
        for approval_id, value in PENDING_APPROVALS.items()
        if now - float(value.get("created_at", 0)) > APPROVAL_TTL_SECONDS
    ]
    for approval_id in expired_ids:
        PENDING_APPROVALS.pop(approval_id, None)
    if expired_ids:
        logger.info("Cleaned up expired approvals count=%s", len(expired_ids))


def _approval_response(result, discovery_error: str | None = None) -> ChatResponse:
    if result.pending_tool_calls and result.pending_messages:
        approval_id = _store_pending_approval(result.pending_messages, result.pending_tool_calls)
        return ChatResponse(
            reply=result.reply,
            used_tools=result.used_tools,
            tool_discovery_error=discovery_error,
            awaiting_tool_approval=True,
            approval_id=approval_id,
            pending_tool_calls=result.pending_tool_calls,
        )

    return ChatResponse(
        reply=result.reply,
        used_tools=result.used_tools,
        tool_discovery_error=discovery_error,
    )


def _settings_response() -> RuntimeSettingsResponse:
    effective = get_effective_settings()
    return RuntimeSettingsResponse(
        groq_api_key_set=bool(effective.groq_api_key),
        groq_api_key_preview=mask_secret(effective.groq_api_key),
        groq_model=effective.groq_model,
        mcp_server_url=effective.mcp_server_url,
        mcp_server_command=effective.mcp_server_command,
        mcp_server_env_json=effective.mcp_server_env_json,
        mcp_transport=effective.mcp_transport,
        mcp_servers_json=effective.mcp_servers_json,
        has_saved_overrides=bool(get_saved_settings()),
    )


@app.get("/api/settings", response_model=RuntimeSettingsResponse)
async def read_settings() -> RuntimeSettingsResponse:
    return _settings_response()


@app.put("/api/settings", response_model=RuntimeSettingsResponse)
async def update_settings(payload: RuntimeSettingsUpdate) -> RuntimeSettingsResponse:
    try:
        save_settings(payload)
    except ValueError as exc:
        logger.warning("Runtime settings update failed validation: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("Runtime settings updated fields=%s", sorted(payload.model_fields_set))
    return _settings_response()


@app.delete("/api/settings", response_model=RuntimeSettingsResponse)
async def delete_settings() -> RuntimeSettingsResponse:
    delete_saved_settings()
    logger.info("Runtime settings deleted.")
    return _settings_response()


@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    logger.info(
        "Chat request received has_approval_id=%s history_messages=%s",
        bool(payload.approval_id),
        len(payload.history),
    )
    active_settings = get_effective_settings()
    mcp_client = MCPHostClient(active_settings)
    discovery_error: str | None = None
    try:
        discovered_tools, discovered_prompts = await _discover_capabilities()
    except MCPConnectionError as exc:
        discovered_tools = []
        discovered_prompts = []
        discovery_error = str(exc)
        logger.warning("MCP discovery failed during chat: %s", exc)

    try:
        groq_service = GroqChatService(active_settings, mcp_client)
        if payload.approval_id:
            pending = PENDING_APPROVALS.pop(payload.approval_id, None)
            if not pending:
                logger.warning("Approval id not found approval_id=%s", payload.approval_id)
                raise HTTPException(status_code=404, detail="Tool approval request expired or was not found.")
            if not payload.approve_tool_calls:
                logger.info("Tool approval denied approval_id=%s", payload.approval_id)
                return ChatResponse(reply="Okay, I did not run the MCP tool call(s).")

            logger.info("Tool approval accepted approval_id=%s", payload.approval_id)
            result = await groq_service.continue_after_approval(
                messages=pending["messages"],  # type: ignore[arg-type]
                pending_tool_calls=pending["pending_tool_calls"],  # type: ignore[arg-type]
                discovered_tools=discovered_tools,
                discovered_prompts=discovered_prompts,
                require_approval=True,
            )
        else:
            result = await groq_service.reply(
                message=payload.message,
                history=payload.history,
                discovered_tools=discovered_tools,
                discovered_prompts=discovered_prompts,
                require_approval=True,
            )
    except GroqChatError as exc:
        logger.warning("Groq chat failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if discovery_error and result.used_tools:
        result.reply = f"{result.reply}\n\nNote: MCP tool discovery had an issue: {discovery_error}"

    response = _approval_response(result, discovery_error)
    logger.info(
        "Chat request completed used_tools=%s awaiting_approval=%s",
        len(response.used_tools),
        response.awaiting_tool_approval,
    )
    return response


def run() -> None:
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)

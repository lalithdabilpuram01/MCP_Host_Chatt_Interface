from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    history: list[ChatMessage] = Field(default_factory=list)
    approval_id: str | None = None
    approve_tool_calls: bool | None = None


class ToolCallInfo(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class PendingToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    kind: Literal["tool", "prompt"] = "tool"


class ChatResponse(BaseModel):
    reply: str
    used_tools: list[ToolCallInfo] = Field(default_factory=list)
    tool_discovery_error: str | None = None
    awaiting_tool_approval: bool = False
    approval_id: str | None = None
    pending_tool_calls: list[PendingToolCall] = Field(default_factory=list)


class RuntimeSettingsUpdate(BaseModel):
    groq_api_key: str | None = None
    groq_model: str | None = None
    mcp_server_url: str | None = None
    mcp_server_command: str | None = None
    mcp_server_env_json: str | None = None
    mcp_transport: str | None = None
    mcp_servers_json: str | None = None


class RuntimeSettingsResponse(BaseModel):
    groq_api_key_set: bool
    groq_api_key_preview: str = ""
    groq_model: str
    mcp_server_url: str | None = None
    mcp_server_command: str | None = None
    mcp_server_env_json: str | None = None
    mcp_transport: str
    mcp_servers_json: str | None = None
    has_saved_overrides: bool

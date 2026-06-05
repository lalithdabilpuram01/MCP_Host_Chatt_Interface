import json
import logging
import re
import shlex
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from backend.config import Settings

PROMPT_TOOL_PREFIX = "mcp_prompt__"
SERVER_TOOL_SEPARATOR = "__"
logger = logging.getLogger(__name__)


class MCPConnectionError(RuntimeError):
    """Raised when the app cannot connect to the configured MCP server."""


class MCPToolError(RuntimeError):
    """Raised when an MCP tool cannot be called successfully."""


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    url: str | None = None
    command: str | None = None
    env_json: str | None = None
    transport: str = "auto"


def _safe_server_name(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip())
    safe = safe.strip("_")
    if not safe:
        raise MCPConnectionError("MCP server name must contain a letter, number, underscore, or dash.")
    return safe[:40]


def _namespaced_name(server_name: str, capability_name: str) -> str:
    return f"{server_name}{SERVER_TOOL_SEPARATOR}{capability_name}"


def _split_namespaced_name(name: str) -> tuple[str, str]:
    if SERVER_TOOL_SEPARATOR not in name:
        return "default", name
    server_name, capability_name = name.split(SERVER_TOOL_SEPARATOR, 1)
    return server_name, capability_name


def _server_configs(settings: Settings) -> list[MCPServerConfig]:
    if settings.mcp_servers_json:
        try:
            parsed = json.loads(settings.mcp_servers_json)
        except json.JSONDecodeError as exc:
            raise MCPConnectionError(f"MCP_SERVERS_JSON is not valid JSON: {exc}") from exc

        if not isinstance(parsed, list):
            raise MCPConnectionError("MCP_SERVERS_JSON must be a JSON array.")

        configs: list[MCPServerConfig] = []
        seen_names: set[str] = set()
        for index, item in enumerate(parsed, start=1):
            if not isinstance(item, dict):
                raise MCPConnectionError(f"MCP server #{index} must be a JSON object.")
            raw_name = str(item.get("name") or "").strip()
            if not raw_name:
                raise MCPConnectionError(f"MCP server #{index} needs a name.")
            name = _safe_server_name(raw_name)
            if name in seen_names:
                raise MCPConnectionError(f"MCP server name '{name}' is duplicated after normalization.")
            seen_names.add(name)

            url = str(item["url"]).strip() if item.get("url") else None
            command = str(item["command"]).strip() if item.get("command") else None
            if not (url or command):
                raise MCPConnectionError(f"MCP server '{name}' needs a url or command.")

            env_json: str | None = None
            if item.get("env") is not None:
                if not isinstance(item["env"], dict):
                    raise MCPConnectionError(f"MCP server '{name}' env must be a JSON object.")
                env_json = json.dumps(item["env"])

            configs.append(
                MCPServerConfig(
                    name=name,
                    url=url,
                    command=command,
                    env_json=env_json,
                    transport=str(item.get("transport") or "auto"),
                )
            )
        return configs

    if settings.mcp_server_url or settings.mcp_server_command:
        return [
            MCPServerConfig(
                name="default",
                url=settings.mcp_server_url,
                command=settings.mcp_server_command,
                env_json=settings.mcp_server_env_json,
                transport=settings.mcp_transport,
            )
        ]

    return []


def _content_to_text(content: Any) -> str:
    """Convert MCP content blocks into readable text for the model."""
    if isinstance(content, str):
        return content

    blocks = content if isinstance(content, list) else [content]
    rendered: list[str] = []
    for block in blocks:
        text = getattr(block, "text", None)
        if text:
            rendered.append(text)
            continue

        model_dump = getattr(block, "model_dump", None)
        if callable(model_dump):
            rendered.append(json.dumps(model_dump(), default=str))
        else:
            rendered.append(str(block))

    return "\n".join(rendered)


def _tool_schema(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {}
    if callable(getattr(schema, "model_dump", None)):
        schema = schema.model_dump()
    if not isinstance(schema, dict):
        schema = {}
    return schema or {"type": "object", "properties": {}}


def _prompt_schema(prompt: Any) -> dict[str, Any]:
    arguments = getattr(prompt, "arguments", None) or []
    properties: dict[str, Any] = {}
    required: list[str] = []

    for argument in arguments:
        name = getattr(argument, "name", None)
        if not name:
            continue
        description = getattr(argument, "description", None)
        properties[name] = {
            "type": "string",
            "description": description or f"Value for {name}",
        }
        if getattr(argument, "required", False):
            required.append(name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _prompt_messages_to_text(prompt_result: Any) -> str:
    messages = getattr(prompt_result, "messages", None) or []
    rendered: list[str] = []
    for message in messages:
        role = getattr(message, "role", "unknown")
        content = getattr(message, "content", "")
        rendered.append(f"{role}: {_content_to_text(content)}")
    return "\n\n".join(rendered)


def _stdio_env(env_json: str | None) -> dict[str, str] | None:
    if not env_json:
        return None

    try:
        parsed = json.loads(env_json)
    except json.JSONDecodeError as exc:
        raise MCPConnectionError(f"MCP_SERVER_ENV_JSON is not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise MCPConnectionError("MCP_SERVER_ENV_JSON must be a JSON object.")

    return {str(key): str(value) for key, value in parsed.items()}


def _stdio_params(command: str, env_json: str | None = None) -> StdioServerParameters:
    parts = shlex.split(command)
    if not parts:
        raise MCPConnectionError("MCP_SERVER_COMMAND is empty.")
    return StdioServerParameters(command=parts[0], args=parts[1:], env=_stdio_env(env_json))


class MCPHostClient:
    """Small wrapper around the MCP Python SDK.

    A fresh session is opened per operation to keep the code simple and avoid
    stale connections during local development.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.servers = _server_configs(settings)
        self.server_by_name = {server.name: server for server in self.servers}
        logger.debug("MCP host client initialized servers=%s", [server.name for server in self.servers])

    @asynccontextmanager
    async def _session(self, server: MCPServerConfig) -> AsyncIterator[ClientSession]:
        try:
            if server.url:
                transport = server.transport.lower()
                if transport == "sse" or server.url.endswith("/sse"):
                    async with sse_client(server.url) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            yield session
                else:
                    async with streamablehttp_client(server.url) as (
                        read,
                        write,
                        _get_session_id,
                    ):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            yield session
                return

            if server.command:
                params = _stdio_params(
                    server.command,
                    server.env_json,
                )
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        yield session
                return

            raise MCPConnectionError("No MCP server configuration found.")
        except Exception as exc:  # noqa: BLE001 - normalized for the API layer
            raise MCPConnectionError(f"Could not connect to MCP server '{server.name}': {exc}") from exc

    async def discover_tools(self) -> list[dict[str, Any]]:
        tools, _prompts = await self.discover_capabilities()
        return tools

    async def discover_prompts(self) -> list[dict[str, Any]]:
        _tools, prompts = await self.discover_capabilities()
        return prompts

    async def discover_capabilities(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not self.servers:
            raise MCPConnectionError("Set MCP_SERVERS_JSON, MCP_SERVER_URL, or MCP_SERVER_COMMAND.")

        tools: list[dict[str, Any]] = []
        prompt_defs: list[dict[str, Any]] = []
        errors: list[str] = []
        for server in self.servers:
            try:
                logger.info("Discovering MCP capabilities server=%s", server.name)
                async with self._session(server) as session:
                    tools_result = await session.list_tools()
                    try:
                        prompts_result = await session.list_prompts()
                        prompts = prompts_result.prompts
                    except Exception:
                        prompts = []
            except MCPConnectionError as exc:
                logger.warning("MCP discovery failed server=%s error=%s", server.name, exc)
                errors.append(str(exc))
                continue

            logger.info(
                "MCP capabilities discovered server=%s tools=%s prompts=%s",
                server.name,
                len(tools_result.tools),
                len(prompts),
            )
            tools.extend(
                {
                    "name": _namespaced_name(server.name, tool.name),
                    "server": server.name,
                    "original_name": tool.name,
                    "description": (
                        f"[{server.name}] {tool.description or f'MCP tool named {tool.name}'}"
                    ),
                    "input_schema": _tool_schema(tool),
                }
                for tool in tools_result.tools
            )
            prompt_defs.extend(
                {
                    "name": _namespaced_name(server.name, prompt.name),
                    "server": server.name,
                    "original_name": prompt.name,
                    "description": (
                        f"[{server.name}] {prompt.description or f'MCP prompt named {prompt.name}'}"
                    ),
                    "input_schema": _prompt_schema(prompt),
                }
                for prompt in prompts
            )

        if not tools and not prompt_defs and errors:
            raise MCPConnectionError("; ".join(errors))

        return tools, prompt_defs

    async def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        server_name, prompt_name = _split_namespaced_name(name)
        server = self.server_by_name.get(server_name)
        if not server:
            logger.warning("MCP prompt requested for unconfigured server=%s prompt=%s", server_name, prompt_name)
            raise MCPToolError(f"MCP server '{server_name}' is not configured.")

        try:
            logger.info("Calling MCP prompt server=%s prompt=%s", server_name, prompt_name)
            async with self._session(server) as session:
                result = await session.get_prompt(prompt_name, arguments or {})
        except Exception as exc:  # noqa: BLE001 - return useful failure to caller
            logger.warning("MCP prompt failed server=%s prompt=%s error=%s", server_name, prompt_name, exc)
            raise MCPToolError(f"Prompt '{name}' failed: {exc}") from exc

        logger.info("MCP prompt completed server=%s prompt=%s", server_name, prompt_name)
        return _prompt_messages_to_text(result)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        server_name, tool_name = _split_namespaced_name(name)
        server = self.server_by_name.get(server_name)
        if not server:
            logger.warning("MCP tool requested for unconfigured server=%s tool=%s", server_name, tool_name)
            raise MCPToolError(f"MCP server '{server_name}' is not configured.")

        try:
            logger.info("Calling MCP tool server=%s tool=%s", server_name, tool_name)
            async with self._session(server) as session:
                result = await session.call_tool(tool_name, arguments or {})
        except Exception as exc:  # noqa: BLE001 - return useful failure to caller
            logger.warning("MCP tool failed server=%s tool=%s error=%s", server_name, tool_name, exc)
            raise MCPToolError(f"Tool '{name}' failed: {exc}") from exc

        if getattr(result, "isError", False):
            logger.warning("MCP tool returned error server=%s tool=%s", server_name, tool_name)
            raise MCPToolError(f"Tool '{name}' returned an error: {_content_to_text(result.content)}")

        logger.info("MCP tool completed server=%s tool=%s", server_name, tool_name)
        return _content_to_text(result.content)

    @staticmethod
    def as_groq_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def enhanced_description(tool: dict[str, Any]) -> str:
            return (
                f"{tool['description']}\n\n"
                "Generic client guidance: follow this tool's JSON schema exactly. If a required "
                "argument appears to need an internal ID and the user gave a human-readable value, "
                "use an available lookup/search/get/details/list tool first to resolve it."
            )

        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": enhanced_description(tool),
                    "parameters": tool["input_schema"],
                },
            }
            for tool in tools
        ]

    @staticmethod
    def as_groq_prompt_tools(prompts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": f"{PROMPT_TOOL_PREFIX}{prompt['name']}",
                    "description": (
                        f"Use MCP prompt '{prompt['name']}'. {prompt['description']}\n\n"
                        "Call this when the user's request matches this prompt workflow. "
                        "The returned prompt content will guide the next tool calls."
                    ),
                    "parameters": prompt["input_schema"],
                },
            }
            for prompt in prompts
        ]


GenericMCPClient = MCPHostClient

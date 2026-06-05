import json
import logging
from dataclasses import dataclass, field
from typing import Any

from groq import APIError, AsyncGroq, GroqError

from backend.config import Settings
from backend.models import ChatMessage, PendingToolCall, ToolCallInfo
from backend.services.mcp_client import MCPHostClient, MCPToolError, PROMPT_TOOL_PREFIX

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a helpful assistant connected to one or more MCP servers.

Use MCP tools when the user's request requires live data, external actions, or server-specific capabilities that the available tools can provide.
For general questions that do not need MCP data or actions, answer directly without tools.
Never invent tool results. If a needed tool is unavailable or fails, explain the issue clearly."""

TOOL_USE_GUIDANCE = """Important generic MCP tool workflow rules:

- Treat tool names, descriptions, and JSON schemas as the source of truth.
- If MCP prompt tools are available and the user request matches a prompt workflow, call the relevant MCP prompt tool first to retrieve server-authored workflow instructions.
- If a requested action requires an ID but the user provides a name, email, title, or other human-readable identifier, first look for an available lookup/search/get/details/list tool that can resolve that value.
- If multiple tools are needed, call them in sequence. For example: resolve a referenced entity -> use the returned ID in the action tool.
- Do not claim that a referenced entity cannot be found until you have tried the most relevant available lookup/search/get/details/list tool with the exact value supplied by the user.
- Preserve user-provided values exactly when passing tool arguments unless a tool result provides a canonical ID/value to use.
- Before any MCP tool or MCP prompt is executed, the application will ask the user for permission. Plan tool calls carefully so the user can approve them.
- Ask a concise follow-up question only when the available tools and prior tool results still do not provide required arguments."""


@dataclass
class ChatServiceResult:
    reply: str
    used_tools: list[ToolCallInfo] = field(default_factory=list)
    pending_tool_calls: list[PendingToolCall] = field(default_factory=list)
    pending_messages: list[dict[str, Any]] | None = None


class GroqChatError(RuntimeError):
    """Raised when Groq cannot complete the chat request."""


class GroqChatService:
    def __init__(self, settings: Settings, mcp_client: MCPHostClient):
        if not settings.groq_api_key:
            raise GroqChatError("GROQ_API_KEY is not configured.")
        self.settings = settings
        self.client = AsyncGroq(api_key=settings.groq_api_key)
        self.mcp_client = mcp_client
        logger.debug("Groq chat service initialized model=%s", settings.groq_model)

    def _build_messages(self, history: list[ChatMessage], message: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{TOOL_USE_GUIDANCE}"}
        ]
        messages.extend({"role": item.role, "content": item.content} for item in history[-12:])
        messages.append({"role": "user", "content": message})
        return messages

    @staticmethod
    def _tool_call_payload(call: Any) -> dict[str, Any]:
        return {
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.function.name,
                "arguments": call.function.arguments,
            },
        }

    @staticmethod
    def _parse_tool_call(call: Any, prompt_names: set[str]) -> tuple[PendingToolCall, str | None]:
        tool_name = call.function.name
        try:
            arguments = json.loads(call.function.arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be a JSON object.")
        except ValueError as exc:
            arguments = {}
            return (
                PendingToolCall(
                    id=call.id,
                    name=tool_name,
                    arguments=arguments,
                    kind="prompt" if tool_name in prompt_names else "tool",
                ),
                f"Invalid arguments for tool '{tool_name}': {exc}",
            )

        return (
            PendingToolCall(
                id=call.id,
                name=tool_name,
                arguments=arguments,
                kind="prompt" if tool_name in prompt_names else "tool",
            ),
            None,
        )

    async def _completion(
        self,
        messages: list[dict[str, Any]],
        groq_tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        request: dict[str, Any] = {
            "model": self.settings.groq_model,
            "messages": messages,
            "temperature": 0.2,
        }
        if groq_tools:
            request["tools"] = groq_tools
            request["tool_choice"] = "auto"

        try:
            return await self.client.chat.completions.create(**request)
        except (APIError, GroqError, Exception) as exc:  # noqa: BLE001
            logger.warning("Groq completion failed model=%s error=%s", self.settings.groq_model, exc)
            raise GroqChatError(f"Groq chat completion failed: {exc}") from exc

    async def _execute_approved_call(
        self,
        pending_call: PendingToolCall,
        available_names: set[str],
        available_prompt_names: set[str],
    ) -> tuple[str, ToolCallInfo | None]:
        tool_name = pending_call.name
        arguments = pending_call.arguments

        if tool_name in available_prompt_names:
            prompt_name = tool_name.removeprefix(PROMPT_TOOL_PREFIX)
            try:
                prompt_result = await self.mcp_client.get_prompt(prompt_name, arguments)
            except MCPToolError as exc:
                logger.warning("Approved MCP prompt failed prompt=%s error=%s", prompt_name, exc)
                return str(exc), None
            return (
                f"MCP prompt '{prompt_name}' returned this workflow context. "
                "Use it to decide the next MCP tool calls:\n\n"
                f"{prompt_result}",
                ToolCallInfo(name=f"prompt:{prompt_name}", arguments=arguments),
            )

        if tool_name not in available_names:
            logger.warning("Model requested unavailable MCP tool tool=%s", tool_name)
            return f"Tool '{tool_name}' is not available from the MCP server.", None

        try:
            tool_result = await self.mcp_client.call_tool(tool_name, arguments)
        except MCPToolError as exc:
            logger.warning("Approved MCP tool failed tool=%s error=%s", tool_name, exc)
            return str(exc), None
        return tool_result, ToolCallInfo(name=tool_name, arguments=arguments)

    async def _continue_loop(
        self,
        messages: list[dict[str, Any]],
        groq_tools: list[dict[str, Any]],
        discovered_tools: list[dict[str, Any]],
        discovered_prompts: list[dict[str, Any]],
        used_tools: list[ToolCallInfo],
        require_approval: bool,
    ) -> ChatServiceResult:
        available_names = {tool["name"] for tool in discovered_tools}
        available_prompt_names = {f"{PROMPT_TOOL_PREFIX}{prompt['name']}" for prompt in discovered_prompts}

        for iteration in range(5):
            logger.debug("Starting Groq tool loop iteration=%s", iteration + 1)
            completion = await self._completion(messages, groq_tools)
            assistant_message = completion.choices[0].message
            tool_calls = assistant_message.tool_calls or []

            if not tool_calls:
                logger.info("Groq response completed without more tool calls iteration=%s", iteration + 1)
                return ChatServiceResult(reply=assistant_message.content or "", used_tools=used_tools)

            logger.info(
                "Groq requested MCP calls iteration=%s count=%s names=%s",
                iteration + 1,
                len(tool_calls),
                [call.function.name for call in tool_calls],
            )
            assistant_entry = {
                "role": "assistant",
                "content": assistant_message.content,
                "tool_calls": [self._tool_call_payload(call) for call in tool_calls],
            }
            messages.append(assistant_entry)

            pending_calls: list[PendingToolCall] = []
            invalid_results: list[tuple[str, str]] = []
            for call in tool_calls:
                pending_call, parse_error = self._parse_tool_call(call, available_prompt_names)
                pending_calls.append(pending_call)
                if parse_error:
                    invalid_results.append((pending_call.id, parse_error))

            if invalid_results:
                logger.warning("Groq produced invalid tool arguments count=%s", len(invalid_results))
                for call_id, parse_error in invalid_results:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": next(call.name for call in pending_calls if call.id == call_id),
                            "content": parse_error,
                        }
                    )
                continue

            if require_approval:
                names = ", ".join(call.name for call in pending_calls)
                logger.info("MCP calls require approval names=%s", names)
                return ChatServiceResult(
                    reply=f"I need your permission before running MCP call(s): {names}.",
                    used_tools=used_tools,
                    pending_tool_calls=pending_calls,
                    pending_messages=messages,
                )

            for pending_call in pending_calls:
                tool_result, used_tool = await self._execute_approved_call(
                    pending_call,
                    available_names,
                    available_prompt_names,
                )
                if used_tool:
                    used_tools.append(used_tool)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": pending_call.id,
                        "name": pending_call.name,
                        "content": tool_result,
                    }
                )

        messages.append(
            {
                "role": "system",
                "content": "Tool-call limit reached. Summarize the completed tool results and ask for any missing information.",
            }
        )
        logger.warning("Groq tool-call loop limit reached.")
        final = await self._completion(messages)
        return ChatServiceResult(reply=final.choices[0].message.content or "", used_tools=used_tools)

    async def reply(
        self,
        message: str,
        history: list[ChatMessage],
        discovered_tools: list[dict[str, Any]],
        discovered_prompts: list[dict[str, Any]] | None = None,
        require_approval: bool = True,
    ) -> ChatServiceResult:
        messages = self._build_messages(history, message)
        discovered_prompts = discovered_prompts or []
        logger.info(
            "Starting Groq reply tools=%s prompts=%s history_messages=%s",
            len(discovered_tools),
            len(discovered_prompts),
            len(history),
        )
        groq_tools = [
            *MCPHostClient.as_groq_prompt_tools(discovered_prompts),
            *MCPHostClient.as_groq_tools(discovered_tools),
        ]
        return await self._continue_loop(
            messages=messages,
            groq_tools=groq_tools,
            discovered_tools=discovered_tools,
            discovered_prompts=discovered_prompts,
            used_tools=[],
            require_approval=require_approval,
        )

    async def continue_after_approval(
        self,
        messages: list[dict[str, Any]],
        pending_tool_calls: list[PendingToolCall],
        discovered_tools: list[dict[str, Any]],
        discovered_prompts: list[dict[str, Any]] | None = None,
        require_approval: bool = True,
    ) -> ChatServiceResult:
        discovered_prompts = discovered_prompts or []
        available_names = {tool["name"] for tool in discovered_tools}
        available_prompt_names = {f"{PROMPT_TOOL_PREFIX}{prompt['name']}" for prompt in discovered_prompts}
        used_tools: list[ToolCallInfo] = []
        logger.info("Continuing after approval pending_calls=%s", len(pending_tool_calls))

        for pending_call in pending_tool_calls:
            tool_result, used_tool = await self._execute_approved_call(
                pending_call,
                available_names,
                available_prompt_names,
            )
            if used_tool:
                used_tools.append(used_tool)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": pending_call.id,
                    "name": pending_call.name,
                    "content": tool_result,
                }
            )

        groq_tools = [
            *MCPHostClient.as_groq_prompt_tools(discovered_prompts),
            *MCPHostClient.as_groq_tools(discovered_tools),
        ]
        return await self._continue_loop(
            messages=messages,
            groq_tools=groq_tools,
            discovered_tools=discovered_tools,
            discovered_prompts=discovered_prompts,
            used_tools=used_tools,
            require_approval=require_approval,
        )

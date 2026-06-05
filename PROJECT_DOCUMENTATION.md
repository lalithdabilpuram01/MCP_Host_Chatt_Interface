# Generic MCP Host - Project Documentation

## 1. Project Overview

This project is a full-stack generic MCP host chatbot. It connects a React chat
UI to a FastAPI backend, uses Groq for natural language reasoning, and can
discover prompts and tools from one or more MCP servers across any domain.

The host is intentionally domain-neutral. It does not hardcode tool names,
business entities, workflows, or server-specific assumptions. A server can be
for CRM, documents, finance, infrastructure, local files, internal operations,
or any other MCP-compatible domain.

Core responsibilities:

1. Provide a chat UI for users.
2. Manage runtime settings for Groq and MCP server connections.
3. Discover MCP tools and prompts dynamically.
4. Convert discovered MCP capabilities into Groq-compatible tool definitions.
5. Ask for user approval before executing MCP tool or prompt calls.
6. Route each approved call to the MCP server that owns it.

## 2. High-Level Architecture

```text
User
  |
  v
React Frontend
  |
  | HTTP JSON
  v
FastAPI Backend
  |
  |-- Groq API
  |     - interprets the user message
  |     - decides whether an MCP capability is needed
  |     - writes the final response
  |
  |-- MCP Host Client
        - connects to configured MCP servers
        - discovers tools and prompts dynamically
        - namespaces capabilities by server name
        - routes selected calls back to the owning server
```

The frontend never talks directly to Groq or MCP servers. It only talks to the
FastAPI backend.

## 3. Project Structure

```text
mcp_client_codex/
  backend/
    main.py                    # FastAPI routes
    config.py                  # Environment configuration
    models.py                  # Request/response schemas
    services/
      groq_client.py           # Groq chat and tool-calling loop
      mcp_client.py            # MCP discovery, namespacing, routing, calls
      settings_store.py        # Runtime settings persistence
  frontend/
    index.html
    package.json
    src/
      api.js                   # Backend API client
      main.jsx                 # Chat UI and settings window
      styles.css               # UI styling
  .env.example
  pyproject.toml
  README.md
```

## 4. Configuration

The backend reads settings from `.env`, then applies optional saved overrides
from `app_settings.json`.

Important environment variables:

```text
GROQ_API_KEY
GROQ_MODEL
MCP_SERVERS_JSON
MCP_SERVER_URL
MCP_SERVER_COMMAND
MCP_SERVER_ENV_JSON
MCP_TRANSPORT
FRONTEND_ORIGIN
REQUEST_TIMEOUT_SECONDS
LOG_LEVEL
LOG_DIR
LOG_FILE
LOG_MAX_BYTES
LOG_BACKUP_COUNT
```

For a single MCP server, use `MCP_SERVER_URL` or `MCP_SERVER_COMMAND`:

```env
MCP_SERVER_URL=http://localhost:8001/mcp
MCP_TRANSPORT=streamable_http
```

```env
MCP_SERVER_COMMAND=python /absolute/path/to/server.py
MCP_SERVER_ENV_JSON={"TOKEN":"value"}
```

For multiple MCP servers, use `MCP_SERVERS_JSON`. When present, it takes
priority over the single-server fields:

```env
MCP_SERVERS_JSON=[{"name":"crm","url":"http://localhost:8001/mcp","transport":"streamable_http"},{"name":"docs","command":"python /absolute/path/to/docs_server.py","env":{"TOKEN":"value"}}]
```

Each server object supports:

- `name`: required unique server name, using letters, numbers, `_`, or `-`
- `url`: HTTP/SSE MCP endpoint
- `command`: stdio command, used when `url` is not set
- `transport`: `auto`, `streamable_http`, or `sse`
- `env`: optional environment variables for stdio server processes

## 5. Capability Namespacing

Different MCP servers can expose tools with the same original name. The host
prevents collisions by exposing every capability with a server-prefixed name:

```text
crm__list_accounts
docs__search_docs
billing__create_invoice
```

The model sees namespaced names, but the backend strips the namespace before
calling the owning MCP server. This lets each server keep its normal MCP tool
names while the host safely combines multiple domains.

MCP prompts are also exposed as model-callable tools with the internal
`mcp_prompt__` prefix:

```text
mcp_prompt__docs__research_summary
```

## 6. Chat Flow

1. The user sends a message from the React UI.
2. FastAPI loads effective runtime settings.
3. The backend discovers tools and prompts from all configured MCP servers.
4. Discovered capabilities are converted into Groq tool definitions.
5. Groq decides whether the user request needs a tool or prompt.
6. If a call is requested, the backend returns an approval request to the UI.
7. After user approval, the backend routes each call to the owning MCP server.
8. Tool results are sent back to Groq.
9. Groq returns a natural-language response for the UI.

General questions that do not need MCP data or actions are answered directly.

## 7. Backend Modules

`backend/main.py` exposes API routes for health, tool discovery, chat, and
runtime settings. It also caches discovered capabilities briefly so frequent
chat requests do not rediscover tools on every request. It configures logging
at startup and records request timing, settings changes, discovery results,
approval decisions, and chat-level failures.

`backend/services/mcp_client.py` contains the generic MCP host client. It
parses single-server and multi-server configuration, opens MCP sessions using
streamable HTTP, SSE, or stdio, discovers tools/prompts, namespaces capability
names, and routes approved calls. It logs discovery and execution by server
name and capability name.

`backend/services/groq_client.py` contains the Groq reasoning loop. It builds
messages, attaches MCP capabilities as function tools, handles approval
continuation, executes approved calls through the MCP host client, and asks
Groq for the final answer. It logs tool-call loop progress and Groq failures.

`backend/services/settings_store.py` persists runtime overrides from the UI.
It validates both single-server environment JSON and multi-server JSON before
saving.

`backend/logging_config.py` configures console logging plus rotating file logs.
By default, logs are written to:

```text
logs/app.log
```

Rotation is controlled by `LOG_MAX_BYTES` and `LOG_BACKUP_COUNT`.

## 8. Frontend Modules

`frontend/src/main.jsx` renders the chat UI, session list, approval cards, tool
chips, and settings window.

`frontend/src/api.js` wraps the backend API calls:

```text
POST   /api/chat
GET    /api/settings
PUT    /api/settings
DELETE /api/settings
```

Chat sessions are stored in browser localStorage under generic keys:

```text
generic-mcp-chat-sessions
generic-mcp-active-session
```

The frontend can still read the older project keys and migrate them naturally
when the next session update is saved.

## 9. Safety And Error Handling

The host asks for user approval before executing MCP tool and prompt calls.

The backend handles:

- MCP configuration errors
- MCP connection failures
- Partial multi-server discovery failures
- Unavailable tool names
- Invalid JSON tool arguments
- MCP tool execution errors
- Groq API errors

If discovery fails for every configured server, `/api/tools` returns a service
error. During chat, the assistant can still answer general questions through
Groq and include MCP discovery errors for debugging.

## 10. Adding A New Domain

To add a new domain, add another MCP server object to `MCP_SERVERS_JSON`:

```env
MCP_SERVERS_JSON=[{"name":"crm","url":"http://localhost:8001/mcp"},{"name":"inventory","url":"http://localhost:8002/mcp"},{"name":"docs","command":"python /absolute/path/to/docs_server.py"}]
```

No frontend changes are needed. The host discovers the new server's tools at
runtime and exposes them to the model with namespaced names.

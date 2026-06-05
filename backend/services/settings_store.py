import json
from pathlib import Path
from typing import Any

from backend.config import Settings, get_settings
from backend.models import RuntimeSettingsUpdate


SETTINGS_FILE = Path(__file__).resolve().parents[2] / "app_settings.json"


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _read_store() -> dict[str, Any]:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def get_saved_settings() -> dict[str, Any]:
    return _read_store()


def get_effective_settings() -> Settings:
    base = get_settings()
    saved = get_saved_settings()
    return base.model_copy(update={key: value for key, value in saved.items() if value is not None})


def save_settings(update: RuntimeSettingsUpdate) -> dict[str, Any]:
    existing = get_saved_settings()
    values = {
        "groq_api_key": _clean(update.groq_api_key),
        "groq_model": _clean(update.groq_model),
        "mcp_server_url": _clean(update.mcp_server_url),
        "mcp_server_command": _clean(update.mcp_server_command),
        "mcp_server_env_json": _clean(update.mcp_server_env_json),
        "mcp_transport": _clean(update.mcp_transport),
        "mcp_servers_json": _clean(update.mcp_servers_json),
    }
    for key in update.model_fields_set:
        if values[key] is None:
            existing.pop(key, None)
        else:
            existing[key] = values[key]

    env_json = existing.get("mcp_server_env_json")
    if env_json:
        parsed = json.loads(env_json)
        if not isinstance(parsed, dict):
            raise ValueError("MCP server env JSON must be a JSON object.")

    servers_json = existing.get("mcp_servers_json")
    if servers_json:
        parsed_servers = json.loads(servers_json)
        if not isinstance(parsed_servers, list):
            raise ValueError("MCP servers JSON must be a JSON array.")
        seen_names: set[str] = set()
        for index, server in enumerate(parsed_servers, start=1):
            if not isinstance(server, dict):
                raise ValueError(f"MCP server #{index} must be a JSON object.")
            name = str(server.get("name") or "").strip()
            if not name:
                raise ValueError(f"MCP server #{index} needs a name.")
            if name in seen_names:
                raise ValueError(f"MCP server name '{name}' is duplicated.")
            seen_names.add(name)
            if not (server.get("url") or server.get("command")):
                raise ValueError(f"MCP server '{name}' needs a url or command.")
            env = server.get("env")
            if env is not None and not isinstance(env, dict):
                raise ValueError(f"MCP server '{name}' env must be a JSON object.")

    SETTINGS_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return existing


def delete_saved_settings() -> None:
    SETTINGS_FILE.unlink(missing_ok=True)


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "********"
    return f"{value[:4]}...{value[-4:]}"

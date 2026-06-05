from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    groq_model: str = Field(default="llama-3.3-70b-versatile", alias="GROQ_MODEL")

    mcp_server_url: str | None = Field(default=None, alias="MCP_SERVER_URL")
    mcp_server_command: str | None = Field(default=None, alias="MCP_SERVER_COMMAND")
    mcp_server_env_json: str | None = Field(default=None, alias="MCP_SERVER_ENV_JSON")
    mcp_transport: str = Field(default="auto", alias="MCP_TRANSPORT")
    mcp_servers_json: str | None = Field(default=None, alias="MCP_SERVERS_JSON")

    frontend_origin: str = Field(default="http://localhost:5173", alias="FRONTEND_ORIGIN")
    request_timeout_seconds: float = Field(default=45.0, alias="REQUEST_TIMEOUT_SECONDS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_dir: str = Field(default="logs", alias="LOG_DIR")
    log_file: str = Field(default="app.log", alias="LOG_FILE")
    log_max_bytes: int = Field(default=5_000_000, alias="LOG_MAX_BYTES")
    log_backup_count: int = Field(default=5, alias="LOG_BACKUP_COUNT")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def has_mcp_server(self) -> bool:
        return bool(self.mcp_servers_json or self.mcp_server_url or self.mcp_server_command)


@lru_cache
def get_settings() -> Settings:
    return Settings()

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration, read from environment variables (case-insensitive)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    loki_url: str = "http://loki:3100"
    loki_timeout: float = 5.0
    log_lookback_minutes: int = 10
    max_log_lines: int = 30
    max_log_chars: int = 3000

    # Any OpenAI-compatible server: Ollama, llama.cpp server, vLLM.
    llm_base_url: str = "http://ollama:11434/v1"
    llm_model: str = "qwen2.5:0.5b"
    llm_api_key: SecretStr = SecretStr("not-needed")
    llm_timeout: float = 90.0
    llm_max_tokens: int = 250
    llm_concurrency: int = 1

    slack_webhook_url: SecretStr | None = None
    slack_timeout: float = 5.0
    dry_run: bool = False

    grafana_url: str | None = None
    webhook_token: SecretStr | None = None
    dedupe_ttl_seconds: int = 3600
    log_level: str = "INFO"

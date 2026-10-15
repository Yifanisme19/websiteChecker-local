import json
from pathlib import Path
from typing import Literal
from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')

    app_env: Literal['local', 'prod'] = 'prod'
    api_host: str = '0.0.0.0'
    api_port: int = Field(default=18000, ge=1, le=65535)

    openclaw_base_url: AnyHttpUrl = AnyHttpUrl('http://127.0.0.1:18789')
    openclaw_token: SecretStr = SecretStr('')
    openclaw_timeout_seconds: float = Field(default=5, gt=0, le=60)

    audit_retry_page_url: AnyHttpUrl | None = None

    discussion_notifications_enabled: bool = False
    discussion_api_token: SecretStr = SecretStr('')

    # Only for explicit import-legacy; never used for runtime authentication.
    api_token: SecretStr = SecretStr('')
    db_host: str = '127.0.0.1'
    db_port: int = Field(default=3306, ge=1, le=65535)
    db_name: str = 'website_checker'
    db_user: str = 'root'
    db_pass: SecretStr = SecretStr('')

    openclaw_agent_id: str = 'website-checker-agent'
    openclaw_model: str = 'openai/gpt-6-astra'
    payload_guard_required: bool = True
    openclaw_thinking: Literal['off', 'minimal', 'low', 'medium', 'high', 'xhigh'] = 'low'
    worker_request_timeout_seconds: int = Field(default=900, ge=1, le=7200)
    worker_poll_seconds: float = Field(default=3, gt=0)
    worker_heartbeat_seconds: int = Field(default=15, ge=1)
    worker_stale_seconds: int = Field(default=120, ge=30)


def runtime_settings() -> Settings:
    """Bind this checkout to its deployment; reject cross-environment overrides."""
    settings = Settings()
    binding = ROOT / '.environment.json'
    if binding.exists():
        expected = json.loads(binding.read_text())
        for key in ('app_env', 'api_port', 'db_host', 'db_port', 'db_name', 'db_user',
                    'openclaw_agent_id', 'openclaw_base_url'):
            actual = getattr(settings, key)
            if key == 'openclaw_base_url':
                actual = str(actual).rstrip('/')
            if actual != expected[key]:
                raise ValueError(f'Environment binding mismatch: {key}; check this deployment configuration')
    return settings

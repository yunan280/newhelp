from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Ch09Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='CH09_', env_file='.env',
                                     extra='ignore', populate_by_name=True)
    enabled: bool = False
    base_url: str | None = Field(default=None, validation_alias='LANGFUSE_BASE_URL')
    public_key: str | None = Field(default=None, validation_alias='LANGFUSE_PUBLIC_KEY', repr=False)
    secret_key: str | None = Field(default=None, validation_alias='LANGFUSE_SECRET_KEY', repr=False)
    confidence_path: Path = Path('config/ch09-confidence.json')
    artifact_dir: Path = Path('artifacts/ch09')
    worker_interval_seconds: float = Field(default=10, gt=0)

    @model_validator(mode='after')
    def require_local_observability(self):
        if self.enabled:
            url = urlsplit(self.base_url or '')
            if (url.scheme not in {'http', 'https'}
                or url.hostname not in {'localhost', '127.0.0.1', '::1'}
                or url.username or url.password or url.query or url.fragment):
                raise ValueError('启用Ch09时LANGFUSE_BASE_URL必须显式指向本地自部署服务')
            if not self.public_key or not self.secret_key:
                raise ValueError('启用Ch09时必须配置本地项目的Langfuse凭据')
        return self

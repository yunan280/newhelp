from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from .limits import AgentLimits


class Ch05Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CH05_", env_file=".env", extra="ignore")
    checkpoint_path: Path = Path("data/ch05/checkpoints.sqlite3")
    max_decisions: int = 4
    max_tools: int = 8
    final_max_tokens: int = 1024
    decision_max_tokens: int = 256
    classifier_max_tokens: int = 128
    total_model_tokens: int = 64000
    turn_seconds: float = 180
    request_seconds: float = 30
    max_retries: int = 0

    def limits(self) -> AgentLimits:
        return AgentLimits(**self.model_dump(exclude={"checkpoint_path"}))

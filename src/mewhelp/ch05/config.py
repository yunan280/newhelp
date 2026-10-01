from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from mewhelp.llm import get_chat_model

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


def get_ch05_model(
    output_tokens: int,
    *,
    temperature: float = 0.0,
    streaming: bool = False,
    json_mode: bool = False,
):
    kwargs = {
        "extra_body": {"max_tokens": output_tokens, "thinking": {"type": "disabled"}},
        "timeout": 30,
        "max_retries": 0,
        "use_responses_api": False,
    }
    if streaming:
        kwargs["stream_usage"] = True
    if json_mode:
        # Top-level response_format selects SDK auto-parsing, which requires strict tools.
        # Our OpenAI-compatible provider uses JSON mode with the existing non-strict tools.
        kwargs["extra_body"]["response_format"] = {"type": "json_object"}
    return get_chat_model(temperature=temperature, **kwargs)

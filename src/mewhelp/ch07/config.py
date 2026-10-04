import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ContextSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='', env_file='.env',
                                      env_file_encoding='utf-8', extra='ignore')
    model_context_window: int = Field(default=128000, ge=1)
    max_output_tokens: int = Field(default=4096, ge=1)
    max_user_input_tokens: int = Field(default=4096, ge=1)
    max_agent_steps: int = Field(default=4, ge=1)
    tool_result_max_tokens: int = Field(default=1200, ge=1)
    rerank_top_k: int = Field(default=10, ge=1)
    context_calibration_path: Path | None = None


@dataclass(frozen=True)
class BudgetProfile:
    prefix_reserve: int = 1700
    evidence_per_chunk: int = 400
    summary_reserve: int = 500
    safety_reserve: int = 500
    control_reserve: int = 400
    desired_turns: int = 40
    steady_user_tokens: int = 256
    steady_answer_tokens: int = 512
    steady_tool_tokens: int = 200
    steady_structure_tokens: int = 96
    cjk_tokens_per_char: float = 1.2
    ascii_chars_per_token: float = 3
    tool_template_tokens: int = 208
    version: str = 'ch07-v2'

    def __post_init__(self):
        for key, value in asdict(self).items():
            if key != 'version' and value <= 0:
                raise ValueError(f'profile.{key} must be positive')

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(payload.encode()).hexdigest()

    @property
    def steady_turn(self) -> int:
        return (self.steady_user_tokens + self.steady_answer_tokens
                + self.steady_tool_tokens + self.steady_structure_tokens)


def load_profile(path: Path | None) -> BudgetProfile:
    if path is None:
        return BudgetProfile()
    data = json.loads(path.read_text(encoding='utf-8'))
    profile = BudgetProfile(**data['profile'])
    if data.get('profile_hash') != profile.fingerprint:
        raise ValueError('context calibration profile hash mismatch; recalibrate jointly')
    return profile

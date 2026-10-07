from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mewhelp.llm import get_chat_model


class Ch06Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CH06_", env_file=".env", extra="ignore")
    primary_model: str = Field(default="deepseek-v4-pro", min_length=1)
    cascade_enabled: bool = False
    small_model: str | None = None
    calibration_path: Path | None = None
    router_dataset_path: Path = Path('eval/ch06')
    policy_calibration_path: Path | None = None
    understanding_max_tokens: int = Field(default=512, ge=128, le=4096)
    expansion_max_tokens: int = Field(default=512, ge=128, le=4096)
    assessment_max_tokens: int = Field(default=768, ge=128, le=4096)

    @model_validator(mode="after")
    def distinct_cascade(self):
        if self.cascade_enabled and (
            not self.small_model or self.small_model == self.primary_model
        ):
            raise ValueError("cascade requires an explicit model distinct from primary_model")
        return self


class RouterCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_hash: str
    understanding_hash: str
    intent_hash: str
    dataset_hash: str
    intent_min_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    cascade_upgrade_threshold: float = Field(ge=0, le=1, allow_inf_nan=False)
    sample_count: int = Field(gt=0)


class PolicyCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_rerank_threshold: float = Field(ge=0, allow_inf_nan=False)
    reranker_metadata: dict
    corpus_hash: str
    retrieval_input_hash: str
    dataset_hash: str
    sample_count: int = Field(gt=0)


def get_router_model(*, purpose: str, model_name: str, output_tokens: int, request_seconds: float):
    """Same configured upstream, JSON-only controls, no tool binding or hidden retry."""
    return get_chat_model(
        model_name=model_name,
        temperature=0.0,
        extra_body={
            "max_tokens": output_tokens,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
        },
        timeout=request_seconds,
        max_retries=0,
        use_responses_api=False,
    )

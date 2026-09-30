"""应用配置 —— 全部来自 .env,不硬编码。

换供应商只需要改 .env 里的 OPENAI_BASE_URL 和 LLM_MODEL。
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # .env 里还有 MYSQL_/MILVUS_/LANGFUSE_ 等本章用不到的变量,忽略掉
        extra="ignore",
    )

    openai_api_key: str
    openai_base_url: str = "https://api.openai.com/v1"
    llm_model: str
    llm_temperature: float = 0.3
    rag_calibration_path: Path | None = None
    rag_context_budget: int | None = Field(default=None, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()


# 历史消息的 token 预算。刻意不做成 env 变量 —— 本章不新增变量名。
HISTORY_TOKEN_BUDGET = 2048

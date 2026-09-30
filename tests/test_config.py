"""config.py 的测试 —— 全部用临时 .env,不依赖真实密钥。"""

import pytest
from pydantic import ValidationError

from mewhelp.config import HISTORY_TOKEN_BUDGET, Settings, get_settings


@pytest.fixture(autouse=True)
def isolate_llm_settings(monkeypatch):
    """BaseSettings 优先读进程环境；测试临时 .env 前先清理宿主覆盖值。"""
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "LLM_MODEL", "LLM_TEMPERATURE"):
        monkeypatch.delenv(name, raising=False)


def write_env(tmp_path, body: str):
    p = tmp_path / ".env"
    p.write_text(body, encoding="utf-8")
    return p


def test_reads_all_llm_fields_from_env_file(tmp_path):
    env = write_env(
        tmp_path,
        "OPENAI_API_KEY=test-key\n"
        "OPENAI_BASE_URL=https://example.test/v1\n"
        "LLM_MODEL=test-model\n"
        "LLM_TEMPERATURE=0.7\n",
    )
    s = Settings(_env_file=env)
    assert s.openai_api_key == "test-key"
    assert s.openai_base_url == "https://example.test/v1"
    assert s.llm_model == "test-model"
    assert s.llm_temperature == 0.7


def test_missing_api_key_raises_instead_of_silently_defaulting(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env = write_env(tmp_path, "LLM_MODEL=test-model\n")
    with pytest.raises(ValidationError):
        Settings(_env_file=env)


def test_unrelated_env_vars_are_ignored(tmp_path):
    """.env 里还有 MYSQL_/MILVUS_/LANGFUSE_ 等本章用不到的变量,不能让它们炸掉校验。"""
    env = write_env(
        tmp_path,
        "OPENAI_API_KEY=k\nLLM_MODEL=m\n"
        "MYSQL_PASSWORD=whatever\nMILVUS_HOST=localhost\nLANGFUSE_PUBLIC_KEY=pk-x\n",
    )
    s = Settings(_env_file=env)
    assert s.llm_model == "m"


def test_get_settings_is_process_wide_singleton():
    assert get_settings() is get_settings()


def test_history_token_budget_is_a_module_constant():
    assert HISTORY_TOKEN_BUDGET == 2048


def test_rag_configuration_is_explicit_and_budget_positive(tmp_path):
    env = write_env(
        tmp_path,
        "OPENAI_API_KEY=k\nLLM_MODEL=m\nRAG_CALIBRATION_PATH=eval/ch04/calibration.json\nRAG_CONTEXT_BUDGET=16000\n",
    )
    settings = Settings(_env_file=env)
    assert settings.rag_context_budget == 16000
    assert settings.rag_calibration_path.as_posix() == "eval/ch04/calibration.json"
    env.write_text("OPENAI_API_KEY=k\nLLM_MODEL=m\nRAG_CONTEXT_BUDGET=0\n")
    with pytest.raises(ValidationError):
        Settings(_env_file=env)

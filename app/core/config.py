"""Environment-based configuration for ReconcileX.

Uses `pydantic.BaseModel` with built-in `.env` and `os.environ` loading so that
it works seamlessly across Python 3.11–3.14 with either Pydantic v1 or Pydantic v2,
without requiring the external `pydantic-settings` package.
"""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel

try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except ImportError:
    load_dotenv = None  # type: ignore


def _load_dotenv_fallback(env_path: str = ".env") -> Dict[str, str]:
    """Parse a local .env file using the standard library if present."""
    parsed: Dict[str, str] = {}
    path = Path(env_path)
    if not path.is_file():
        return parsed
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                parsed[key] = val
    except OSError:
        pass
    return parsed


_SETTINGS_FIELD_NAMES = (
    "APP_NAME",
    "APP_VERSION",
    "ENVIRONMENT",
    "DEBUG",
    "API_V1_PREFIX",
    "HOST",
    "PORT",
    "DATABASE_URL",
    "DB_ECHO",
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    "REDIS_URL",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "CELERY_TASK_ALWAYS_EAGER",
    "QDRANT_URL",
    "QDRANT_COLLECTION_INCIDENTS",
    "QDRANT_ENABLED",
    "LLM_PROVIDER",
    "HF_MODEL_ID",
    "HF_API_TOKEN",
    "HF_INFERENCE_API_URL",
    "EMBEDDING_MODEL_ID",
    "LLM_MAX_TOKENS",
    "LLM_TEMPERATURE",
    "LLM_TIMEOUT_SECONDS",
    "SECRET_KEY",
    "OPERATOR_API_KEYS",
    "MAX_UPLOAD_BYTES",
    "DEFAULT_CURRENCY",
    "STALE_EVENT_WINDOW_SECONDS",
    "LOG_LEVEL",
    "LOG_JSON",
    "METRICS_ENABLED",
    "OTEL_ENABLED",
    "OTEL_SERVICE_NAME",
)


class Settings(BaseModel):
    """Application settings loaded from environment variables or .env file."""

    # Application metadata
    APP_NAME: str = "ReconcileX"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: Literal["development", "testing", "staging", "production"] = "development"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"

    # Server settings
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Database configuration
    DATABASE_URL: str = "sqlite:///./reconcilex.db"
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    # Redis & Celery configuration
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"
    CELERY_TASK_ALWAYS_EAGER: bool = False

    # Vector Database (Qdrant) for historical incidents and runbooks
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION_INCIDENTS: str = "reconcilex_incidents"
    QDRANT_ENABLED: bool = False

    # Agentic AI & Open-Source Model Configuration
    # Options:
    # - "mock": fast local grounded reasoning (no API key or GPU required)
    # - "huggingface_api": calls Hugging Face Inference API using HF_API_TOKEN and HF_MODEL_ID
    # - "huggingface_local": loads local Hugging Face transformers pipeline on CPU/GPU
    LLM_PROVIDER: Literal["mock", "huggingface_api", "huggingface_local", "vllm_openai_compat"] = "mock"
    HF_MODEL_ID: str = "Qwen/Qwen2.5-7B-Instruct"
    HF_API_TOKEN: Optional[str] = None
    HF_INFERENCE_API_URL: str = "https://router.huggingface.co/hf-inference/models"
    EMBEDDING_MODEL_ID: str = "sentence-transformers/all-MiniLM-L6-v2"
    LLM_MAX_TOKENS: int = 1024
    LLM_TEMPERATURE: float = 0.0
    LLM_TIMEOUT_SECONDS: int = 30

    # Security & Operational Controls
    SECRET_KEY: str = "change-me-in-production-use-strong-random-key"
    OPERATOR_API_KEYS: str = "dev-operator-key:operator,dev-admin-key:admin,dev-viewer-key:viewer"
    MAX_UPLOAD_BYTES: int = 10 * 1024 * 1024  # 10 MB upload limit
    DEFAULT_CURRENCY: str = "INR"
    STALE_EVENT_WINDOW_SECONDS: int = 3600

    # Observability
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True
    METRICS_ENABLED: bool = True
    OTEL_ENABLED: bool = False
    OTEL_SERVICE_NAME: str = "reconcilex-backend"

    def __init__(self, **values: Any) -> None:
        if load_dotenv is not None:
            load_dotenv()
        dotenv_values = _load_dotenv_fallback(".env")
        merged: Dict[str, Any] = {}
        for field_name in _SETTINGS_FIELD_NAMES:
            if field_name in values:
                merged[field_name] = values[field_name]
            elif field_name in os.environ:
                merged[field_name] = os.environ[field_name]
            elif field_name in dotenv_values:
                merged[field_name] = dotenv_values[field_name]
        for extra_k, extra_v in values.items():
            if extra_k not in merged:
                merged[extra_k] = extra_v
        super().__init__(**merged)

    @property
    def is_sqlite(self) -> bool:
        """Return True if the configured database is SQLite."""
        return self.DATABASE_URL.startswith("sqlite")

    @property
    def sqlite_file_path(self) -> str:
        """Extract the filesystem path or :memory: identifier from a SQLite URL."""
        if not self.is_sqlite:
            raise ValueError("Database URL is not a SQLite URL.")
        if self.DATABASE_URL in ("sqlite:///:memory:", "sqlite://"):
            return ":memory:"
        prefix = "sqlite:///"
        if self.DATABASE_URL.startswith(prefix):
            return self.DATABASE_URL[len(prefix) :]
        return "./reconcilex.db"

    def parse_api_keys(self) -> dict[str, str]:
        """Parse OPERATOR_API_KEYS ('key:role,key2:role2') into a lookup dictionary."""
        mapping: dict[str, str] = {}
        for entry in self.OPERATOR_API_KEYS.split(","):
            entry = entry.strip()
            if not entry or ":" not in entry:
                continue
            key, role = entry.split(":", 1)
            mapping[key.strip()] = role.strip()
        return mapping


@lru_cache()
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()

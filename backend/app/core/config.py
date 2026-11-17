from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "CarbonTrace"
    environment: str = "development"
    secret_key: str = "development-only-change-me"
    access_token_expire_minutes: int = 60
    database_url: str = "postgresql+psycopg://carbontrace:carbontrace@localhost:5432/carbontrace"
    celery_broker_url: str = "redis://localhost:6379/0"
    run_jobs_async: bool = False
    cors_origins: str = "http://localhost:5173"
    max_upload_size_mb: int = 20
    upload_directory: str = "./uploads"
    report_directory: str = "./generated_reports"
    owid_data_url: str = "https://owid-public.owid.io/data/co2/owid-co2-data.csv"
    owid_data_directory: str = "./data/owid"
    llm_provider: str = "disabled"
    gemini_api_key: str | None = None
    llm_api_key: str | None = None
    llm_model: str = "gemini-2.5-flash"
    llm_embedding_model: str = "gemini-embedding-001"
    llm_request_timeout_seconds: int = 30
    llm_max_retries: int = 2
    chroma_host: str = "localhost"
    chroma_port: int = 8001

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def validate_runtime_security(self) -> "Settings":
        if self.access_token_expire_minutes < 1:
            raise ValueError("Access tokens must have a positive expiry.")
        if self.environment.lower() == "production" and (
            self.secret_key == "development-only-change-me" or len(self.secret_key) < 32
        ):
            raise ValueError("Production requires a SECRET_KEY with at least 32 characters.")
        if self.environment.lower() == "production" and not self.run_jobs_async:
            raise ValueError("Production requires RUN_JOBS_ASYNC=true so pipelines use Celery.")
        if self.llm_provider.lower() not in {"disabled", "gemini"}:
            raise ValueError("LLM_PROVIDER must be 'disabled' or 'gemini'.")
        if self.llm_request_timeout_seconds < 1:
            raise ValueError("LLM_REQUEST_TIMEOUT_SECONDS must be positive.")
        if self.llm_max_retries < 0:
            raise ValueError("LLM_MAX_RETRIES cannot be negative.")
        if not 1 <= self.chroma_port <= 65535:
            raise ValueError("CHROMA_PORT must be a valid TCP port.")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider.lower() == "gemini" and bool(self.effective_gemini_api_key)

    @property
    def effective_gemini_api_key(self) -> str | None:
        key = self.gemini_api_key or self.llm_api_key
        if not key:
            return None
        return key.strip() or None


@lru_cache
def get_settings() -> Settings:
    return Settings()

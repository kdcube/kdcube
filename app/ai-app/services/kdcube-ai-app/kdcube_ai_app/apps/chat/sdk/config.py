# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# chatbot/sdk/config.py
from __future__ import annotations
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    # API
    PORT: int = 8011
    CORS_ORIGINS: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # OpenAI
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL_ANSWER: str = "gpt-4o-mini"
    OPENAI_MODEL_CLASSIFIER: str = "gpt-4o-mini"
    OPENAI_MODEL_QUERYWRITER: str = "gpt-4o-mini"
    OPENAI_MODEL_RERANKER: str = "gpt-4o-mini"
    OPENAI_MODEL_EMBEDDING: str = "text-embedding-3-small"

    ANTHROPIC_API_KEY: str | None = None
    GOOGLE_API_KEY: str | None = Field(default=None, alias="GEMINI_API_KEY")

    # Postgres
    PGHOST: str = Field(default="localhost", alias="POSTGRES_HOST")
    PGPORT: int = Field(default=5434, alias="POSTGRES_PORT")
    PGDATABASE: str = Field(default="postgres", alias="POSTGRES_DATABASE")
    PGUSER: str = Field(default="postgres", alias="POSTGRES_USER")
    PGPASSWORD: str = Field(default="postgres", alias="POSTGRES_PASSWORD")
    PGSSL: bool = Field(default=False, alias="POSTGRES_SSL")

    # Neo4j
    NEO4J_URI: str = Field(default="bolt://neo4j:7687", alias="APP_NEO4J_URI")
    NEO4J_USER: str = Field(default="neo4j", alias="APP_NEO4J_USERNAME")
    NEO4J_PASSWORD: str = Field(default="neo4j", alias="APP_NEO4J_PASSWORD")

    # S3
    AWS_REGION: str = "us-east-1"
    AWS_S3_BUCKET: str = "your-conv-bucket"
    AWS_PROFILE: str | None = None
    AWS_SHARED_CREDENTIALS_FILE: str | None = None
    AWS_CONFIG_FILE: str | None = None

    # Redis
    REDIS_URL: str = Field(default="redis://localhost:6379/0")

    STORAGE_PATH: str | None = Field(default=None, alias="KDCUBE_STORAGE_PATH")

    TENANT: str = Field(default="home", alias="TENANT_ID")
    PROJECT: str = Field(default="default-project", alias="DEFAULT_PROJECT_NAME")
    INSTANCE_ID: str = Field(default="home-instance-1", alias="INSTANCE_ID")

    DEFAULT_MODEL_LLM: str | None = "claude-3-7-sonnet-20250219"

    # Parse comma-separated CORS into list
    @classmethod
    def model_validate_env(cls, env: dict) -> dict:
        # optional hook; or use a validator if you prefer
        v = dict(env)
        cors = v.get("CORS_ORIGINS")
        if isinstance(cors, str):
            v["CORS_ORIGINS"] = [o.strip() for o in cors.split(",") if o.strip()]
        return v

@lru_cache()
def get_settings() -> Settings:
    return Settings()

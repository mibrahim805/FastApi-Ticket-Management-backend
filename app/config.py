from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    secret_key: SecretStr = Field(
        validation_alias="SECRET_KEY",
        min_length=32,
    )
    mongo_uri: str = Field(validation_alias="MONGO_URI", min_length=1)
    mongo_db_name: str = Field(
        default="mydatabase",
        validation_alias="MONGO_DB_NAME",
    )
    jwt_algorithm: str = Field(default="HS256", validation_alias="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(
        default=60,
        ge=1,
        validation_alias="ACCESS_TOKEN_EXPIRE_MINUTES",
    )
    password_reset_expire_minutes: int = Field(
        default=15,
        ge=1,
        validation_alias="PASSWORD_RESET_EXPIRE_MINUTES",
    )
    rate_limit_enabled: bool = Field(
        default=True,
        validation_alias="RATE_LIMIT_ENABLED",
    )
    rate_limit_window_seconds: int = Field(
        default=60,
        ge=1,
        validation_alias="RATE_LIMIT_WINDOW_SECONDS",
    )
    login_rate_limit_requests: int = Field(
        default=10,
        ge=1,
        validation_alias="LOGIN_RATE_LIMIT_REQUESTS",
    )
    registration_rate_limit_requests: int = Field(
        default=5,
        ge=1,
        validation_alias="REGISTRATION_RATE_LIMIT_REQUESTS",
    )
    password_reset_rate_limit_requests: int = Field(
        default=5,
        ge=1,
        validation_alias="PASSWORD_RESET_RATE_LIMIT_REQUESTS",
    )
    trust_proxy_headers: bool = Field(
        default=False,
        validation_alias="TRUST_PROXY_HEADERS",
    )
    trusted_proxy_ips: str = Field(
        default="",
        validation_alias="TRUSTED_PROXY_IPS",
        description="Comma-separated trusted proxy IPs or CIDR networks",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

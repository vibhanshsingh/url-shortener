"""
Centralized configuration.

Why this file exists: every other option is worse. Reading os.environ
directly, scattered across the codebase, means (a) no validation until
something crashes deep in a request, (b) no single place to see what
config the app actually depends on, and (c) no type safety.

pydantic-settings gives us: env vars validated and type-coerced at
startup (fail fast, not mid-request), a single object injected wherever
config is needed, and free .env file loading for local dev.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # --- Postgres ---
    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str = "db"  # service name from docker-compose.yml, not localhost
    postgres_port: int = 5432

    # --- Redis ---
    redis_host: str = "redis"
    redis_port: int = 6379

    # --- Application ---
    # Used to build the full short_url in API responses, and to detect
    # someone trying to shorten a URL that points back at us (which
    # would create a confusing or infinite redirect chain).
    base_url: str = "http://localhost:8000"
    cors_allowed_origins: str = "http://localhost:4200"

    # --- Kafka ---
    kafka_bootstrap_servers: str = "kafka:9092"
    kafka_click_events_topic: str = "click-events"
    kafka_click_events_dlq_topic: str = "click-events-dlq"
    kafka_consumer_group_id: str = "click-event-processor"

    # --- Rate limiting ---
    # How many requests one IP address may make per 60-second window
    # before getting a 429. Kept as a setting (not a hardcoded number)
    # so it can be tuned per environment without a code change.
    rate_limit_requests_per_minute: int = 60

    @property
    def base_host(self) -> str:
        from urllib.parse import urlparse

        return urlparse(self.base_url).netloc

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


# Instantiated once, imported everywhere. FastAPI's dependency injection
# will wrap this in Milestone 3 so it's swappable in tests — for now,
# a module-level singleton is the right amount of complexity.
settings = Settings()

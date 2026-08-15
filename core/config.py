"""
Centralised configuration loaded from environment variables / .env file.
All services import `settings` from here — never read os.environ directly.
"""
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:password@localhost:5432/intelligence_db"
    )

    # ── Ollama ────────────────────────────────────────────────────────────────
    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_llm_model: str = Field(default="llama3.2")
    ollama_embed_model: str = Field(default="nomic-embed-text")

    # ── Email ─────────────────────────────────────────────────────────────────
    smtp_host: str = Field(default="smtp.gmail.com")
    smtp_port: int = Field(default=587)
    smtp_user: str = Field(default="")
    smtp_password: str = Field(default="")
    # Single recipient (kept for backward compatibility)
    recipient_email: str = Field(default="")
    # Multiple recipients — comma-separated string in .env:
    #   RECIPIENT_EMAILS=alice@example.com,bob@example.com
    recipient_emails: str = Field(default="")
    email_from_name: str = Field(default="Intelligence Briefing")
    # Test mode: when --test is passed to run_pipeline.py or send_newsletter.py
    # the email is sent only to this address and the newsletter is NOT marked as sent.
    #   TEST_EMAIL=yourname+test@gmail.com
    test_email: str = Field(default="")

    @property
    def all_recipients(self) -> List[str]:
        """Merged, deduplicated list of every configured recipient."""
        seen: set[str] = set()
        result: List[str] = []
        for addr in (
            [a.strip() for a in self.recipient_emails.split(",") if a.strip()]
            + ([self.recipient_email] if self.recipient_email.strip() else [])
        ):
            if addr not in seen:
                seen.add(addr)
                result.append(addr)
        return result

    # ── App behaviour ─────────────────────────────────────────────────────────
    app_env: str = Field(default="development")
    log_level: str = Field(default="INFO")
    profile_window_days: int = Field(default=30)
    dedup_title_threshold: float = Field(default=0.85)

    # Embedding dimension — nomic-embed-text outputs 768 floats
    embedding_dim: int = Field(default=768)

    # CORS: restrict to this list of origins in production (comma-separated in .env)
    # Example: ALLOWED_ORIGINS=["https://myapp.example.com"]
    allowed_origins: List[str] = Field(
        default=["http://localhost:8000", "http://localhost:3000"]
    )

    # ── Pipeline tuning ───────────────────────────────────────────────────────
    # All of these can be overridden in .env without touching source code.

    # Max articles collected per RSS feed per run
    feed_max_per_source: int = Field(default=20)

    # Max concurrent HTTP requests during full-text scraping
    scrape_concurrency: int = Field(default=25)

    # Articles embedded in each concurrent batch
    embed_batch_size: int = Field(default=10)

    # Minimum articles in a cluster to qualify for knowledge generation
    min_cluster_articles: int = Field(default=2)

    # Cap on how many clusters get knowledge stories per run
    # (top N by article count; rest are low-signal)
    max_knowledge_clusters: int = Field(default=20)

    # ── Security ──────────────────────────────────────────────────────────────
    # Static API key checked on every non-health request.
    # Leave empty in development to disable the check.
    # Example: API_KEY=supersecrettoken
    api_key: str = Field(default="")

    # ── Observability ─────────────────────────────────────────────────────────
    # Set LOG_FORMAT=json to emit structured JSON log lines (e.g. in production)
    log_format: str = Field(default="text")

    # ── Content quality ───────────────────────────────────────────────────────
    # Articles with scraped content shorter than this are flagged content_quality=low
    min_content_length: int = Field(default=300)

    # ── Archiving ─────────────────────────────────────────────────────────────
    # scripts/archive.py deletes articles older than this many days
    archive_keep_days: int = Field(default=90)

    # ── Unsubscribe ───────────────────────────────────────────────────────────
    # Public-facing base URL used to build one-click unsubscribe links.
    # Override in .env: PUBLIC_URL=https://your.domain.com
    public_url: str = Field(default="http://localhost:8000")
    # HMAC secret for signing unsubscribe tokens.
    # Defaults to smtp_password so existing deployments work out of the box;
    # set UNSUBSCRIBE_SECRET in .env to isolate the signing key.
    unsubscribe_secret: str = Field(default="")

    # ── Production guard ──────────────────────────────────────────────────────

    @model_validator(mode="after")
    def _require_email_config_in_production(self) -> "Settings":
        """
        Fail at startup — not after a 30-minute pipeline run — if the email
        configuration is incomplete in a production environment.
        """
        if self.app_env == "production":
            missing = []
            if not self.all_recipients:
                missing.append("RECIPIENT_EMAIL or RECIPIENT_EMAILS")
            if not self.smtp_password:
                missing.append("SMTP_PASSWORD")
            if not self.smtp_user:
                missing.append("SMTP_USER")
            if missing:
                raise ValueError(
                    f"Production environment requires these .env variables to be set: "
                    f"{', '.join(missing)}"
                )
        return self


settings = Settings()

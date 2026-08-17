"""
Centralised configuration loaded from environment variables / .env file.
All services import `settings` from here — never read os.environ directly.
"""
import logging
from typing import List
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator

# Hostnames that resolve only on the machine running the pipeline. A PUBLIC_URL
# pointing at any of these produces email links no recipient can open.
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", ""}


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

    # Cosine similarity above which a newly embedded article counts as a
    # republication of one already stored, and is dropped before clustering.
    #
    # Measured against 13,836 real articles: identical reposts and syndicated
    # copies score 0.99–1.00, while independent outlets covering the same event
    # score around 0.96. That lower group must survive — multi-source coverage
    # is what clustering looks for and what reliability grading counts. 0.985
    # also clears a recurring-column false positive observed at 0.9832.
    dedup_semantic_threshold: float = Field(default=0.985)

    # How far back to look for the earlier original.
    dedup_lookback_days: int = Field(default=7)

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

    # Echo every SQL statement. Defaults to on in development.
    #
    # This exists so that a CLI wanting quiet output can say SQL_ECHO=false
    # instead of claiming APP_ENV=production. The scripts used to do the
    # latter, which meant read-only tools ran under production validation and
    # started failing the moment a production-only check was added.
    sql_echo: bool | None = Field(default=None)

    @model_validator(mode="after")
    def _default_sql_echo_to_environment(self) -> "Settings":
        if self.sql_echo is None:
            self.sql_echo = self.app_env == "development"
        return self

    # ── Content quality ───────────────────────────────────────────────────────
    # Articles with scraped content shorter than this are flagged content_quality=low
    min_content_length: int = Field(default=300)

    # ── Archiving ─────────────────────────────────────────────────────────────
    # Retention is tiered, because the two kinds of data have very different
    # cost/value profiles:
    #
    #   articles  — raw scraped text plus a 768-dim embedding each. This is
    #               essentially all of the disk usage, and its value drops off
    #               quickly once the story has been written.
    #   stories   — the distilled output the whole pipeline exists to produce,
    #               a few KB each. Worth keeping far longer.
    #
    # A single window forced you to choose between paying to store embeddings
    # for a year or throwing away last quarter's briefings.
    #
    # ARCHIVE_KEEP_DAYS is retained as the legacy name and seeds the article
    # window when the more specific key is not set.
    archive_keep_days: int = Field(default=90)
    archive_keep_articles_days: int | None = Field(default=None)
    archive_keep_stories_days: int = Field(default=365)

    @model_validator(mode="after")
    def _default_article_retention_to_legacy_key(self) -> "Settings":
        if self.archive_keep_articles_days is None:
            self.archive_keep_articles_days = self.archive_keep_days
        return self

    # ── Unsubscribe ───────────────────────────────────────────────────────────
    # Public-facing base URL used to build one-click unsubscribe links.
    # Override in .env: PUBLIC_URL=https://your.domain.com
    public_url: str = Field(default="http://localhost:8000")
    # HMAC secret for signing unsubscribe tokens.
    # Defaults to smtp_password so existing deployments work out of the box;
    # set UNSUBSCRIBE_SECRET in .env to isolate the signing key.
    unsubscribe_secret: str = Field(default="")

    @property
    def public_url_is_reachable(self) -> bool:
        """
        True when PUBLIC_URL points somewhere a recipient could actually open.
        A loopback host resolves only on the machine running the pipeline, so
        any link built from it is dead on arrival in someone else's inbox.
        """
        return (urlparse(self.public_url).hostname or "") not in _LOOPBACK_HOSTS

    @property
    def supports_one_click_unsubscribe(self) -> bool:
        """
        RFC 8058 one-click unsubscribe requires an HTTPS endpoint that accepts
        POST. Advertising the header without one makes mail clients show an
        unsubscribe button that silently fails.
        """
        return (
            self.public_url_is_reachable
            and urlparse(self.public_url).scheme == "https"
        )

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

    @model_validator(mode="after")
    def _require_reachable_public_url_in_production(self) -> "Settings":
        """
        A localhost PUBLIC_URL is worse than a missing one: the pipeline runs
        clean, the mail sends, and every recipient gets an unsubscribe link and
        a "read online" link pointing at a host only this machine can resolve.
        Nothing surfaces the failure, so refuse to start instead.
        """
        if self.app_env != "production":
            return self
        if not self.public_url_is_reachable:
            raise ValueError(
                f"PUBLIC_URL is set to '{self.public_url}', which recipients cannot "
                "reach. Unsubscribe and 'read online' links are built from it. "
                "Set PUBLIC_URL to a publicly reachable base URL."
            )
        return self

    @model_validator(mode="after")
    def _warn_on_derived_unsubscribe_secret(self) -> "Settings":
        """
        Falling back to smtp_password still signs correctly, so this is a
        warning rather than a hard failure — but rotating the mail password
        would silently break every unsubscribe link already delivered.
        """
        if not self.unsubscribe_secret and self.smtp_password:
            logging.getLogger(__name__).warning(
                "UNSUBSCRIBE_SECRET is unset — signing unsubscribe tokens with "
                "SMTP_PASSWORD. Rotating that password will invalidate every "
                "unsubscribe link already sent. Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return self


settings = Settings()

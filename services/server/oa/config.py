from __future__ import annotations

from functools import cached_property
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OA_", env_file=(str(REPO_ROOT / ".env"),), extra="ignore")

    mode: str = "lite"  # lite | full
    data_dir: Path = REPO_ROOT / "data"
    database_url: str = ""
    redis_url: str = "redis://127.0.0.1:6379/0"  # not "localhost": see s3_endpoint

    storage: str = "local"  # local | s3
    storage_dir: Path | None = None
    # 127.0.0.1, not "localhost": on macOS/Docker Desktop each new connection to "localhost" first tries ::1 and
    # costs ~1s (measured 1.008s vs 0.006s per S3 PutObject) — it throttled snapshot/dist uploads in full mode.
    s3_endpoint: str = "http://127.0.0.1:9000"
    s3_bucket: str = "openatoms"
    s3_key: str = "oaminio"
    s3_secret: str = "oaminio123"

    sandbox_provider: str = "local"  # local | docker
    sandbox_image: str = "openatoms/sandbox:latest"
    sandboxd_bin: Path = REPO_ROOT / "services/sandboxd/bin/sandboxd"
    sandbox_idle_seconds: int = 15 * 60
    sandbox_max_running_per_workspace: int = 5
    template_dir: Path = REPO_ROOT / "templates/react-shadcn-vite"
    k8s_namespace: str = "openatoms-sandboxes"
    k8s_runtime_class: str = "gvisor"
    k8s_workspace_pvc: str = "oa-workspaces"
    k8s_run_as_user: int = 1000  # sandbox pod uid/gid; must match the api/worker uid that writes the shared PVC

    queue: str = "inproc"  # inproc | arq

    jwt_secret: str = "dev-jwt-secret-change-me"
    internal_token: str = "dev-internal-token"
    secret_key: str = "dev-secret-key"
    access_token_minutes: int = 15
    refresh_token_days: int = 30

    llm_base_url: str = "http://localhost:4000/v1"
    llm_api_key: str = ""
    default_model: str = "oa-mock"
    basic_model: str = "oa-mock"
    image_model: str = "gpt-image-1"
    adv_models: str = "claude-sonnet-4-5,gpt-5,deepseek-chat,kimi-k2"

    public_web_url: str = "http://localhost:3000"
    public_api_url: str = "http://127.0.0.1:8100"
    app_domain: str = "app.localhost:8080"
    app_scheme: str = "http"

    tavily_api_key: str = ""
    stripe_secret: str = ""
    stripe_webhook_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    supabase_client_id: str = ""
    supabase_client_secret: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    # Provider endpoints (overridable for self-hosted GitHub Enterprise, Stripe mocks, and tests)
    github_api_url: str = "https://api.github.com"
    github_oauth_url: str = "https://github.com"
    github_git_url: str = "https://github.com"
    supabase_api_url: str = "https://api.supabase.com"
    stripe_api_url: str = "https://api.stripe.com"
    google_auth_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    google_token_url: str = "https://oauth2.googleapis.com/token"
    google_userinfo_url: str = "https://openidconnect.googleapis.com/v1/userinfo"
    smtp_host: str = ""
    smtp_port: int = 1025
    mail_from: str = "OpenAtoms <noreply@openatoms.local>"

    signup_credits: int = 500
    # Return one-time auth tokens (password reset) in API responses so local dev works without a mailbox.
    # Anyone who knows an email could then reset that account's password: never enable on a reachable deployment.
    dev_expose_tokens: bool = False

    @cached_property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        if self.mode == "full":
            return "postgresql+asyncpg://oa:oa@127.0.0.1:5432/oa"
        return f"sqlite+aiosqlite:///{self.data_dir / 'oa.db'}"

    @property
    def is_lite(self) -> bool:
        return self.mode != "full"

    @property
    def use_redis(self) -> bool:
        return self.mode == "full"

    @cached_property
    def storage_root(self) -> Path:
        return self.storage_dir or (self.data_dir / "storage")

    def chat_dir(self, chat_id: str) -> Path:
        return self.data_dir / "chats" / chat_id

    def workspace_dir(self, chat_id: str) -> Path:
        return self.chat_dir(chat_id) / "workspace"

    def preview_url(self, chat_id: str, port: int = 5173) -> str:
        return f"{self.app_scheme}://{port}-{chat_id}-preview.{self.app_domain}"

    def version_url(self, user_id: int, chat_id: str, version: str, seq: int = 0) -> str:
        return f"{self.app_scheme}://{user_id}-{chat_id}-{seq}-{version}.{self.app_domain}"

    @property
    def adv_model_list(self) -> list[str]:
        return [m.strip() for m in self.adv_models.split(",") if m.strip()]


settings = Settings()

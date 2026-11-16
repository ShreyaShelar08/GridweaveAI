"""Central configuration. Values come from environment variables / .env file,
so secrets are never hard-coded in the repository."""
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET = "dev-only-change-me"


class Settings(BaseSettings):
    app_name: str = "GridWeave AI Backend"
    environment: str = "development"  # development | production
    secret_key: str = DEFAULT_SECRET  # override in .env for any real deployment
    algorithm: str = "HS256"

    # --- authentication ---
    access_token_expire_minutes: int = 15      # short-lived; client renews with the refresh token
    refresh_token_expire_days: int = 7
    max_failed_logins: int = 5                 # wrong passwords before the account is locked
    lockout_minutes: int = 15

    # --- database ---
    database_url: str = "sqlite:///./gridweave.db"
    auto_create_tables: bool = False           # True only for quick local/test runs; real DBs use Alembic

    tax_percent: float = 0.0  # electricity is generally outside GST in India; configurable anyway
    log_level: str = "INFO"
    log_dir: str = "logs"
    # --- payments ---
    payment_mode: str = "test"        # "test" = built-in simulator | "razorpay" = real Razorpay API
    razorpay_key_id: str = ""         # rzp_test_xxx while developing, rzp_live_xxx in production
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    payment_terms_days: int = 15      # invoice due this many days after issue
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def _production_safety(self):
        if self.environment == "production" and (self.secret_key == DEFAULT_SECRET or len(self.secret_key) < 32):
            raise ValueError("In production SECRET_KEY must be a random string of at least 32 characters")
        if self.payment_mode == "razorpay" and not (
                self.razorpay_key_id and self.razorpay_key_secret and self.razorpay_webhook_secret):
            raise ValueError("PAYMENT_MODE=razorpay needs RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET "
                             "and RAZORPAY_WEBHOOK_SECRET")
        return self


settings = Settings()

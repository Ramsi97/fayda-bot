from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    # Telegram Bot Settings
    TELEGRAM_TOKEN: str
    WEBHOOK_URL: str
    API_BASE_URL: str = "https://api.telegram.org"
    BOT_NAME: str = "National ID converter"
    
    AUTHORIZED_USER_IDS: str = ""
    SUPER_ADMIN_IDS: str = ""
    REQUIRED_GROUP_ID: int = 0  # Set to -100xxx... and etc to restrict bot to members

    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    
    UPLOAD_DIR: Path = BASE_DIR / "storage" / "uploads"
    OUTPUT_DIR: Path = BASE_DIR / "storage" / "outputs"

    # ── Billing / points system ───────────────────────────────────────────────
    # MongoDB connection. Balances live in MongoDB (not a container file), so
    # they survive restarts/redeploys and work across multiple instances. Point
    # MONGODB_URI at a managed cluster (e.g. MongoDB Atlas) in production — the
    # localhost default is only for local development (see AZURE_DEPLOYMENT.md).
    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_DB: str = "fayda_bot"
    # Price (whole Birr) debited from a user's balance per generated ID card.
    PRICE_PER_DOWNLOAD: int = 10
    # Balance (Birr) auto-granted to a user the first time they're seen.
    FREE_SIGNUP_BALANCE: int = 0
    # Where deposit receipts are forwarded for approval. If 0, each authorized
    # user is DMed instead (they must have started a chat with the bot first).
    ADMIN_CHAT_ID: int = 0

    # Pydantic V2 configuration style
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",           # Important: ignores extra fields in .env
        protected_namespaces=(),  # Stops the "model_" warning
        case_sensitive=True
    )
    
    @property
    def super_admin_ids_ordered(self) -> list[int]:
        """Return super admin IDs in order (first = primary). Treats SUPER_ADMIN_IDS as canonical, falling back to AUTHORIZED_USER_IDS."""
        raw = self.SUPER_ADMIN_IDS or self.AUTHORIZED_USER_IDS
        if not raw:
            return []
        res = []
        for uid in raw.split(","):
            uid = uid.strip()
            if uid:
                try:
                    val = int(uid)
                    if val not in res:
                        res.append(val)
                except ValueError:
                    pass
        return res

    @property
    def super_admins(self) -> set[int]:
        """Return super admin IDs as a set of integers."""
        return set(self.super_admin_ids_ordered)

    @property
    def authorized_users(self) -> set[int]:
        """Legacy alias for super_admins."""
        return self.super_admins

settings = Settings()

# Ensure directories exist
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
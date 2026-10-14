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
    # VIP_IDS: comma-separated Telegram user IDs that get FREE access without
    # reseller capabilities. They can generate IDs for themselves at no cost but
    # cannot invite members, set prices, or approve deposits.
    # Great for listing a whole team / group as free users without making them
    # resellers. Example: VIP_IDS=111111111,222222222,333333333
    VIP_IDS: str = ""

    # SUPPORT_USERNAME: public Telegram @username shown to uninvited/blocked users
    # so they know who to message for an access link (typically the super admin).
    # Stored with or without a leading '@'. Empty → a generic "administrator"
    # fallback is shown instead.
    SUPPORT_USERNAME: str = ""

    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    
    UPLOAD_DIR: Path = BASE_DIR / "storage" / "uploads"
    OUTPUT_DIR: Path = BASE_DIR / "storage" / "outputs"

    # ── Billing / points system ───────────────────────────────────────────────
    # Postgres (Supabase) connection. Balances live in Postgres (not a container
    # file), so they survive restarts/redeploys and work across multiple
    # instances. Point DATABASE_URL at a managed Postgres (e.g. Supabase — use the
    # Session pooler connection string) in production; the localhost default is
    # only for local development (see AZURE_DEPLOYMENT.md).
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/postgres"
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

    @property
    def vip_user_ids(self) -> list[int]:
        """Return VIP user IDs in order from VIP_IDS env var.

        VIP users get free access with no reseller capabilities. You can list
        the whole members of a team/group here and they will all use the bot
        for free without needing an invite link.
        """
        if not self.VIP_IDS:
            return []
        res = []
        for uid in self.VIP_IDS.split(","):
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
    def vip_users(self) -> set[int]:
        """Return VIP user IDs as a set."""
        return set(self.vip_user_ids)

    @property
    def support_contact(self) -> str:
        """Human-facing contact handle for 'no access' messages.

        Returns a tappable `@username` when SUPPORT_USERNAME is configured, else
        the generic phrase "the administrator". Any leading '@'/whitespace is
        stripped so the value works whether or not the env var includes the '@'.
        """
        handle = self.SUPPORT_USERNAME.strip().lstrip("@").strip()
        return f"@{handle}" if handle else "the administrator"

settings = Settings()

# Ensure directories exist
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
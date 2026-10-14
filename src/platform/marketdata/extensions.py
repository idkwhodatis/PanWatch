"""Optional market capabilities, separate from legacy execution markets.

Configuration records consent to enable an installed extension. It never grants
capabilities which are not implemented, and reading this registry has no writes.
"""
from sqlalchemy.orm import Session

from src.platform.persistence.models import AppSettings
from src.web.errors import api_error

CAPABILITIES = ("quotes", "klines", "search", "watchlist", "holdings",
                "analysis", "scanning", "paper_trading")
EXTENSIONS = {
    "canada": {"exchanges": ("TSX", "TSXV"), "capabilities": ()},
}


def extension_status(db: Session, extension_id: str) -> dict:
    spec = EXTENSIONS.get(extension_id)
    if spec is None:
        raise api_error(404, "extension_not_found", "Unknown market extension")
    setting = db.query(AppSettings).filter(AppSettings.key == f"market_extension_{extension_id}_enabled").first()
    return {
        "id": extension_id,
        "enabled": setting is not None and setting.value == "true",
        "available": bool(spec["capabilities"]),
        "exchanges": list(spec["exchanges"]),
        "capabilities": {name: name in spec["capabilities"] for name in CAPABILITIES},
    }


def set_extension_enabled(db: Session, extension_id: str, enabled: bool) -> dict:
    extension_status(db, extension_id)  # Reject unknown IDs before writing.
    key = f"market_extension_{extension_id}_enabled"
    setting = db.query(AppSettings).filter(AppSettings.key == key).first()
    if setting is None:
        setting = AppSettings(key=key, description="Optional market extension")
        db.add(setting)
    setting.value = "true" if enabled else "false"
    db.commit()
    return extension_status(db, extension_id)


def require_extension(db: Session, extension_id: str, capability: str) -> dict:
    status = extension_status(db, extension_id)
    if not status["enabled"]:
        raise api_error(403, "extension_disabled", "Market extension is disabled")
    if not status["capabilities"].get(capability, False):
        raise api_error(409, "extension_capability_unavailable", "Market capability is not installed")
    return status

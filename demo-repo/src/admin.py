"""Admin dashboard — depends on everything. Maximum blast radius target."""

from src.auth import validate_token, check_permissions
from src.users import get_user
from src.orders import create_order
from src.billing import charge
from src.notifications import send_email


def dashboard(admin_token: str) -> dict:
    """Render admin dashboard data."""
    validate_token(admin_token)
    check_permissions("admin", "dashboard:read")
    return {
        "total_users": 1000,
        "total_orders": 5000,
        "revenue": 250000.00,
    }


def audit_log(admin_token: str, action: str, details: str) -> dict:
    """Log an admin action."""
    validate_token(admin_token)
    return {"action": action, "details": details, "timestamp": "2026-08-20T00:00:00Z"}

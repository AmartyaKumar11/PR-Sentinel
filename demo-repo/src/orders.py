"""Order management — depends on auth and users."""

from src.auth import validate_token
from src.users import get_user
from src.notifications import send_email


def create_order(user_id: str, token: str, items: list, quantity: int) -> dict:
    """Create a new order."""
    validate_token(token)
    user = get_user(user_id, token)

    if quantity <= 0:
        raise ValueError("Quantity must be positive")

    order = {
        "id": "order-123",
        "user_id": user_id,
        "items": items,
        "quantity": quantity,
        "status": "created",
    }

    send_email(user["email"], "Order confirmation", f"Order {order['id']} created.")
    return order


def cancel_order(order_id: str, token: str, reason: str = "") -> dict:
    """Cancel an existing order."""
    validate_token(token)
    return {"id": order_id, "status": "cancelled", "reason": reason}

# PR Sentinel — Demo Repository Specification

> **Repo name:** `pr-sentinel-demo`  
> **Purpose:** A small Python app with intentional structures designed to produce impressive demo results.  
> **This is NOT the PR Sentinel codebase.** It's the repo PR Sentinel monitors.

---

## Source Files

### src/auth.py

```python
"""Authentication module — the most connected module in the app."""

import hashlib
import secrets
from datetime import datetime


def validate_token(token: str) -> dict:
    """Validate a JWT-like token. Called by users, orders, and admin."""
    if not token or len(token) < 10:
        raise ValueError("Invalid token")
    # Simplified validation
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Malformed token")
    return {"user_id": parts[1], "valid": True}


def hash_password(password: str, salt: str = None) -> tuple[str, str]:
    """Hash a password with a salt."""
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return hashed, salt


def generate_reset_token(user_id: str) -> str:
    """Generate a password reset token."""
    token = secrets.token_urlsafe(32)
    # In a real app, this would be stored in a DB with an expiry
    return token


def check_permissions(user_id: str, resource: str) -> bool:
    """Check if a user has access to a resource."""
    # Simplified — always returns True for demo
    return True
```

### src/users.py

```python
"""User management — depends on auth module."""

from src.auth import validate_token, hash_password, check_permissions


def get_user(user_id: str, token: str) -> dict:
    """Get user profile. Validates token first."""
    validate_token(token)
    return {
        "id": user_id,
        "name": f"User {user_id}",
        "email": f"user{user_id}@example.com",
    }


def create_user(name: str, email: str, password: str) -> dict:
    """Create a new user account."""
    hashed, salt = hash_password(password)
    return {
        "id": "new-user-id",
        "name": name,
        "email": email,
        "password_hash": hashed,
        "salt": salt,
    }


def update_profile(user_id: str, token: str, updates: dict) -> dict:
    """Update user profile fields."""
    validate_token(token)
    check_permissions(user_id, "profile:write")
    user = get_user(user_id, token)
    user.update(updates)
    return user
```

### src/orders.py

```python
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
```

### src/notifications.py

```python
"""Notification service — called by orders and users. NO TEST FILE EXISTS."""

import logging

logger = logging.getLogger(__name__)


def send_email(to: str, subject: str, body: str) -> bool:
    """Send an email notification."""
    logger.info(f"Sending email to {to}: {subject}")
    # In a real app, this would call an email API
    return True


def send_sms(phone: str, message: str) -> bool:
    """Send an SMS notification."""
    logger.info(f"Sending SMS to {phone}: {message}")
    return True
```

### src/billing.py

```python
"""Billing module — depends on orders. NO TEST FILE EXISTS."""

from src.orders import create_order


def charge(user_id: str, token: str, amount: float, currency: str = "USD") -> dict:
    """Charge a user's payment method."""
    if amount <= 0:
        raise ValueError("Amount must be positive")
    return {
        "transaction_id": "txn-456",
        "user_id": user_id,
        "amount": amount,
        "currency": currency,
        "status": "charged",
    }


def refund(transaction_id: str, amount: float) -> dict:
    """Refund a transaction."""
    return {
        "transaction_id": transaction_id,
        "refund_amount": amount,
        "status": "refunded",
    }
```

### src/admin.py

```python
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
```

---

## Test Files

### tests/test_auth.py

```python
from src.auth import validate_token, hash_password, generate_reset_token

def test_validate_token_valid():
    result = validate_token("header.userid123.signature")
    assert result["valid"] is True

def test_validate_token_invalid():
    try:
        validate_token("")
    except ValueError:
        pass

def test_hash_password():
    hashed, salt = hash_password("mypassword")
    assert len(hashed) == 64
    assert len(salt) == 32

def test_generate_reset_token():
    token = generate_reset_token("user-1")
    assert len(token) > 20
```

### tests/test_users.py

```python
from src.users import get_user, create_user

def test_get_user():
    user = get_user("123", "header.123.signature")
    assert user["id"] == "123"

def test_create_user():
    user = create_user("Test", "test@example.com", "password123")
    assert user["name"] == "Test"
    assert "password_hash" in user
```

### tests/test_orders.py

```python
from src.orders import create_order, cancel_order

def test_create_order():
    order = create_order("user-1", "header.user-1.sig", ["item-a"], quantity=2)
    assert order["status"] == "created"

def test_create_order_negative_quantity():
    try:
        create_order("user-1", "header.user-1.sig", ["item-a"], quantity=-1)
    except ValueError as e:
        assert "positive" in str(e)

def test_cancel_order():
    result = cancel_order("order-123", "header.user-1.sig", reason="changed mind")
    assert result["status"] == "cancelled"
```

**Intentionally missing:** `test_notifications.py` and `test_billing.py` — this creates untested impacted code that the blast radius tracer can flag.

---

## Dependency Graph (Expected Output)

```
auth.validate_token ◄── users.get_user ◄── orders.create_order ◄── billing.charge
                    ◄── users.update_profile                    ◄── admin.dashboard
                    ◄── orders.cancel_order
                    ◄── admin.dashboard
                    ◄── admin.audit_log

auth.hash_password  ◄── users.create_user
auth.check_permissions ◄── users.update_profile ◄── admin.dashboard

notifications.send_email ◄── orders.create_order
                         ◄── admin (imported but not called in demo)
```

---

## 4 Scenario Branches

Create these branches with the corresponding changes. Each has a matching GitHub issue.

### Branch: `feature/1-password-reset`

**GitHub Issue #1:** "Add password reset: must validate email format, send reset email, expire token after 1 hour"

**Changes in the branch:**
- `src/auth.py`: Add `reset_password(email: str)` function that calls `generate_reset_token` and `send_email` — but does NOT validate email format and does NOT set token expiry.

```python
# Add to src/auth.py
def reset_password(email: str) -> dict:
    """Reset a user's password."""
    # BUG: No email format validation
    # BUG: No token expiry
    token = generate_reset_token(email)
    from src.notifications import send_email
    send_email(email, "Password Reset", f"Your reset token: {token}")
    return {"email": email, "token": token, "status": "sent"}
```

### Branch: `fix/2-order-validation`

**GitHub Issue #2:** "Reject negative quantities in create_order"

**Changes in the branch:**
- `src/orders.py`: Fix the validation (already exists in demo, so this is a no-op or tighten it)
- `src/notifications.py`: ALSO refactor `send_email` to add a `cc` parameter (scope creep)

### Branch: `feature/no-issue-billing`

**No GitHub Issue** — this is a phantom PR.

**Changes in the branch:**
- `src/billing.py`: Add a `create_invoice` function

### Branch: `docs/update-readme`

**No GitHub Issue needed.**

**Changes in the branch:**
- Only modifies `README.md`

---

## Setup Script

```bash
#!/bin/bash
# Run from the pr-sentinel-demo repo root

# Create main branch structure
mkdir -p src tests
# Copy all source files from above
# Push to GitHub

# Create scenario branches
git checkout -b feature/1-password-reset
# Make changes to src/auth.py
git add . && git commit -m "Add password reset (missing validation)"
git push -u origin feature/1-password-reset

git checkout main
git checkout -b fix/2-order-validation
# Make changes to src/orders.py and src/notifications.py
git add . && git commit -m "Fix order validation + refactor notifications"
git push -u origin fix/2-order-validation

git checkout main
git checkout -b feature/no-issue-billing
# Add create_invoice to billing.py
git add . && git commit -m "Add invoice generation"
git push -u origin feature/no-issue-billing

git checkout main
git checkout -b docs/update-readme
# Edit README.md
git add . && git commit -m "Update README"
git push -u origin docs/update-readme

git checkout main
echo "All 4 scenario branches created."
```

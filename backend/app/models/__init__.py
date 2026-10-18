"""Import every model so its table registers on Base.metadata.

Alembic's env.py and the application must import `app.models` (not just one
module) before reading Base.metadata; otherwise tables would be missing.
"""

from app.models.membership import ROLE_MEMBER, ROLE_OWNER, Membership
from app.models.organization import Organization
from app.models.user import User
from app.models.user_session import UserSession

__all__ = [
    "ROLE_MEMBER",
    "ROLE_OWNER",
    "Membership",
    "Organization",
    "User",
    "UserSession",
]
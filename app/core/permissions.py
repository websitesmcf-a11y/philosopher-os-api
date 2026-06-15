"""Role-based permissions for Socrates AI."""
from enum import Enum


class Role(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MANAGER = "manager"
    AGENT = "agent"
    MEMBER = "member"


# Permission definitions
PERMISSIONS = {
    Role.OWNER: ["*"],
    Role.ADMIN: [
        "leads:*", "clients:*", "campaigns:*", "messages:*",
        "finance:*", "analytics:*", "calendar:*", "tasks:*",
        "automation:*", "knowledge:*", "agents:*", "notifications:*",
        "users:read", "users:update", "settings:*",
    ],
    Role.MANAGER: [
        "leads:read", "leads:create", "leads:update", "leads:delete",
        "clients:read", "clients:create", "clients:update",
        "campaigns:read", "campaigns:create", "campaigns:update",
        "messages:read", "messages:write",
        "finance:read", "analytics:read",
        "calendar:read", "calendar:create", "calendar:update",
        "tasks:read", "tasks:create", "tasks:update",
        "knowledge:read", "knowledge:create",
    ],
    Role.AGENT: [
        "leads:read", "leads:update",
        "clients:read",
        "messages:read", "messages:write",
        "tasks:read", "tasks:update",
        "memory:read", "memory:write",
    ],
    Role.MEMBER: [
        "leads:read", "leads:create",
        "messages:read",
        "tasks:read", "tasks:update",
        "analytics:read",
    ],
}


def check_permission(user_role: str, required: str) -> bool:
    """Check if a user role has a specific permission."""
    role = Role(user_role.lower())
    perms = PERMISSIONS.get(role, [])
    if "*" in perms:
        return True
    if required in perms:
        return True
    # Check wildcard: "leads:*" matches "leads:read"
    resource = required.split(":")[0] if ":" in required else required
    for p in perms:
        if p == f"{resource}:*":
            return True
    return False

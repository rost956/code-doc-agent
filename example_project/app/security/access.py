class User:
    def __init__(self, role):
        self.role = role


def require_execute_access(user, action):
    """Checks whether user can execute selected action."""
    permissions = get_user_permissions(user)
    if action not in permissions:
        raise PermissionError("Access denied")
    return True


def get_user_permissions(user):
    """Returns permissions for user role."""
    if user.role == "admin":
        return ["create", "read", "update", "delete", "execute"]
    return ["read"]

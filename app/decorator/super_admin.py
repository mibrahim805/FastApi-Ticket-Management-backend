from functools import wraps
from fastapi import HTTPException

def super_admin_required(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        user = kwargs.get("current_user")

        if not user:
            raise HTTPException(
                status_code=401,
                detail="User not authenticated"
            )

        if user.get("role") != "super_admin":
            raise HTTPException(
                status_code=403,
                detail="Only super_admin can perform this action"
            )

        return await func(*args, **kwargs)

    return wrapper
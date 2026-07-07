import inspect
from functools import wraps

from fastapi import HTTPException


def super_admin_required(func):
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        user = kwargs.get("current_user")

        if not user:
            raise HTTPException(status_code=401, detail="User not authenticated")

        if user.get("role") != "super_admin":
            raise HTTPException(
                status_code=403, detail="Only super_admin can perform this action"
            )

        return await func(*args, **kwargs)

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        user = kwargs.get("current_user")

        if not user:
            raise HTTPException(status_code=401, detail="User not authenticated")

        if user.get("role") != "super_admin":
            raise HTTPException(
                status_code=403, detail="Only super_admin can perform this action"
            )

        return func(*args, **kwargs)

    if inspect.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper

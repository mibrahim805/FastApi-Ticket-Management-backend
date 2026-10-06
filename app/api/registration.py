from bson import ObjectId
from fastapi import APIRouter, HTTPException
from fastapi.params import Depends
from pymongo.errors import DuplicateKeyError

from app.api.auth import (
    consume_password_reset_token,
    create_access_token,
    create_password_reset_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.api.permissions import (
    ADMIN_ROLES,
    SUPER_ADMIN_ROLES,
    ensure_role,
    parse_object_id,
    require_role,
)
from app.api.rate_limit import (
    login_rate_limit,
    password_reset_rate_limit,
    registration_rate_limit,
)
from app.db.db import reset_tokens_collection, user_collection
from app.models.registration import (
    AdminRegister,
    ChangeGlobalRole,
    ChangePasswordRequest,
    GlobalRole,
    PasswordResetRequest,
    ResetPasswordRequest,
    SuperAdminRegister,
    UserLogin,
    UserManagementCreate,
    UserRegister,
    UserUpdate,
)

router = APIRouter(tags=["Auth"])

MIN_PASSWORD_LENGTH = 12

_require_super_admin = require_role(
    *SUPER_ADMIN_ROLES,
    detail="Only super admin can register an admin",
)
_require_admin = require_role(
    *ADMIN_ROLES,
    detail="Only super admin or admin can activate or deactivate users",
)


@router.post(
    "/register_super_admin",
    tags=["Auth"],
    dependencies=[Depends(registration_rate_limit)],
)
def register_super_admin(user: SuperAdminRegister):
    hashed_password = hash_password(user.password)
    super_admin_user = {
        "username": user.name,
        "email": user.email,
        "email_key": user.email.casefold(),
        "hashed_password": hashed_password,
        "role": "super_admin",
        "is_active": True,
    }

    # The partial unique index on users.role is the synchronization primitive
    # for first-admin setup. The insert and the constraint check therefore
    # happen atomically in MongoDB; the loser of a concurrent setup request
    # receives DuplicateKeyError instead of creating a second super admin.
    try:
        user_collection.insert_one(super_admin_user)
    except DuplicateKeyError:
        return {"message": "An super admin already exists."}

    return {"message": "Super admin registered successfully"}


@router.post(
    "/register",
    tags=["Auth"],
    dependencies=[Depends(registration_rate_limit)],
)
def register(user: UserRegister):
    existing_email = user_collection.find_one({"email": user.email})

    if existing_email:
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed_password = hash_password(user.password)
    user_data = {
        "name": user.name,
        "email": user.email,
        "email_key": user.email.casefold(),
        "hashed_password": hashed_password,
        "is_active": user.is_active,
    }

    try:
        user_collection.insert_one(user_data)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Email already registered")
    return {"message": "User registered successfully"}


@router.post("/register_admin", tags=["Auth"])
def register_admin(
    user: AdminRegister,
    current_user: dict = Depends(_require_super_admin),
):
    ensure_role(
        current_user,
        SUPER_ADMIN_ROLES,
        detail="Only super admin can register an admin",
    )

    existing_email = user_collection.find_one({"email": user.email})

    if existing_email:
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed_password = hash_password(user.password)
    user_data = {
        "name": user.name,
        "email": user.email,
        "email_key": user.email.casefold(),
        "hashed_password": hashed_password,
        "role": GlobalRole.ADMIN.value,
        "is_active": user.is_active,
    }

    try:
        user_collection.insert_one(user_data)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Email already registered")
    return {"message": "Admin registered successfully"}


@router.post(
    "/login",
    tags=["Auth"],
    dependencies=[Depends(login_rate_limit)],
)
def login(user: UserLogin):
    db_user = user_collection.find_one({"email": user.email})
    if not db_user or not verify_password(
        user.password, db_user.get("hashed_password", "")
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not db_user["is_active"]:
        raise HTTPException(
            status_code=401,
            detail="Your account has been deactivated. Please contact support.",
        )
    access_token = create_access_token(
        {
            "sub": str(db_user["_id"]),
            "token_version": db_user.get("token_version", 0),
        }
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", tags=["Auth"])
def get_profile(current_user: dict = Depends(get_current_user)):
    return {
        "user_id": str(current_user["_id"]),
        "username": current_user.get("username") or current_user.get("name") or "User",
        "email": current_user.get("email"),
        "role": current_user.get("role"),
    }


@router.get("/users", tags=["Auth"])
def list_users(current_user: dict = Depends(_require_super_admin)):
    ensure_role(
        current_user,
        SUPER_ADMIN_ROLES,
        detail="Only super admin can manage users",
    )
    users = []
    for user in user_collection.find({}, {"hashed_password": 0, "token_version": 0}):
        users.append(
            {
                "id": str(user["_id"]),
                "name": user.get("username") or user.get("name") or "Unnamed user",
                "email": user.get("email", ""),
                "role": user.get("role"),
                "is_active": user.get("is_active", True),
            }
        )
    return {"users": users}


@router.post("/users", tags=["Auth"])
def create_managed_user(
    user: UserManagementCreate,
    current_user: dict = Depends(_require_super_admin),
):
    ensure_role(
        current_user,
        SUPER_ADMIN_ROLES,
        detail="Only super admin can manage users",
    )
    email = str(user.email)
    if user_collection.find_one(
        {"$or": [{"email_key": email.casefold()}, {"email": email}]}
    ):
        raise HTTPException(status_code=400, detail="Email already registered")

    user_data = {
        "name": user.name,
        "email": email,
        "email_key": email.casefold(),
        "hashed_password": hash_password(user.password),
        "is_active": user.is_active,
    }
    if user.role is not None:
        user_data["role"] = user.role.value

    try:
        user_collection.insert_one(user_data)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Email already registered")
    return {"message": "User created successfully"}


@router.put("/activate_or_deactivate/{user_id}", tags=["Auth"])
def activate_or_deactivate(
    user_id: str,
    current_user: dict = Depends(_require_admin),
):
    ensure_role(
        current_user,
        ADMIN_ROLES,
        detail="Only super admin or admin can activate or deactivate users",
    )
    target_id = parse_object_id(user_id, "user_id")
    user = user_collection.find_one({"_id": target_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.get("role") == GlobalRole.SUPER_ADMIN.value:
        raise HTTPException(
            status_code=403, detail="Cannot activate or deactivate super admin"
        )

    user_collection.update_one(
        {"_id": target_id}, {"$set": {"is_active": not user["is_active"]}}
    )

    if user["is_active"]:
        return {"message": "User deactivated successfully"}
    else:
        return {"message": "User activated successfully"}


@router.put("/change_role/{user_id}", tags=["Auth"])
def change_user_role(
    user_id: str,
    payload: ChangeGlobalRole,
    current_user: dict = Depends(_require_super_admin),
):
    """Change a user's global role; project membership remains unchanged."""
    ensure_role(
        current_user,
        SUPER_ADMIN_ROLES,
        detail="Only super admin can change user roles",
    )
    try:
        target_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    target = user_collection.find_one({"_id": target_id})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.get("role") == GlobalRole.SUPER_ADMIN.value:
        raise HTTPException(status_code=403, detail="Cannot change super admin role")
    if payload.role == GlobalRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Cannot assign super admin role")

    if payload.role is None:
        user_collection.update_one({"_id": target_id}, {"$unset": {"role": ""}})
    else:
        user_collection.update_one(
            {"_id": target_id},
            {"$set": {"role": payload.role.value}},
        )
    return {
        "message": "User role updated successfully",
        "user_id": user_id,
        "role": payload.role.value if payload.role else None,
    }


@router.put("/update_yourself/", tags=["Auth"])
def update_yourself(user: UserUpdate, current_user: dict = Depends(get_current_user)):
    db_user = user_collection.find_one({"_id": ObjectId(current_user["_id"])})
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    update_data = user.model_dump(exclude_unset=True)

    if "password" in update_data:
        update_data["hashed_password"] = hash_password(update_data.pop("password"))
        update_data["token_version"] = db_user.get("token_version", 0) + 1

    user_collection.update_one({"_id": db_user["_id"]}, {"$set": update_data})
    return {"message": "User updated successfully"}


@router.put("/change_password/", tags=["Auth"])
def change_password(
    payload: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user),
):
    old_password, new_password = payload.old_password, payload.new_password
    db_user = user_collection.find_one(ObjectId(current_user["_id"]))
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    if not verify_password(old_password, db_user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Old password is incorrect")
    if old_password == new_password:
        raise HTTPException(
            status_code=400,
            detail="New password cannot be the same as the old password",
        )
    hashed_new_password = hash_password(new_password)
    user_collection.update_one(
        {"_id": db_user["_id"]},
        {
            "$set": {
                "hashed_password": hashed_new_password,
                "token_version": db_user.get("token_version", 0) + 1,
            }
        },
    )
    return {"message": "Password changed successfully"}


@router.post(
    "/request-password-reset",
    dependencies=[Depends(password_reset_rate_limit)],
)
def request_password_reset(
    payload: PasswordResetRequest,
):
    user = user_collection.find_one({"email": payload.email})

    if not user:
        return {"message": "If the email exists, a reset link has been sent"}

    create_password_reset_token(
        reset_tokens_collection=reset_tokens_collection, user=user
    )

    return {"message": "If the email exists, a reset link has been sent"}


@router.post(
    "/reset-password",
    dependencies=[Depends(password_reset_rate_limit)],
)
def reset_password(
    payload: ResetPasswordRequest,
):
    token, new_password = payload.token, payload.new_password
    record = consume_password_reset_token(reset_tokens_collection, token)

    if not record:
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    hashed_password = hash_password(new_password)

    user_collection.update_one(
        {"_id": ObjectId(record["user_id"])},
        {
            "$set": {
                "hashed_password": hashed_password,
                "token_version": user_collection.find_one(
                    {"_id": ObjectId(record["user_id"])}
                ).get("token_version", 0)
                + 1,
            }
        },
    )
    reset_tokens_collection.update_many(
        {
            "user_id": record["user_id"],
            "used": False,
        },
        {"$set": {"used": True}},
    )

    return {"message": "Password reset successful"}

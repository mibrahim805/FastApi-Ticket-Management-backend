from bson import ObjectId
from fastapi import APIRouter, HTTPException
from fastapi.params import Depends

from app.api.auth import (
    create_access_token,
    create_password_reset_token,
    get_current_user,
    hash_password,
    verify_password,
    verify_password_reset_token,
)
from app.db.db import reset_tokens_collection, user_collection
from app.models.registration import (
    Role,
    SuperAminRegister,
    UserLogin,
    UserRegister,
    UserUpdate,
)

router = APIRouter(tags=["Auth"])


@router.post("/register_super_admin", tags=["Auth"])
def register_super_admin(user: SuperAminRegister):
    existing_admin = user_collection.find_one({"role": "super_admin"})
    if existing_admin:
        return {"message": "An super admin already exists."}

    hashed_password = hash_password(user.password)
    super_admin_user = {
        "username": user.name,
        "email": user.email,
        "hashed_password": hashed_password,
        "role": "super_admin",
        "is_active": True,
    }
    user_collection.insert_one(super_admin_user)
    return {"message": "Super admin registered successfully"}


@router.post("/register", tags=["Auth"])
def register(user: UserRegister):
    existing_email = user_collection.find_one({"email": user.email})
    allowed_users = {Role.SUPER_ADMIN.value, Role.ADMIN.value, Role.AGENT.value}
    valid_roles = {role.value for role in Role}

    if existing_email:
        raise HTTPException(status_code=400, detail="Email already registered")

    if user.role not in valid_roles:
        raise HTTPException(status_code=400, detail="Invalid role")

    if user.role in allowed_users:
        raise HTTPException(
            status_code=403,
            detail=" Only super admin can register an admin or agent",
        )

    hashed_password = hash_password(user.password)
    user_data = {
        "name": user.name,
        "email": user.email,
        "hashed_password": hashed_password,
        "role": user.role,
        "is_active": user.is_active,
    }

    user_collection.insert_one(user_data)
    return {"message": "User registered successfully"}


@router.post("/register_admin_or_agent", tags=["Auth"])
def register_admin_or_agent(
    user: UserRegister, current_user: dict = Depends(get_current_user)
):
    if current_user["role"] != "super_admin":
        raise HTTPException(
            status_code=403,
            detail="Only super admin can register an admin or agent",
        )

    existing_email = user_collection.find_one({"email": user.email})
    allowed_roles = {Role.ADMIN.value, Role.AGENT.value}

    if existing_email:
        raise HTTPException(status_code=400, detail="Email already registered")

    if user.role not in allowed_roles:
        raise HTTPException(
            status_code=400,
            detail="Invalid role. Only 'admin' or 'agent' roles are allowed.",
        )

    hashed_password = hash_password(user.password)
    user_data = {
        "name": user.name,
        "email": user.email,
        "hashed_password": hashed_password,
        "role": user.role,
        "is_active": user.is_active,
    }

    user_collection.insert_one(user_data)
    return {"message": f"{user.role.capitalize()} registered successfully"}


@router.post("/login", tags=["Auth"])
def login(user: UserLogin):
    db_user = user_collection.find_one({"email": user.email})
    if not db_user:
        raise HTTPException(status_code=401, detail="Email is not registered")
    if not verify_password(user.password, db_user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Wrong password")
    if not db_user["is_active"]:
        raise HTTPException(
            status_code=401,
            detail="Your account has been deactivated. Please contact support.",
        )
    access_token = create_access_token({"sub": str(db_user["_id"])})
    return {"access_token": access_token, "token_type": "bearer"}


@router.put("/activate_or_deactivate/{user_id}", tags=["Auth"])
def activate_or_deactivate(
    user_id: str, current_user: dict = Depends(get_current_user)
):
    if current_user["role"] not in ["super_admin", "admin"]:
        raise HTTPException(
            status_code=403,
            detail="Only super admin or admin can activate or deactivate users",
        )
    user = user_collection.find_one(ObjectId(user_id))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user["role"] == "super_admin":
        raise HTTPException(
            status_code=403, detail="Cannot activate or deactivate super admin"
        )

    user_collection.update_one(
        {"_id": ObjectId(user_id)}, {"$set": {"is_active": not user["is_active"]}}
    )

    if user["is_active"]:
        return {"message": "User deactivated successfully"}
    else:
        return {"message": "User activated successfully"}


@router.put("/update_yourself/", tags=["Auth"])
def update_yourself(user: UserUpdate, current_user: dict = Depends(get_current_user)):
    db_user = user_collection.find_one({"_id": ObjectId(current_user["_id"])})
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    update_data = user.model_dump(exclude_unset=True)

    if "password" in update_data:
        update_data["password"] = hash_password(update_data["password"])

    user_collection.update_one({"_id": db_user["_id"]}, {"$set": update_data})
    return {"message": "User updated successfully"}


@router.put("/change_password/", tags=["Auth"])
def change_password(
    old_password: str, new_password: str, current_user: dict = Depends(get_current_user)
):
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
        {"_id": db_user["_id"]}, {"$set": {"hashed_password": hashed_new_password}}
    )
    return {"message": "Password changed successfully"}


@router.post("/request-password-reset")
def request_password_reset(email: str):
    user = user_collection.find_one({"email": email})

    if not user:
        return {"message": "If the email exists, a reset link has been sent"}

    token = create_password_reset_token(
        reset_tokens_collection=reset_tokens_collection, user=user
    )

    reset_link = f"http://localhost:8000/reset-password?token={token}"

    print("RESET LINK:", reset_link)

    return {"message": "If the email exists, a reset link has been sent"}


@router.post("/reset-password")
def reset_password(token: str, new_password: str):
    record = verify_password_reset_token(reset_tokens_collection, token)

    if not record:
        raise HTTPException(status_code=400, detail="Invalid or expired token")

    hashed_password = hash_password(new_password)

    user_collection.update_one(
        {"_id": ObjectId(record["user_id"])},
        {"$set": {"hashed_password": hashed_password}},
    )
    reset_tokens_collection.update_one({"token": token}, {"$set": {"used": True}})

    return {"message": "Password reset successful"}

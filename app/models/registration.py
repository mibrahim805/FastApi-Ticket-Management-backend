import enum

from pydantic import BaseModel, ConfigDict, EmailStr


class GlobalRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"


class UserRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    email: EmailStr
    password: str
    is_active: bool = True


class AdminRegister(UserRegister):
    """Account payload for the one meaningful delegated global role."""


class SuperAdminRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    email: EmailStr
    password: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class ChangeGlobalRole(BaseModel):
    role: GlobalRole | None


class UserUpdate(BaseModel):
    name: str
    password: str
    is_active: bool


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

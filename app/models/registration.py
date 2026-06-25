import enum
from datetime import timezone, datetime
from openpyxl.utils import datetime
from pydantic import BaseModel, EmailStr, Field


class Role(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    USER = "User"
    ADMIN = "admin"
    CUSTOMER = "customer"
    AGENT= "agent"

class UserRegister(BaseModel):
    name : str
    email : EmailStr
    password : str
    is_active : bool = True
    role : str = Role.USER.value


class SuperAminRegister(BaseModel):
    name : str
    email : EmailStr
    password : str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserUpdate(BaseModel):
    name: str
    password: str
    is_active: bool


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str
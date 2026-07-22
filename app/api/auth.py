import secrets
from datetime import datetime, timedelta

from bson import ObjectId
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import get_settings
from app.db.db import user_collection

settings = get_settings()
SECRET_KEY = settings.secret_key.get_secret_value()
ALGORITHM = settings.jwt_algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes
PASSWORD_RESET_EXPIRE_MINUTES = settings.password_reset_expire_minutes

security_scheme = HTTPBearer()


def get_current_user(token: HTTPAuthorizationCredentials = Depends(security_scheme)):
    try:
        payload = jwt.decode(token.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token payload")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = user_collection.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str):
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str):
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def generate_reset_token():
    return secrets.token_urlsafe(32)


def create_password_reset_token(reset_tokens_collection, user):
    token = generate_reset_token()

    doc = {
        "token": token,
        "user_id": str(user["_id"]),
        "email": user["email"],
        "type": "password_reset",
        "used": False,
        "expires_at": datetime.utcnow()
        + timedelta(minutes=PASSWORD_RESET_EXPIRE_MINUTES),
    }

    reset_tokens_collection.insert_one(doc)

    return token


def verify_password_reset_token(reset_tokens_collection, token: str):
    record = reset_tokens_collection.find_one({"token": token})
    if not record:
        return None

    if record.get("used"):
        return None

    if record["expires_at"] < datetime.utcnow():
        return None

    return record

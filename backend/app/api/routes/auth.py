import hmac

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.config import settings
from app.core.security import create_access_token, verify_password

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse:
    # Always run a bcrypt verify call so unknown users do not take a cheap path.
    users = settings.auth_users()
    password_hash = ""
    username_ok = False
    for username, candidate_hash in users.items():
        if hmac.compare_digest(body.username, username):
            username_ok = True
            password_hash = candidate_hash
            break
    if not password_hash:
        password_hash = settings.auth_password_hash or next(iter(users.values()), "")

    password_ok = verify_password(body.password, password_hash)
    if not username_ok or not password_ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_access_token(body.username)
    return TokenResponse(access_token=token)

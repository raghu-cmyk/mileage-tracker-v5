import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from argon2.low_level import Type
from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from .models import User

PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

SESSION_USER_ID_KEY = "user_id"
SESSION_EXPIRES_AT_KEY = "expires_at"
SESSION_TTL_SECONDS = 3600
MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300


@dataclass
class LoginAttemptTracker:
    attempts: list[float] = field(default_factory=list)

    def record_failure(self, now: float) -> None:
        self.attempts.append(now)
        cutoff = now - LOGIN_WINDOW_SECONDS
        self.attempts = [t for t in self.attempts if t >= cutoff]

    def is_rate_limited(self, now: float) -> bool:
        cutoff = now - LOGIN_WINDOW_SECONDS
        recent = [t for t in self.attempts if t >= cutoff]
        self.attempts = recent
        return len(recent) >= MAX_LOGIN_ATTEMPTS


_login_attempts: dict[str, LoginAttemptTracker] = defaultdict(LoginAttemptTracker)


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def get_user_count(db: Session) -> int:
    return db.query(User).count()


def create_user(db: Session, username: str, password: str) -> User:
    if get_user_count(db) >= 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is closed. A user account already exists.",
        )
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken.",
        )
    user = User(username=username.strip(), password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, username: str, password: str, client_key: str) -> User:
    now = time.time()
    tracker = _login_attempts[client_key]
    if tracker.is_rate_limited(now):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again in a few minutes.",
        )

    user = db.query(User).filter(User.username == username.strip()).first()
    if user is None or not verify_password(user.password_hash, password):
        tracker.record_failure(now)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )

    tracker.attempts.clear()
    return user


def establish_session(request: Request, user_id: int) -> None:
    request.session[SESSION_USER_ID_KEY] = user_id
    request.session[SESSION_EXPIRES_AT_KEY] = time.time() + SESSION_TTL_SECONDS


def clear_session(request: Request) -> None:
    request.session.clear()


def get_current_user_id(request: Request) -> Optional[int]:
    expires_at = request.session.get(SESSION_EXPIRES_AT_KEY)
    user_id = request.session.get(SESSION_USER_ID_KEY)
    if user_id is None or expires_at is None:
        return None
    if time.time() > float(expires_at):
        clear_session(request)
        return None
    return int(user_id)


def require_authenticated_user(request: Request, db: Session) -> User:
    user_id = get_current_user_id(request)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        clear_session(request)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )
    return user

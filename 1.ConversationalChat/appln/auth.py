from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from sqlalchemy import create_engine, text

from config import DATABASE_URL, SESSION_TTL_HOURS


_password_hasher = PasswordHasher()


class AuthenticationError(Exception):
    """Raised when authentication fails."""


class AuthorizationError(Exception):
    """Raised when a user is not allowed to access a resource."""


class AuthService:
    def __init__(self, engine=None):
        self.engine = engine or create_engine(DATABASE_URL, pool_pre_ping=True)
        self._initialize_database()

    def _initialize_database(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS users (
                    id UUID PRIMARY KEY,
                    email VARCHAR(320) NOT NULL UNIQUE,
                    name VARCHAR(200) NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id UUID PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL,
                    revoked_at TIMESTAMPTZ NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_user_id
                    ON sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_expires_at
                    ON sessions(expires_at);
            """))

    @staticmethod
    def normalize_email(email: str) -> str:
        return email.strip().lower()

    def create_user(self, email: str, password: str, name: str) -> str:
        email = self.normalize_email(email)
        name = name.strip()

        if not email or not password or not name:
            raise ValueError("Name, email and password are required.")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")

        password_hash = _password_hasher.hash(password)
        user_id = uuid4()

        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text("""
                        INSERT INTO users (id, email, name, password_hash)
                        VALUES (:id, :email, :name, :password_hash)
                    """),
                    {
                        "id": user_id,
                        "email": email,
                        "name": name,
                        "password_hash": password_hash,
                    },
                )
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise ValueError("An account with this email already exists.") from exc
            raise

        return str(user_id)

    def authenticate(self, email: str, password: str) -> dict:
        email = self.normalize_email(email)

        with self.engine.connect() as conn:
            user = conn.execute(
                text("""
                    SELECT id, email, name, password_hash, is_active
                    FROM users
                    WHERE email = :email
                """),
                {"email": email},
            ).mappings().first()

        if not user or not user["is_active"]:
            raise AuthenticationError("Invalid email or password.")

        try:
            _password_hasher.verify(user["password_hash"], password)
        except (VerifyMismatchError, VerificationError):
            raise AuthenticationError("Invalid email or password.")

        # Transparently upgrade the hash if Argon2's recommended parameters change.
        if _password_hasher.check_needs_rehash(user["password_hash"]):
            new_hash = _password_hasher.hash(password)
            with self.engine.begin() as conn:
                conn.execute(
                    text("UPDATE users SET password_hash = :hash, updated_at = NOW() WHERE id = :id"),
                    {"hash": new_hash, "id": user["id"]},
                )

        return {
            "id": str(user["id"]),
            "email": user["email"],
            "name": user["name"],
        }

    def create_session(self, user_id: str) -> str:
        session_id = uuid4()
        expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)

        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO sessions (id, user_id, expires_at)
                    VALUES (:id, :user_id, :expires_at)
                """),
                {"id": session_id, "user_id": UUID(user_id), "expires_at": expires_at},
            )

        return str(session_id)

    def get_user_from_session(self, session_id: str | None) -> dict | None:
        if not session_id:
            return None

        with self.engine.begin() as conn:
            user = conn.execute(
                text("""
                    SELECT u.id, u.email, u.name
                    FROM sessions s
                    JOIN users u ON u.id = s.user_id
                    WHERE s.id = :session_id
                      AND s.revoked_at IS NULL
                      AND s.expires_at > NOW()
                      AND u.is_active = TRUE
                """),
                {"session_id": UUID(session_id)},
            ).mappings().first()

            if not user:
                return None

            conn.execute(
                text("UPDATE sessions SET last_seen_at = NOW() WHERE id = :session_id"),
                {"session_id": UUID(session_id)},
            )

        return {
            "id": str(user["id"]),
            "email": user["email"],
            "name": user["name"],
        }

    def revoke_session(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE sessions SET revoked_at = NOW() WHERE id = :session_id"),
                {"session_id": UUID(session_id)},
            )

"""Authentication use cases."""

from dataclasses import dataclass
from hashlib import sha256
from http import HTTPStatus
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import session_transaction
from app.core.exceptions import AppError
from app.core.security import PasswordService, TokenService
from app.models.user import User
from app.repositories.users import UserRepository


@dataclass(frozen=True, slots=True)
class GuestLoginResult:
    """Guest authentication result before HTTP serialization."""

    user: User
    access_token: str
    expires_in: int
    created: bool


@dataclass(frozen=True, slots=True)
class AuthLoginResult:
    """Authenticated account result before HTTP serialization."""

    user: User
    access_token: str
    expires_in: int


class AuthService:
    """Create/reuse identities and issue application JWTs."""

    def __init__(self, session: AsyncSession, token_service: TokenService) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._tokens = token_service
        self._passwords = PasswordService()

    async def login_guest(self, device_id: str | None) -> GuestLoginResult:
        async with session_transaction(self._session):
            if device_id is None:
                user = await self._users.create_guest_without_device()
                created = True
            else:
                device_hash = sha256(device_id.encode("utf-8")).hexdigest()
                user, created = await self._users.create_or_get_guest_by_hash(device_hash)

        return GuestLoginResult(
            user=user,
            access_token=self._tokens.issue(user_id=user.id, role=user.role),
            expires_in=self._tokens.expires_in_seconds,
            created=created,
        )

    async def register_email(
        self,
        *,
        email: str,
        password: str,
        display_name: str | None,
    ) -> AuthLoginResult:
        normalized_email = email.lower()
        normalized_name = display_name.strip() if display_name is not None else None
        if normalized_name == "":
            normalized_name = None

        try:
            async with session_transaction(self._session):
                existing = await self._users.get_by_email(normalized_email)
                if existing is not None:
                    raise AppError(
                        code="AUTH_EMAIL_EXISTS",
                        message="this email is already registered",
                        status_code=HTTPStatus.CONFLICT,
                    )
                user = await self._users.create_email_user(
                    email=normalized_email,
                    password_hash=self._passwords.hash(password),
                    display_name=normalized_name,
                )
        except IntegrityError as exc:
            raise AppError(
                code="AUTH_EMAIL_EXISTS",
                message="this email is already registered",
                status_code=HTTPStatus.CONFLICT,
            ) from exc

        return AuthLoginResult(
            user=user,
            access_token=self._tokens.issue(user_id=user.id, role=user.role),
            expires_in=self._tokens.expires_in_seconds,
        )

    async def login_email(self, *, email: str, password: str) -> AuthLoginResult:
        async with session_transaction(self._session):
            user = await self._users.get_by_email(email.lower())
            if (
                user is None
                or not user.is_active
                or user.auth_type != "email"
                or not self._passwords.verify(password, user.password_hash)
            ):
                raise AppError(
                    code="AUTH_INVALID_CREDENTIALS",
                    message="email or password is incorrect",
                    status_code=HTTPStatus.UNAUTHORIZED,
                )

        return AuthLoginResult(
            user=user,
            access_token=self._tokens.issue(user_id=user.id, role=user.role),
            expires_in=self._tokens.expires_in_seconds,
        )

    async def delete_current_user(self, user_id: UUID) -> None:
        async with session_transaction(self._session):
            user = await self._users.get_by_id(user_id)
            if user is not None:
                await self._users.delete(user)

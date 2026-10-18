from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import CustomerProfile, Role, User, UserRole


async def ensure_role(db: AsyncSession, role_name: str) -> Role:
    result = await db.execute(select(Role).where(Role.name == role_name))
    role = result.scalar_one_or_none()
    if role is None:
        role = Role(name=role_name)
        db.add(role)
        await db.flush()
    return role


async def register_user(db: AsyncSession, email: str, password: str) -> User:
    existing = await db.execute(select(User).where(User.email == email.lower()))
    if existing.scalar_one_or_none() is not None:
        raise ValueError("Email is already registered")

    user = User(email=email.lower(), password_hash=hash_password(password), status="ACTIVE")
    db.add(user)
    await db.flush()

    customer_role = await ensure_role(db, "CUSTOMER")
    db.add(UserRole(user_id=user.id, role_id=customer_role.id))
    db.add(CustomerProfile(user_id=user.id))
    await db.commit()
    await db.refresh(user)
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email.lower()))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    return user


async def create_token_for_user(db: AsyncSession, user: User) -> str:
    role_result = await db.execute(
        select(Role.name).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user.id)
    )
    roles = [row[0] for row in role_result.all()]
    return create_access_token(str(user.id), roles)

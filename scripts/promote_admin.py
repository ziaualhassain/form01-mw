from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.db.models import Role, User, UserRole
from app.db.session import async_session_factory


async def promote(email: str, role_name: str = "ADMIN") -> None:
    async with async_session_factory() as db:
        user = (await db.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none()
        if user is None:
            raise SystemExit(f"No user found for {email}")

        role = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one_or_none()
        if role is None:
            role = Role(name=role_name, description="FORM/01 administrative access")
            db.add(role)
            await db.flush()

        existing = await db.execute(select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id))
        if existing.scalar_one_or_none() is None:
            db.add(UserRole(user_id=user.id, role_id=role.id))
            await db.commit()

        print(f"{email} now has {role_name} access")


if __name__ == "__main__":
    if len(sys.argv) not in {2, 3}:
        raise SystemExit("Usage: python scripts/promote_admin.py <email> [ADMIN|OWNER]")
    asyncio.run(promote(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else "ADMIN"))

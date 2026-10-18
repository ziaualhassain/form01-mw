from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentPrincipal
from app.db.session import get_db
from app.schemas import LoginRequest, RegisterRequest, TokenResponse, UserRead
from app.services.auth import authenticate_user, create_token_for_user, register_user


router = APIRouter()


@router.post("/register", response_model=UserRead, status_code=201)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    try:
        user = await register_user(db, payload.email, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return UserRead(id=user.id, email=user.email, status=user.status, roles=[role.name for role in user.roles], email_verified_at=user.email_verified_at)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = await authenticate_user(db, payload.email, payload.password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = await create_token_for_user(db, user)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserRead)
async def me(principal: CurrentPrincipal, db: AsyncSession = Depends(get_db)):
    return UserRead(id=principal.user_id, email=principal.email, status="ACTIVE", roles=principal.roles)

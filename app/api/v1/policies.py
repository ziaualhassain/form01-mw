from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Response

from app.services.policies import POLICY_SECTIONS, PolicyDocument, PolicySummary, published_policies


router = APIRouter()
logger = logging.getLogger(__name__)


def load_published() -> list[PolicyDocument]:
    try:
        return published_policies()
    except (OSError, UnicodeError, ValueError):
        logger.exception("Unable to load approved policy pack")
        raise HTTPException(status_code=503, detail="Policies are temporarily unavailable") from None


@router.get("/policies", response_model=list[PolicySummary])
def policies(response: Response):
    response.headers["Cache-Control"] = "no-store"
    published = {policy.slug for policy in load_published()}
    return [
        PolicySummary(slug=slug, title=title, published=slug in published)
        for slug, (_, title) in POLICY_SECTIONS.items()
    ]


@router.get("/policies/{slug}", response_model=PolicyDocument)
def policy(slug: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    if slug not in POLICY_SECTIONS:
        raise HTTPException(status_code=404, detail="Policy not found")
    for document in load_published():
        if document.slug == slug:
            return document
    raise HTTPException(
        status_code=409,
        detail="This policy has not been approved for publication yet. Contact support@form01.com for assistance.",
        headers={"Cache-Control": "no-store"},
    )

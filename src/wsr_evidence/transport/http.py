"""HTTP routes that do not contain domain decisions."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}

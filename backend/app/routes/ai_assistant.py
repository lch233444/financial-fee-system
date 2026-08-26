from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from ..services.codex_app_server import (
    CodexAuthenticationError,
    CodexIntegrationError,
    CodexModelReroutedError,
    CodexModelUnavailableError,
    CodexProtocolError,
    CodexQuotaExceededError,
    CodexRecognitionError,
    CodexTimeoutError,
    CodexUnavailableError,
    CodexWrongAuthenticationError,
    get_codex_app_server,
)


router = APIRouter(prefix="/api/ai-assistant", tags=["ai-assistant"])
FINANCIAL_REQUEST_HEADER = "X-Financial-System-Request"


def require_financial_system_request(
    request_marker: str | None = Header(default=None, alias=FINANCIAL_REQUEST_HEADER),
) -> None:
    """Block blind cross-site form posts to subscription-sensitive actions."""

    if request_marker != "1":
        raise HTTPException(status_code=403, detail="缺少本地系统请求标记")


def raise_ai_http_error(exc: CodexIntegrationError) -> None:
    if isinstance(exc, CodexWrongAuthenticationError):
        status_code = 409
    elif isinstance(exc, CodexAuthenticationError):
        status_code = 401
    elif isinstance(exc, CodexTimeoutError):
        status_code = 504
    elif isinstance(exc, (CodexUnavailableError, CodexModelUnavailableError, CodexQuotaExceededError)):
        status_code = 503
    elif isinstance(exc, (CodexModelReroutedError, CodexProtocolError, CodexRecognitionError)):
        status_code = 502
    else:
        status_code = 500
    raise HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": str(exc), "manual_review_required": True},
    ) from exc


@router.get("/status")
def ai_assistant_status() -> dict:
    return get_codex_app_server().status()


@router.post("/login")
def ai_assistant_login(_request_guard: None = Depends(require_financial_system_request)) -> dict:
    try:
        return get_codex_app_server().login()
    except CodexIntegrationError as exc:
        raise_ai_http_error(exc)


@router.post("/logout")
def ai_assistant_logout(_request_guard: None = Depends(require_financial_system_request)) -> dict:
    try:
        return get_codex_app_server().logout()
    except CodexIntegrationError as exc:
        raise_ai_http_error(exc)

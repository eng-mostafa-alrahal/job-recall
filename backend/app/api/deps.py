import secrets

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings


def verify_api_token(
    x_jobrecall_token: str | None = Header(default=None, alias="X-JobRecall-Token"),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = settings.jobrecall_api_token
    if not expected:
        return
    if not x_jobrecall_token or not secrets.compare_digest(x_jobrecall_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "AUTH_MISMATCH",
                "message": "Invalid or missing X-JobRecall-Token header. Check your Extension Settings.",
            },
        )

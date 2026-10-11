"""Browser OIDC endpoints. Redirects only use the configured canonical URL."""
import logging

import httpx
import jwt
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from src.modules.administration import oidc
from src.platform.persistence.database import get_db
from src.platform.persistence.models import OIDCSession

router = APIRouter()
logger = logging.getLogger(__name__)


def _redirect(config: oidc.OIDCConfig, error: str | None = None) -> RedirectResponse:
    target = config.public_url + ("/login?oidc_error=" + error if error else "/")
    response = RedirectResponse(target, status_code=303)
    response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"})
    return response


@router.get("/login")
def login(request: Request, db: Session = Depends(get_db)):
    config = oidc.require_config()
    try:
        target, binding = oidc.start_login(db, config, request.cookies.get(config.transaction_cookie))
    except (oidc.OIDCError, httpx.HTTPError, ValueError, TypeError, KeyError):
        # Provider responses, tokens, codes and client credentials must not be logged.
        logger.warning("OIDC authorization could not start")
        return _redirect(config, "unavailable")
    response = RedirectResponse(target, status_code=303)
    response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    response.set_cookie(config.transaction_cookie, binding, max_age=oidc.TRANSACTION_SECONDS, path=config.cookie_path, secure=config.secure_cookie, httponly=True, samesite="lax")
    return response


@router.get("/callback")
def callback(request: Request, db: Session = Depends(get_db)):
    config = oidc.require_config()
    try:
        params = request.query_params
        if len(params.getlist("state")) != 1 or len(params.getlist("code")) > 1 or len(params.getlist("iss")) > 1 or len(params.getlist("error")) > 1:
            raise oidc.OIDCError("Ambiguous callback")
        nonce, verifier = oidc.consume_transaction(db, config, params.get("state", ""), request.cookies.get(config.transaction_cookie, ""))
        if params.get("error"):
            response = _redirect(config, "cancelled")
        else:
            if params.get("iss") is not None and params["iss"] != config.issuer:
                raise oidc.OIDCError("Authorization response issuer mismatch")
            session_id, _ = oidc.finish_login(db, config, params.get("code", ""), nonce, verifier)
            # Rotate/revoke the browser's previous local session on successful login.
            previous = request.cookies.get(config.session_cookie)
            if previous:
                db.query(OIDCSession).filter(OIDCSession.session_hash == oidc.digest(previous), OIDCSession.config_binding == config.binding).delete(synchronize_session=False)
                db.commit()
            response = _redirect(config)
            response.set_cookie(config.session_cookie, session_id, max_age=config.session_seconds, path=config.cookie_path, secure=config.secure_cookie, httponly=True, samesite="lax")
    except PermissionError:
        response = _redirect(config, "denied")
    except (oidc.OIDCError, httpx.HTTPError, jwt.PyJWTError, ValueError, TypeError, KeyError):
        logger.warning("OIDC callback validation failed")
        response = _redirect(config, "failed")
    # consume_transaction invalidates the matching one-use row. Never clear the
    # shared binding cookie here, even for a valid callback: a delayed response
    # could erase a newer login's binding. It expires after ten minutes or is
    # replaced by the next start; a retained binding cannot replay a used row.
    return response

"""Optional single-owner OpenID Connect authentication.

The provider's tokens never reach the browser or persistent storage. Only hashed,
revocable local session identifiers are stored; each identity is issuer + subject.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import quote, urlencode, urlsplit

import httpx
import jwt
from dotenv import dotenv_values
from fastapi import Request
from sqlalchemy.orm import Session

from src.platform.persistence.models import OIDCLoginTransaction, OIDCSession
from src.platform.persistence.database import SessionLocal
from src.platform.observability.log_context import redact_http_logs
from src.web.errors import api_error

SESSION_COOKIE = "panwatch_oidc_session"
TRANSACTION_COOKIE = "panwatch_oidc_login"
TRANSACTION_SECONDS = 600
# Explicit asymmetric allowlist; never derive allowed algorithms from token data.
SIGNING_ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"]


class OIDCError(ValueError):
    """Configuration/protocol error, deliberately without provider response data."""


def _flag(values: dict, name: str, default: bool = False) -> bool:
    value = str(values.get(name, str(default))).strip().lower()
    if value not in {"true", "false", "1", "0"}:
        raise OIDCError(f"{name} must be true or false")
    return value in {"true", "1"}


def enabled() -> bool:
    # Invalid configuration must never silently enable guest/password access.
    try:
        return get_config() is not None
    except OIDCError:
        return True


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _valid_url(value: str, allow_local_http: bool, *, issuer: bool = False) -> str:
    if not isinstance(value, str):
        raise OIDCError("Invalid OIDC URL")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError as exc:
        raise OIDCError("Invalid OIDC URL") from exc
    local_http = allow_local_http and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (parsed.scheme != "https" and not local_http) or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise OIDCError("OIDC URLs require HTTPS (HTTP is opt-in for loopback development only)")
    if "\\" in value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise OIDCError("Invalid OIDC URL")
    if any(part in {".", ".."} for part in parsed.path.split("/")) or "%" in parsed.path or "//" in parsed.path:
        raise OIDCError("OIDC URLs require an unambiguous path")
    # Browsers serialize origins with lower-case/IDNA hosts and no default port.
    # Keep issuer text exact for ID-token comparison; normalize redirect origins.
    if "%" in parsed.netloc:
        raise OIDCError("Encoded OIDC hostnames are not supported")
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL as exc:
        raise OIDCError("Invalid OIDC URL") from exc
    try:
        address = ipaddress.ip_address(url.host)
    except ValueError:
        # Browsers reinterpret short/octal/hex IPv4 forms. Reject these rather
        # than accepting a URL whose browser Origin differs from this server.
        last_label = url.host.rstrip(".").rsplit(".", 1)[-1]
        if last_label.isdigit() or last_label.lower().startswith("0x"):
            raise OIDCError("OIDC numeric hosts must use canonical IP literals")
    else:
        url = url.copy_with(host=address.compressed)
    return value if issuer else str(url).rstrip("/")


@dataclass(frozen=True)
class OIDCConfig:
    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    public_url: str
    subjects: frozenset[str]
    allow_password: bool
    allow_local_http: bool
    token_auth_method: str
    session_seconds: int
    endpoint_origins: frozenset[str]

    @property
    def callback_url(self) -> str:
        return self.public_url + "/api/auth/oidc/callback"

    @property
    def cookie_path(self) -> str:
        return urlsplit(self.public_url).path.rstrip("/") + "/"

    @property
    def origin(self) -> str:
        parsed = urlsplit(self.public_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @property
    def secure_cookie(self) -> bool:
        return urlsplit(self.public_url).scheme == "https"

    @property
    def binding(self) -> str:
        return digest(json.dumps([self.issuer, self.client_id, self.public_url], separators=(",", ":")))


@lru_cache(maxsize=1)
def get_config() -> OIDCConfig | None:
    values = {key: "" if value is None else value for key, value in {**dotenv_values(".env"), **os.environ}.items()}
    if not _flag(values, "OIDC_ENABLED"):
        return None
    allow_http = _flag(values, "OIDC_ALLOW_HTTP_LOCALHOST")
    issuer = _valid_url(values.get("OIDC_ISSUER", "").strip(), allow_http, issuer=True)
    public_url = _valid_url(values.get("OIDC_PUBLIC_URL", "").strip(), allow_http)
    base_path = values.get("PANWATCH_BASE_PATH", "").strip().rstrip("/")
    if urlsplit(public_url).path.rstrip("/") != base_path:
        raise OIDCError("OIDC_PUBLIC_URL path must match PANWATCH_BASE_PATH")
    client_id = values.get("OIDC_CLIENT_ID", "").strip()
    if not client_id:
        raise OIDCError("OIDC_CLIENT_ID is required")
    try:
        subjects = json.loads(values.get("OIDC_ALLOWED_SUBJECTS", "[]"))
    except (ValueError, TypeError) as exc:
        raise OIDCError("OIDC_ALLOWED_SUBJECTS must be a JSON array") from exc
    if not isinstance(subjects, list) or not subjects or any(not isinstance(sub, str) or not sub or len(sub) > 255 for sub in subjects):
        raise OIDCError("OIDC_ALLOWED_SUBJECTS must explicitly list allowed subject identifiers")
    method = values.get("OIDC_TOKEN_AUTH_METHOD", "client_secret_basic").strip()
    if method not in {"client_secret_basic", "client_secret_post", "none"}:
        raise OIDCError("Unsupported OIDC_TOKEN_AUTH_METHOD")
    secret = values.get("OIDC_CLIENT_SECRET", "")
    if method != "none" and not secret:
        raise OIDCError("OIDC_CLIENT_SECRET is required for confidential clients")
    try:
        hours = int(values.get("OIDC_SESSION_HOURS", "12"))
    except ValueError as exc:
        raise OIDCError("OIDC_SESSION_HOURS must be an integer") from exc
    if not 1 <= hours <= 168:
        raise OIDCError("OIDC_SESSION_HOURS must be between 1 and 168")
    issuer_parts = urlsplit(_valid_url(issuer, allow_http))
    origins = {f"{issuer_parts.scheme}://{issuer_parts.netloc}"}
    try:
        extra_origins = json.loads(values.get("OIDC_ALLOWED_ENDPOINT_ORIGINS", "[]"))
    except (ValueError, TypeError) as exc:
        raise OIDCError("OIDC_ALLOWED_ENDPOINT_ORIGINS must be a JSON array") from exc
    if not isinstance(extra_origins, list):
        raise OIDCError("OIDC_ALLOWED_ENDPOINT_ORIGINS must be a JSON array")
    for origin in extra_origins:
        normalized = _valid_url(origin, allow_http)
        if urlsplit(normalized).path:
            raise OIDCError("OIDC_ALLOWED_ENDPOINT_ORIGINS must contain origins without paths")
        origins.add(normalized)
    return OIDCConfig(issuer, client_id, secret, public_url, frozenset(subjects), _flag(values, "OIDC_ALLOW_PASSWORD_LOGIN"), allow_http, method, hours * 3600, frozenset(origins))


def require_config() -> OIDCConfig:
    try:
        config = get_config()
    except OIDCError:
        raise api_error(503, "oidc_not_configured", "单点登录配置无效，请联系管理员") from None
    if config is None:
        raise api_error(404, "oidc_disabled", "单点登录未启用")
    return config


def password_enabled() -> bool:
    config = require_config() if enabled() else None
    return config is None or config.allow_password


def http_client() -> httpx.Client:
    # No redirects: never forward client credentials to a redirected endpoint.
    return httpx.Client(timeout=httpx.Timeout(10.0), follow_redirects=False)


def _json_response(response: httpx.Response) -> dict:
    response.raise_for_status()
    if len(response.content) > 1_000_000:
        raise OIDCError("OIDC response too large")
    data = response.json()
    if not isinstance(data, dict):
        raise OIDCError("Invalid OIDC response")
    return data


def discover(config: OIDCConfig, client: httpx.Client) -> dict:
    metadata = _json_response(client.get(config.issuer.rstrip("/") + "/.well-known/openid-configuration"))
    if metadata.get("issuer") != config.issuer:
        raise OIDCError("OIDC discovery issuer mismatch")
    for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        endpoint = _valid_url(metadata.get(name, ""), config.allow_local_http)
        parts = urlsplit(endpoint)
        if f"{parts.scheme}://{parts.netloc}" not in config.endpoint_origins:
            raise OIDCError("Discovery endpoint origin is not explicitly trusted")
    if "code" not in metadata.get("response_types_supported", []):
        raise OIDCError("Provider does not support authorization code flow")
    methods = metadata.get("code_challenge_methods_supported")
    if methods is not None and "S256" not in methods:
        raise OIDCError("Provider does not support S256 PKCE")
    if config.token_auth_method not in metadata.get("token_endpoint_auth_methods_supported", ["client_secret_basic"]):
        raise OIDCError("Provider does not support configured client authentication")
    return metadata


def cleanup(db: Session) -> None:
    now = int(time.time())
    db.query(OIDCLoginTransaction).filter(OIDCLoginTransaction.expires_at <= now).delete(synchronize_session=False)
    db.query(OIDCSession).filter(OIDCSession.expires_at <= now).delete(synchronize_session=False)


def start_login(db: Session, config: OIDCConfig, previous_binding: str | None = None) -> tuple[str, str]:
    with redact_http_logs(), http_client() as client:
        metadata = discover(config, client)
    cleanup(db)
    if previous_binding:
        db.query(OIDCLoginTransaction).filter(OIDCLoginTransaction.browser_hash == digest(previous_binding)).delete(synchronize_session=False)
    if db.query(OIDCLoginTransaction).count() >= 128:
        db.commit()
        raise api_error(429, "oidc_busy", "登录请求过多，请稍后重试")
    state, browser_binding, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
    db.add(OIDCLoginTransaction(state_hash=digest(state), browser_hash=digest(browser_binding), nonce=nonce, verifier=verifier, config_binding=config.binding, expires_at=int(time.time()) + TRANSACTION_SECONDS))
    db.commit()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {"response_type": "code", "client_id": config.client_id, "redirect_uri": config.callback_url, "scope": "openid", "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256", "response_mode": "query"}
    return metadata["authorization_endpoint"] + "?" + urlencode(params), browser_binding


def consume_transaction(db: Session, config: OIDCConfig, state: str, browser_binding: str) -> tuple[str, str]:
    if not state or not browser_binding or len(state) > 256 or len(browser_binding) > 256:
        raise OIDCError("Missing login transaction")
    filters = (OIDCLoginTransaction.state_hash == digest(state), OIDCLoginTransaction.browser_hash == digest(browser_binding), OIDCLoginTransaction.config_binding == config.binding, OIDCLoginTransaction.expires_at > int(time.time()))
    transaction = db.query(OIDCLoginTransaction).filter(*filters).first()
    if transaction is None:
        raise OIDCError("Expired or invalid login transaction")
    nonce, verifier = transaction.nonce, transaction.verifier
    # The atomic delete must win before the authorization code is exchanged.
    consumed = db.query(OIDCLoginTransaction).filter(*filters).delete(synchronize_session=False)
    db.commit()
    if consumed != 1:
        raise OIDCError("Login transaction already used")
    return nonce, verifier


def validate_id_token(config: OIDCConfig, metadata: dict, token: str, nonce: str, client: httpx.Client) -> dict:
    header = jwt.get_unverified_header(token)
    algorithm = header.get("alg")
    if algorithm not in SIGNING_ALGORITHMS or algorithm not in metadata.get("id_token_signing_alg_values_supported", ["RS256"]):
        raise OIDCError("Unsupported ID token algorithm")
    jwks = _json_response(client.get(metadata["jwks_uri"]))
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        raise OIDCError("Invalid signing keys")
    candidates = [key for key in keys if isinstance(key, dict) and key.get("use", "sig") == "sig" and ("key_ops" not in key or "verify" in key["key_ops"]) and key.get("alg", algorithm) == algorithm and (not header.get("kid") or key.get("kid") == header["kid"]) and key.get("kty") in {"RSA", "EC", "OKP"}]
    if len(candidates) != 1:
        raise OIDCError("Missing or ambiguous ID token signing key")
    key = jwt.PyJWK.from_dict(candidates[0], algorithm=algorithm)
    claims = jwt.decode(token, key, algorithms=SIGNING_ALGORITHMS, audience=config.client_id, issuer=config.issuer, options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]}, leeway=30)
    audience = claims["aud"]
    if (isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != config.client_id) or ("azp" in claims and claims["azp"] != config.client_id):
        raise OIDCError("ID token authorized party mismatch")
    if not isinstance(claims["nonce"], str) or not secrets.compare_digest(claims["nonce"], nonce):
        raise OIDCError("ID token nonce mismatch")
    if not isinstance(claims["sub"], str) or claims["sub"] not in config.subjects:
        raise PermissionError("Identity is not authorized")
    return claims


def finish_login(db: Session, config: OIDCConfig, code: str, nonce: str, verifier: str) -> tuple[str, str]:
    if not code or len(code) > 8192:
        raise OIDCError("Missing authorization code")
    with redact_http_logs(), http_client() as client:
        metadata = discover(config, client)
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": config.callback_url, "code_verifier": verifier}
        auth = None
        if config.token_auth_method == "client_secret_basic":
            auth = httpx.BasicAuth(quote(config.client_id, safe=""), quote(config.client_secret, safe=""))
        else:
            data["client_id"] = config.client_id
            if config.token_auth_method == "client_secret_post":
                data["client_secret"] = config.client_secret
        token_response = _json_response(client.post(metadata["token_endpoint"], data=data, auth=auth))
        token = token_response.get("id_token")
        if not isinstance(token, str) or len(token) > 65536:
            raise OIDCError("Missing ID token")
        claims = validate_id_token(config, metadata, token, nonce, client)
    session_id, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    cleanup(db)
    db.add(OIDCSession(session_hash=digest(session_id), csrf_token=csrf, issuer=config.issuer, subject=claims["sub"], config_binding=config.binding, expires_at=int(time.time()) + config.session_seconds))
    db.commit()
    return session_id, csrf


def get_session(db: Session, request: Request, config: OIDCConfig) -> OIDCSession | None:
    session_id = request.cookies.get(SESSION_COOKIE, "")
    if not session_id or len(session_id) > 256:
        return None
    session = db.query(OIDCSession).filter(OIDCSession.session_hash == digest(session_id), OIDCSession.expires_at > int(time.time()), OIDCSession.config_binding == config.binding).first()
    if session is None or session.issuer != config.issuer or session.subject not in config.subjects:
        return None
    return session


def check_csrf(request: Request, session: OIDCSession, config: OIDCConfig) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    value = request.headers.get("X-CSRF-Token", "")
    if request.headers.get("origin") != config.origin or not value or not secrets.compare_digest(value, session.csrf_token):
        raise api_error(403, "csrf_invalid", "请求验证失败，请刷新后重试")


def session_is_active(identity: tuple[str, str]) -> bool:
    """Use a fresh DB read so live streams see logout committed elsewhere."""
    config = get_config()
    if config is None or identity[1] != config.binding:
        return False
    with SessionLocal() as db:
        session = db.query(OIDCSession).filter(OIDCSession.session_hash == identity[0], OIDCSession.config_binding == config.binding, OIDCSession.expires_at > int(time.time())).first()
        return bool(session is not None and session.issuer == config.issuer and session.subject in config.subjects)

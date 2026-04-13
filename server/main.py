"""
AWS Secrets Manager MCP Server
-------------------------------
Streamable HTTP transport (MCP spec 2025-03-26) with Okta OAuth 2.1 PKCE.

Auth flow:
  1. MCP client hits /mcp unauthenticated → 401 with WWW-Authenticate header
  2. Client fetches /.well-known/oauth-protected-resource → points to this server
  3. Client fetches /.well-known/oauth-authorization-server → Okta endpoints + /register
  4. Client POSTs to /register (static DCR) → gets pre-registered client_id
  5. Client opens browser → user logs in to Okta → PKCE exchange → gets JWT
  6. All /mcp requests carry  Authorization: Bearer <okta_jwt>
  7. Server verifies JWT signature via Okta JWKS

Secret entry (URL mode elicitation — MCP spec requirement for sensitive values):
  - create_secret / update_secret generate a one-time token and return a URL
  - MCP client opens that URL in the user's browser
  - User types the value into a form served by THIS server
  - Server stores it directly in Secrets Manager — value never enters the MCP protocol

Entry point: uvicorn main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import contextvars
import html as _html
import logging
import os
import secrets as _secrets_mod
import time
from typing import Optional

import jwt as pyjwt
from jwt import PyJWKClient
import boto3
from botocore.exceptions import ClientError
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

logger = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _sm() -> boto3.client:  # type: ignore[return]
    return boto3.client("secretsmanager")


def _iso(dt) -> Optional[str]:
    """Convert boto3 datetime (or None) to ISO 8601 string."""
    if dt is None:
        return None
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


# ─── User Context & Authorization ─────────────────────────────────────────────

_user_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("user_ctx", default={})
_base_url_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("base_url_ctx", default="")

_OAUTH_JWKS_URI = os.environ.get("OAUTH_JWKS_URI", "")
_OAUTH_AUDIENCE = os.environ.get("OAUTH_AUDIENCE", "api://default")
_OAUTH_ISSUER_V = os.environ.get("OAUTH_ISSUER", "")

# Initialized once at startup; PyJWKClient caches the key set with a 1-hour TTL.
_jwks_client: Optional[PyJWKClient] = (
    PyJWKClient(_OAUTH_JWKS_URI, cache_jwk_set=True, lifespan=3600)
    if _OAUTH_JWKS_URI else None
)


def _get_jwt_claims(authorization: str) -> dict:
    """Verify JWT signature and return claims. Returns {} if invalid or missing."""
    token = authorization.removeprefix("Bearer ").strip()
    if not token or _jwks_client is None:
        return {}
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        return pyjwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=_OAUTH_AUDIENCE,
            issuer=_OAUTH_ISSUER_V,
        )
    except Exception as e:
        logger.warning("JWT verification failed: %s", e)
        return {}


def _user_groups() -> set[str]:
    # IT configured forge_groups as the claim name for Okta group memberships.
    return set(_user_ctx.get({}).get("forge_groups", []))


def _check_access(tags: dict[str, str], tag_key: str, user_groups: set[str]) -> bool:
    """True if user is allowed.  No tag present → unrestricted (open by default)."""
    allowed = tags.get(tag_key, "").strip()
    if not allowed:
        return True
    return bool(user_groups & {g.strip() for g in allowed.split(",")})


class UserContextMiddleware:
    """ASGI middleware — verifies the JWT and stores claims in a ContextVar.
    Returns 401 for /mcp requests that have no valid token."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            auth    = headers.get(b"authorization", b"").decode()
            path    = scope.get("path", "")

            # Capture base URL for use in tool functions via ContextVar
            host   = headers.get(b"host", b"").decode()
            scheme = headers.get(b"x-forwarded-proto", b"https").decode()
            _base_url_ctx.set(f"{scheme}://{host}")

            if path.startswith("/mcp"):
                claims = _get_jwt_claims(auth) if auth else {}
                if not claims:
                    prm = f"{scheme}://{host}/.well-known/oauth-protected-resource"
                    resp = Response(
                        "Unauthorized",
                        status_code=401,
                        media_type="text/plain",
                        headers={"WWW-Authenticate": f'Bearer resource_metadata="{prm}"'},
                    )
                    await resp(scope, receive, send)
                    return
                _user_ctx.set(claims)
            elif auth:
                _user_ctx.set(_get_jwt_claims(auth))

        await self.app(scope, receive, send)


# ─── URL-mode secret entry stores ─────────────────────────────────────────────
# Two-phase approach: create_secret/update_secret return a URL and token
# immediately. The browser form POSTs the value to /secret-entry/{token},
# stored in _completed. finalize_secret(token) reads it and commits to AWS.
#
# Single-instance App Runner (max_size=1) keeps both dicts in the same process.

_ENTRY_TIMEOUT = 300  # seconds before a pending token expires

# token → operation metadata (op, name/secret_id, description, tags, expires_at)
_pending_ops: dict[str, dict] = {}
# token → submitted secret value (cleared immediately after finalize)
_completed: dict[str, str] = {}


# ─── MCP Server & Tools ───────────────────────────────────────────────────────

mcp = FastMCP(
    name="AWS Secrets Manager",
    stateless_http=False,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    instructions=(
        "Manage AWS Secrets Manager secrets. "
        "Available operations: list_secrets, get_secret, describe_secret, "
        "create_secret, update_secret, delete_secret."
    ),
)


@mcp.tool()
def whoami() -> dict:
    """Return the current user's identity and group memberships from the JWT."""
    claims = _user_ctx.get({})
    return {
        "sub":          claims.get("sub"),
        "email":        claims.get("sub"),  # Okta puts email in sub for this org
        "forge_groups": claims.get("forge_groups", []),
        "client_id":    claims.get("client_id"),
        "scopes":       claims.get("scp", []),
    }


@mcp.tool()
def list_secrets(
    name_prefix: str = "",
    max_results: int = 20,
) -> list[dict]:
    """
    List secrets in AWS Secrets Manager.

    Args:
        name_prefix: Optional prefix to filter secret names.
        max_results: Maximum number of results (1–100).

    Returns:
        List of secret metadata objects (no values).
    """
    kwargs: dict = {"MaxResults": max(1, min(max_results, 100))}
    if name_prefix:
        kwargs["Filters"] = [{"Key": "name", "Values": [name_prefix]}]

    try:
        resp = _sm().list_secrets(**kwargs)
    except ClientError as e:
        raise ValueError(f"AWS error listing secrets: {e.response['Error']['Message']}") from e

    user_groups = _user_groups()
    return [
        {
            "name": s["Name"],
            "arn": s["ARN"],
            "description": s.get("Description", ""),
            "last_changed": _iso(s.get("LastChangedDate")),
            "tags": {t["Key"]: t["Value"] for t in s.get("Tags", [])},
        }
        for s in resp.get("SecretList", [])
        if _check_access(
            {t["Key"]: t["Value"] for t in s.get("Tags", [])},
            "mcp:read_groups",
            user_groups,
        )
    ]


@mcp.tool()
def get_secret(secret_id: str) -> dict:
    """
    Retrieve the current value of a secret.

    Args:
        secret_id: Secret name or full ARN.

    Returns:
        Object with 'name', 'arn', 'version_id', 'created_date', and 'value'.
    """
    try:
        meta = _sm().describe_secret(SecretId=secret_id)
    except ClientError as e:
        raise ValueError(f"AWS error: {e.response['Error']['Message']}") from e
    tags = {t["Key"]: t["Value"] for t in meta.get("Tags", [])}
    if not _check_access(tags, "mcp:read_groups", _user_groups()):
        raise PermissionError(f"Access denied to secret: {secret_id}")

    try:
        resp = _sm().get_secret_value(SecretId=secret_id)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ResourceNotFoundException":
            raise ValueError(f"Secret not found: {secret_id}") from e
        if code in ("AccessDeniedException", "UnauthorizedException"):
            raise PermissionError(f"Access denied to secret: {secret_id}") from e
        raise ValueError(f"AWS error: {e.response['Error']['Message']}") from e

    result: dict = {
        "name": resp["Name"],
        "arn": resp["ARN"],
        "version_id": resp.get("VersionId"),
        "created_date": _iso(resp.get("CreatedDate")),
    }
    if "SecretString" in resp:
        result["value"] = resp["SecretString"]
    else:
        result["value"] = resp["SecretBinary"].decode("utf-8")
    return result


@mcp.tool()
def describe_secret(secret_id: str) -> dict:
    """
    Get metadata about a secret without returning its value.

    Args:
        secret_id: Secret name or full ARN.

    Returns:
        Metadata including creation date, rotation status, tags, and version info.
    """
    try:
        resp = _sm().describe_secret(SecretId=secret_id)
    except ClientError as e:
        raise ValueError(f"AWS error: {e.response['Error']['Message']}") from e

    tags = {t["Key"]: t["Value"] for t in resp.get("Tags", [])}
    if not _check_access(tags, "mcp:read_groups", _user_groups()):
        raise PermissionError(f"Access denied to secret: {secret_id}")

    return {
        "name": resp["Name"],
        "arn": resp["ARN"],
        "description": resp.get("Description", ""),
        "created": _iso(resp.get("CreatedDate")),
        "last_changed": _iso(resp.get("LastChangedDate")),
        "last_accessed": _iso(resp.get("LastAccessedDate")),
        "rotation_enabled": resp.get("RotationEnabled", False),
        "deletion_date": _iso(resp.get("DeletedDate")),
        "tags": tags,
        "versions": {
            vid: list(stages)
            for vid, stages in resp.get("VersionIdsToStages", {}).items()
        },
    }


@mcp.tool()
def create_secret(
    name: str,
    description: str = "",
    tags: Optional[dict[str, str]] = None,
) -> dict:
    """
    Begin creating a new secret in AWS Secrets Manager.

    Returns a one-time URL for entering the secret value in a secure browser form.
    The value never passes through the LLM or the MCP protocol.

    After the user submits the form, call finalize_secret(token) to commit.

    Args:
        name: Unique secret name (use / for namespacing, e.g. 'prod/myapp/db').
        description: Optional human-readable description.
        tags: Optional key/value tags (e.g. mcp:read_groups, mcp:write_groups).

    Returns:
        Object with 'status', 'entry_url', 'token', and 'next_step'.
    """
    token = _secrets_mod.token_urlsafe(32)
    base_url = _base_url_ctx.get()
    entry_url = f"{base_url}/secret-entry/{token}?name={name}"
    _pending_ops[token] = {
        "op": "create",
        "name": name,
        "description": description,
        "tags": tags,
        "expires_at": time.time() + _ENTRY_TIMEOUT,
    }
    return {
        "status": "awaiting_value",
        "entry_url": entry_url,
        "token": token,
        "next_step": f"Open the entry_url in a browser to enter the secret value, then call finalize_secret(token='{token}').",
    }


@mcp.tool()
def update_secret(
    secret_id: str,
    description: Optional[str] = None,
) -> dict:
    """
    Begin updating the value of an existing secret.

    Returns a one-time URL for entering the new value in a secure browser form.
    The value never passes through the LLM or the MCP protocol.

    After the user submits the form, call finalize_secret(token) to commit.

    Args:
        secret_id: Secret name or full ARN.
        description: If provided, also updates the description.

    Returns:
        Object with 'status', 'entry_url', 'token', and 'next_step'.
    """
    try:
        meta = _sm().describe_secret(SecretId=secret_id)
    except ClientError as e:
        raise ValueError(f"AWS error: {e.response['Error']['Message']}") from e
    tags = {t["Key"]: t["Value"] for t in meta.get("Tags", [])}
    if not _check_access(tags, "mcp:write_groups", _user_groups()):
        raise PermissionError(f"Access denied to secret: {secret_id}")

    token = _secrets_mod.token_urlsafe(32)
    base_url = _base_url_ctx.get()
    entry_url = f"{base_url}/secret-entry/{token}?name={secret_id}"
    _pending_ops[token] = {
        "op": "update",
        "secret_id": secret_id,
        "description": description,
        "expires_at": time.time() + _ENTRY_TIMEOUT,
    }
    return {
        "status": "awaiting_value",
        "entry_url": entry_url,
        "token": token,
        "next_step": f"Open the entry_url in a browser to enter the new value, then call finalize_secret(token='{token}').",
    }


@mcp.tool()
def finalize_secret(token: str) -> dict:
    """
    Complete a pending secret creation or update.

    Call this after the user has submitted the secret value via the browser form
    returned by create_secret or update_secret.

    Args:
        token: The token returned by create_secret or update_secret.

    Returns:
        Object with 'name', 'arn', 'version_id'.
    """
    op = _pending_ops.pop(token, None)
    if op is None:
        raise ValueError("Invalid or expired token. Please call create_secret or update_secret again.")
    if time.time() > op["expires_at"]:
        _completed.pop(token, None)
        raise ValueError("Token expired (5 minutes). Please start over.")

    value = _completed.pop(token, None)
    if value is None:
        # Put the op back so the user can try again after submitting the form
        _pending_ops[token] = op
        raise ValueError("Secret value not yet submitted. Please complete the browser form first, then call finalize_secret again.")

    if op["op"] == "create":
        kwargs: dict = {"Name": op["name"], "SecretString": value}
        if op["description"]:
            kwargs["Description"] = op["description"]
        if op["tags"]:
            kwargs["Tags"] = [{"Key": k, "Value": v} for k, v in op["tags"].items()]
        try:
            resp = _sm().create_secret(**kwargs)
        except ClientError as e:
            raise ValueError(f"AWS error creating secret: {e.response['Error']['Message']}") from e
        return {"name": resp["Name"], "arn": resp["ARN"], "version_id": resp["VersionId"]}

    # op == "update"
    kwargs = {"SecretId": op["secret_id"], "SecretString": value}
    if op["description"] is not None:
        kwargs["Description"] = op["description"]
    try:
        resp = _sm().update_secret(**kwargs)
    except ClientError as e:
        raise ValueError(f"AWS error updating secret: {e.response['Error']['Message']}") from e
    return {"name": resp["Name"], "arn": resp["ARN"], "version_id": resp["VersionId"]}


@mcp.tool()
def delete_secret(
    secret_id: str,
    recovery_window_days: int = 30,
    force_delete: bool = False,
) -> dict:
    """
    Delete a secret from AWS Secrets Manager.

    By default, schedules deletion after a recovery window (7–30 days).
    Set force_delete=True to immediately and permanently delete (irreversible).

    Args:
        secret_id: Secret name or full ARN.
        recovery_window_days: Days before permanent deletion (7–30). Ignored if force_delete=True.
        force_delete: Skip recovery window and delete immediately. Cannot be undone.

    Returns:
        Object with 'name', 'arn', 'deletion_date'.
    """
    try:
        meta = _sm().describe_secret(SecretId=secret_id)
    except ClientError as e:
        raise ValueError(f"AWS error: {e.response['Error']['Message']}") from e
    tags = {t["Key"]: t["Value"] for t in meta.get("Tags", [])}
    if not _check_access(tags, "mcp:admin_groups", _user_groups()):
        raise PermissionError(f"Access denied to secret: {secret_id}")

    kwargs: dict = {"SecretId": secret_id}
    if force_delete:
        kwargs["ForceDeleteWithoutRecovery"] = True
    else:
        kwargs["RecoveryWindowInDays"] = max(7, min(30, recovery_window_days))

    try:
        resp = _sm().delete_secret(**kwargs)
    except ClientError as e:
        raise ValueError(f"AWS error deleting secret: {e.response['Error']['Message']}") from e

    return {
        "name": resp["Name"],
        "arn": resp["ARN"],
        "deletion_date": _iso(resp.get("DeletionDate")),
    }


# ─── OAuth endpoints ──────────────────────────────────────────────────────────

def _base_url(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("host", "")
    return f"{scheme}://{host}"


async def oauth_protected_resource(request: Request) -> JSONResponse:
    base = _base_url(request)
    return JSONResponse(
        {
            "authorization_servers": [base],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["openid", "profile", "email"],
        },
        headers={"Cache-Control": "no-cache, no-store"},
    )


async def oauth_authorization_server(request: Request) -> JSONResponse:
    base = _base_url(request)
    return JSONResponse(
        {
            "issuer":                        os.environ["OAUTH_ISSUER"],
            "authorization_endpoint":        os.environ["OAUTH_AUTHORIZATION_ENDPOINT"],
            "token_endpoint":                os.environ["OAUTH_TOKEN_ENDPOINT"],
            "jwks_uri":                      os.environ["OAUTH_JWKS_URI"],
            "registration_endpoint":         f"{base}/register",
            "scopes_supported":              ["openid", "profile", "email"],
            "response_types_supported":      ["code"],
            "grant_types_supported":         ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        },
        headers={"Cache-Control": "no-cache, no-store"},
    )


async def register_client(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    logger.info("DCR /register request body: %s", body)
    return JSONResponse(
        {
            "client_id":                  os.environ["OAUTH_CLIENT_ID"],
            "client_secret_expires_at":   0,
            "redirect_uris":              body.get("redirect_uris", []),
            "grant_types":                ["authorization_code"],
            "response_types":             ["code"],
            "token_endpoint_auth_method": "none",
            "client_id_issued_at":        int(time.time()),
        },
        status_code=201,
        headers={"Cache-Control": "no-store"},
    )


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


# ─── Secret entry form (URL mode elicitation) ─────────────────────────────────

_FORM_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Enter secret — AWS Secrets Manager</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 480px; margin: 80px auto; padding: 0 24px; color: #1a1a1a; }}
    h2   {{ font-size: 1.2rem; margin-bottom: 4px; }}
    p    {{ color: #555; font-size: 0.9rem; margin-top: 4px; }}
    label {{ display: block; margin-top: 20px; font-weight: 600; font-size: 0.9rem; }}
    input {{ display: block; width: 100%; box-sizing: border-box; padding: 10px 12px;
             font-size: 1rem; border: 1px solid #ccc; border-radius: 6px; margin-top: 6px; }}
    button {{ margin-top: 20px; padding: 10px 24px; background: #0066cc; color: #fff;
              border: none; border-radius: 6px; font-size: 1rem; cursor: pointer; }}
    button:hover {{ background: #0052a3; }}
    .badge {{ display:inline-block; background:#f0f4ff; border:1px solid #c0cff0;
              border-radius:4px; padding:2px 8px; font-size:0.8rem; color:#334; margin-bottom:16px; }}
  </style>
</head>
<body>
  <div class="badge">🔒 AWS Secrets Manager MCP</div>
  <h2>{title}</h2>
  <p>This value is sent directly to the server and stored in AWS Secrets Manager.<br>
     It never passes through the AI model.</p>
  <form method="POST">
    <label for="value">Secret value</label>
    <input type="password" id="value" name="value" required autofocus
           placeholder="Enter value…" autocomplete="off">
    <button type="submit">Store secret</button>
  </form>
</body>
</html>
"""

_DONE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Secret stored</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 480px; margin: 80px auto; padding: 0 24px; color: #1a1a1a; }}
    h2   {{ color: #1a7a3a; }}
  </style>
</head>
<body>
  <h2>✓ Secret stored</h2>
  <p>You can close this tab.</p>
</body>
</html>
"""

_EXPIRED_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Link expired</title></head>
<body style="font-family:system-ui;max-width:480px;margin:80px auto;padding:0 24px">
  <h2>Link expired or invalid</h2>
  <p>This link has already been used or has expired. Please retry the operation in Claude Code.</p>
</body>
</html>
"""


async def secret_entry(request: Request) -> Response:
    """GET: serve the password form. POST: store submitted value for finalize_secret."""
    token = request.path_params["token"]

    op = _pending_ops.get(token)
    if op is None or time.time() > op["expires_at"]:
        return HTMLResponse(_EXPIRED_HTML, status_code=404)

    if request.method == "GET":
        title = _html.escape(request.query_params.get("name", "secret"))
        return HTMLResponse(_FORM_HTML.format(title=f"Enter value for <em>{title}</em>"))

    # POST — store the submitted value; finalize_secret() will pick it up
    form = await request.form()
    value = str(form.get("value", ""))
    if not value:
        return HTMLResponse("Value cannot be empty.", status_code=400)

    _completed[token] = value
    return HTMLResponse(_DONE_HTML)


# ─── ASGI Application ────────────────────────────────────────────────────────

def _build_app():
    _app = mcp.streamable_http_app()
    _app.router.routes = [
        Route("/.well-known/oauth-protected-resource",   oauth_protected_resource),
        Route("/.well-known/oauth-authorization-server", oauth_authorization_server),
        Route("/register",              register_client,  methods=["POST"]),
        Route("/health",                health),
        Route("/secret-entry/{token}",  secret_entry,     methods=["GET", "POST"]),
    ] + list(_app.router.routes)
    _app.add_middleware(UserContextMiddleware)
    return _app


app = _build_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

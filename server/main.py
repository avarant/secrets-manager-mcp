"""
AWS Secrets Manager MCP Server
-------------------------------
Streamable HTTP transport (MCP spec 2025-03-26) with Okta OAuth 2.1 PKCE.

Auth flow:
  1. MCP client hits GET /.well-known/oauth-protected-resource  (no auth)
  2. Response points client to Okta authorization server
  3. Client opens browser → user logs in → PKCE exchange → gets JWT
  4. All /mcp requests carry  Authorization: Bearer <okta_jwt>
  5. API Gateway JWT Authorizer validates the token before Lambda is invoked

Lambda entry point:  handler
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import boto3
from botocore.exceptions import ClientError
from mangum import Mangum
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
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


# ─── MCP Server & Tools ───────────────────────────────────────────────────────

# Disable DNS rebinding protection — this runs behind API Gateway, not on
# localhost, so the Host header will always be the API GW domain.
mcp = FastMCP(
    name="AWS Secrets Manager",
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    instructions=(
        "Manage AWS Secrets Manager secrets. "
        "Available operations: list_secrets, get_secret, describe_secret, "
        "create_secret, update_secret, delete_secret."
    ),
)


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

    return [
        {
            "name": s["Name"],
            "arn": s["ARN"],
            "description": s.get("Description", ""),
            "last_changed": _iso(s.get("LastChangedDate")),
            "tags": {t["Key"]: t["Value"] for t in s.get("Tags", [])},
        }
        for s in resp.get("SecretList", [])
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

    return {
        "name": resp["Name"],
        "arn": resp["ARN"],
        "description": resp.get("Description", ""),
        "created": _iso(resp.get("CreatedDate")),
        "last_changed": _iso(resp.get("LastChangedDate")),
        "last_accessed": _iso(resp.get("LastAccessedDate")),
        "rotation_enabled": resp.get("RotationEnabled", False),
        "deletion_date": _iso(resp.get("DeletedDate")),
        "tags": {t["Key"]: t["Value"] for t in resp.get("Tags", [])},
        "versions": {
            vid: list(stages)
            for vid, stages in resp.get("VersionIdsToStages", {}).items()
        },
    }


@mcp.tool()
def create_secret(
    name: str,
    value: str,
    description: str = "",
    tags: Optional[dict[str, str]] = None,
) -> dict:
    """
    Create a new secret in AWS Secrets Manager.

    Args:
        name: Unique secret name (use / for namespacing, e.g. 'prod/myapp/db').
        value: Secret value — plain string or JSON-encoded object.
        description: Optional human-readable description.
        tags: Optional key/value tags.

    Returns:
        Object with 'name', 'arn', 'version_id'.
    """
    kwargs: dict = {"Name": name, "SecretString": value}
    if description:
        kwargs["Description"] = description
    if tags:
        kwargs["Tags"] = [{"Key": k, "Value": v} for k, v in tags.items()]

    try:
        resp = _sm().create_secret(**kwargs)
    except ClientError as e:
        raise ValueError(f"AWS error creating secret: {e.response['Error']['Message']}") from e

    return {"name": resp["Name"], "arn": resp["ARN"], "version_id": resp["VersionId"]}


@mcp.tool()
def update_secret(
    secret_id: str,
    value: str,
    description: Optional[str] = None,
) -> dict:
    """
    Update the value of an existing secret.

    Args:
        secret_id: Secret name or full ARN.
        value: New secret value.
        description: If provided, also updates the description.

    Returns:
        Object with 'name', 'arn', 'version_id'.
    """
    kwargs: dict = {"SecretId": secret_id, "SecretString": value}
    if description is not None:
        kwargs["Description"] = description

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


# ─── OAuth endpoints (all unauthenticated — no JWT authorizer on these routes) ─

def _base_url(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto", "https")
    host = request.headers.get("host", "")
    return f"{scheme}://{host}"


async def oauth_protected_resource(request: Request) -> JSONResponse:
    """RFC 9728 — points MCP clients to THIS server as the authorization server.
    We act as a DCR facade in front of the real IdP (Cognito/Okta)."""
    base = _base_url(request)
    return JSONResponse(
        {
            # Omitting "resource" intentionally — Okta's default auth server
            # does not support RFC 8707 resource indicators and rejects token
            # exchanges that include the resource parameter.
            "authorization_servers": [base],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["openid", "profile", "email"],
        },
        headers={"Cache-Control": "no-cache, no-store"},
    )


async def oauth_authorization_server(request: Request) -> JSONResponse:
    """RFC 8414 — authorization server metadata.
    Proxies real IdP endpoints and adds our /register DCR endpoint so that
    MCP clients (Claude Code, Cursor) that require DCR can proceed."""
    base = _base_url(request)
    return JSONResponse(
        {
            "issuer": os.environ["OAUTH_ISSUER"],
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
    """RFC 7591 static DCR — always returns the pre-registered client_id.
    No client is actually created; this satisfies MCP clients that require a
    DCR endpoint before they will attempt the OAuth PKCE flow."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    logger.info("DCR /register request body: %s", body)

    import time
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


# ─── Lambda Entry Point ───────────────────────────────────────────────────────
# StreamableHTTPSessionManager raises "can only be called once per instance"
# on Lambda warm starts because Mangum re-runs lifespan on every invocation.
# Fix: create a fresh app (and thus a fresh session manager) per invocation.
# The `mcp` instance with all registered tools is shared across invocations.

def handler(event, context):  # type: ignore[return]
    # Reset cached session manager so streamable_http_app() creates a fresh
    # StreamableHTTPSessionManager instance on every invocation. Without this,
    # warm-start invocations reuse the already-exhausted manager and crash.
    mcp._session_manager = None
    app = mcp.streamable_http_app()
    app.router.routes = [
        Route("/.well-known/oauth-protected-resource",   oauth_protected_resource),
        Route("/.well-known/oauth-authorization-server", oauth_authorization_server),
        Route("/register", register_client, methods=["POST"]),
        Route("/health",   health),
    ] + list(app.router.routes)
    return Mangum(app, lifespan="on")(event, context)

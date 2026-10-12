"""Optional API key for the HTTP server, the *arr way.

Off unless ``MEDIA_ARCHIVIST_API_KEY`` is set. When on, every request
except ``GET /healthz`` carries the key as the ``X-Api-Key`` header or the
``apikey`` query parameter, or comes from an exempt address.

Exempt addresses are the socket peer's, never a forwarded one: loopback,
the private IPv4 ranges, Tailscale (``100.64.0.0/10``,
``fd7a:115c:a1e0::/48``). ``MEDIA_ARCHIVIST_AUTH_EXEMPT`` replaces that
list with comma-separated networks; an empty value exempts nothing. A
request that arrives with ``X-Forwarded-For``, ``Forwarded`` or
``X-Real-IP`` is never exempt, because a reverse proxy on the local network
would otherwise pass every outside client through as local.
"""
from __future__ import annotations

import ipaddress
import os
import secrets
from typing import Iterable, Mapping, Optional, Tuple, Union

ENV_KEY = "MEDIA_ARCHIVIST_API_KEY"
ENV_EXEMPT = "MEDIA_ARCHIVIST_AUTH_EXEMPT"

DEFAULT_EXEMPT = (
    "127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "100.64.0.0/10", "fd7a:115c:a1e0::/48",
)
FORWARDING_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip")
OPEN_PATHS = {"/healthz"}

Network = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]


def parse_networks(spec: str) -> Tuple[Network, ...]:
    """Comma-separated networks; raises ``ValueError`` on a bad one."""
    nets = []
    for part in spec.split(","):
        part = part.strip()
        if part:
            nets.append(ipaddress.ip_network(part, strict=False))
    return tuple(nets)


class ApiKeyAuth:
    def __init__(self, key: Optional[str] = None,
                 exempt: Iterable[Network] = ()) -> None:
        self.key = key or None
        self.exempt = tuple(exempt)

    @classmethod
    def from_env(cls) -> "ApiKeyAuth":
        spec = os.environ.get(ENV_EXEMPT)
        nets = parse_networks(spec) if spec is not None else parse_networks(",".join(DEFAULT_EXEMPT))
        return cls(os.environ.get(ENV_KEY, "").strip() or None, nets)

    @property
    def enabled(self) -> bool:
        return self.key is not None

    def is_exempt(self, peer: Optional[str]) -> bool:
        try:
            ip = ipaddress.ip_address(str(peer or "").split("%")[0])
        except ValueError:
            return False
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return any(ip.version == n.version and ip in n for n in self.exempt)

    def matches(self, given: Optional[str]) -> bool:
        if not self.key or not given:
            return False
        return secrets.compare_digest(given.encode(), self.key.encode())

    def allowed(self, peer: Optional[str], headers: Mapping[str, str],
                query: Mapping[str, str], path: str = "") -> bool:
        """``headers`` must look up case-insensitively (Starlette's do)."""
        if not self.enabled or path in OPEN_PATHS:
            return True
        if self.matches(headers.get("x-api-key") or query.get("apikey")):
            return True
        forwarded = any(headers.get(h) for h in FORWARDING_HEADERS)
        return not forwarded and self.is_exempt(peer)


def install(app, auth: Optional[ApiKeyAuth] = None) -> ApiKeyAuth:
    """Add the key check to ``app`` as HTTP middleware."""
    from fastapi.responses import JSONResponse

    auth = auth or ApiKeyAuth.from_env()
    app.state.auth = auth
    if not auth.enabled:
        return auth

    @app.middleware("http")
    async def _require_key(request, call_next):
        peer = request.client.host if request.client else None
        path = request.url.path
        root = request.scope.get("root_path") or ""
        if root and path.startswith(root):
            path = path[len(root):]
        if auth.allowed(peer, request.headers, request.query_params, path):
            return await call_next(request)
        return JSONResponse(status_code=401, content={"detail": "invalid or missing API key"})

    return auth

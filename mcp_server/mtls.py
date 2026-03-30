"""mTLS client SSL context for MCP Server (AipRpcClient). ``None`` ⇒ plain HTTP."""

import logging
import os
import ssl
from pathlib import Path
from typing import Optional, Union

logger = logging.getLogger(__name__)


def resolve_mtls_base_dir(service_root: Union[str, Path]) -> Path:
    """Certificate root: ``MTLS_BASE_DIR`` if set (absolute, or relative to *service_root*), else *service_root*."""
    root = Path(service_root).resolve()
    raw = os.getenv("MTLS_BASE_DIR", "").strip()
    if not raw:
        return root
    p = Path(raw)
    if p.is_absolute():
        return p.resolve()
    return (root / p).resolve()


def get_client_ssl_context(
    service_root: Union[str, Path],
    aic: Optional[str] = None,
    cert_dir: Optional[str] = None,
    key_dir: Optional[str] = None,
    trust_bundle: Optional[str] = None,
) -> Optional[ssl.SSLContext]:
    """Build client ``ssl.SSLContext`` for mTLS; *service_root* is MCP package root. Returns ``None`` to fall back to HTTP."""
    base = resolve_mtls_base_dir(service_root)

    _aic = aic or os.getenv("LEADER_AIC", "")
    if not _aic:
        logger.debug("mTLS skipped: LEADER_AIC not configured")
        return None

    _cert_dir = Path(cert_dir) if cert_dir else base / os.getenv("MTLS_CERT_DIR", "certs")
    _key_dir = Path(key_dir) if key_dir else base / os.getenv("MTLS_KEY_DIR", "private")
    _trust_name = trust_bundle or os.getenv("MTLS_TRUST_BUNDLE", "trust-bundle.pem")

    cert_file = _cert_dir / f"{_aic}.pem"
    key_file = _key_dir / f"{_aic}.key"
    trust_file = _cert_dir / _trust_name

    missing = [str(f) for f in (cert_file, key_file, trust_file) if not f.exists()]
    if missing:
        logger.info("mTLS disabled — certificate files not found: %s", missing)
        return None

    try:
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        ctx.load_verify_locations(cafile=str(trust_file))
        ctx.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))
        ctx.check_hostname = False
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        logger.info("mTLS client context ready (AIC=%s)", _aic)
        return ctx
    except Exception as exc:
        logger.warning("mTLS context creation failed (%s) — falling back to HTTP", exc)
        return None

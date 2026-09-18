"""Tokens HMAC firmados para QR cliente → scan operario.

Payload opaco (URL-safe): base64url(payload).base64url(sig)
Campos: booking_id, matricula, product, exp (unix ts).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from app.config import settings


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _secret() -> bytes:
    return settings.toma_qr_secret.encode("utf-8")


def _sign(payload_b64: str) -> str:
    sig = hmac.new(_secret(), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return _b64url_encode(sig)


@dataclass(frozen=True)
class TomaTokenPayload:
    booking_id: int
    matricula: str
    product: str
    exp: int

    def as_dict(self) -> dict:
        return {
            "booking_id": self.booking_id,
            "matricula": self.matricula,
            "product": self.product,
            "exp": self.exp,
        }


def mint_toma_token(
    *,
    booking_id: int,
    matricula: str,
    product: str,
    ttl_seconds: int | None = None,
) -> str:
    """Emite token opaco. TTL default: hasta ~48h (cubre turno + margen)."""
    ttl = ttl_seconds if ttl_seconds is not None else settings.toma_qr_ttl_seconds
    payload = {
        "booking_id": int(booking_id),
        "matricula": (matricula or "").strip().upper(),
        "product": (product or "").strip(),
        "exp": int(time.time()) + int(ttl),
    }
    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    return f"{payload_b64}.{_sign(payload_b64)}"


def verify_toma_token(token: str) -> TomaTokenPayload:
    """Valida firma + exp. Lanza ValueError si inválido/expirado (fail-closed)."""
    raw = (token or "").strip()
    if not raw or raw.count(".") != 1:
        raise ValueError("Token inválido.")
    payload_b64, sig = raw.split(".", 1)
    expected = _sign(payload_b64)
    if not hmac.compare_digest(sig, expected):
        raise ValueError("Token inválido o alterado.")
    try:
        data = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Token inválido.") from exc
    try:
        booking_id = int(data["booking_id"])
        matricula = str(data.get("matricula") or "").strip().upper()
        product = str(data.get("product") or "").strip()
        exp = int(data["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Token incompleto.") from exc
    if not matricula:
        raise ValueError("Token sin matrícula.")
    if exp < int(time.time()):
        raise ValueError("Token expirado.")
    return TomaTokenPayload(
        booking_id=booking_id,
        matricula=matricula,
        product=product,
        exp=exp,
    )


def scan_url_for_token(token: str) -> str:
    """URL absoluta que el QR apunta (operador abre y valida)."""
    return f"{settings.base_url}/operador/s/{token}"


def qr_png_data_url(content: str, *, box_size: int = 6, border: int = 2) -> str:
    """PNG QR como data URL para embeber en Mis turnos."""
    import io

    import qrcode

    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=box_size, border=border)
    qr.add_data(content)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"

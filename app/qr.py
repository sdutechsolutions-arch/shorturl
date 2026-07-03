"""QR rendering — branded PNG (colors, size, error-correction, center logo) and
a colored SVG built directly from the module matrix (logo embedded best-effort).
"""
from __future__ import annotations

import base64
import io
import mimetypes

import qrcode
from qrcode.constants import (
    ERROR_CORRECT_H,
    ERROR_CORRECT_L,
    ERROR_CORRECT_M,
    ERROR_CORRECT_Q,
)
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.colormasks import SolidFillColorMask

EC_MAP = {
    "L": ERROR_CORRECT_L,
    "M": ERROR_CORRECT_M,
    "Q": ERROR_CORRECT_Q,
    "H": ERROR_CORRECT_H,
}

_BORDER = 2


def _hex_rgb(value: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
    try:
        h = (value or "").strip().lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) != 6:
            return default
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return default


def _build(url: str, ec: str) -> qrcode.QRCode:
    qr = qrcode.QRCode(
        error_correction=EC_MAP.get((ec or "M").upper(), ERROR_CORRECT_M),
        box_size=10,
        border=_BORDER,
    )
    qr.add_data(url)
    qr.make(fit=True)
    return qr


def render_png(
    url: str,
    *,
    fg: str = "#000000",
    bg: str = "#FFFFFF",
    size: int = 512,
    ec: str = "M",
    logo_path: str | None = None,
) -> bytes:
    qr = _build(url, ec)
    count = qr.modules_count + 2 * _BORDER
    qr.box_size = max(4, round(size / count))  # approximate target px, keep logo crisp

    kwargs = {
        "image_factory": StyledPilImage,
        "color_mask": SolidFillColorMask(
            back_color=_hex_rgb(bg, (255, 255, 255)),
            front_color=_hex_rgb(fg, (0, 0, 0)),
        ),
    }
    if logo_path:
        kwargs["embeded_image_path"] = logo_path  # library forces high EC when embedding

    img = qr.make_image(**kwargs)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_svg(
    url: str,
    *,
    fg: str = "#000000",
    bg: str = "#FFFFFF",
    size: int = 512,
    ec: str = "M",
    logo_path: str | None = None,
) -> bytes:
    qr = _build(url, ec)
    matrix = qr.get_matrix()  # includes border
    n = len(matrix)
    scale = size / n

    parts: list[str] = []
    for r, row in enumerate(matrix):
        for c, dark in enumerate(row):
            if dark:
                x, y = c * scale, r * scale
                parts.append(f"M{x:.2f} {y:.2f}h{scale:.2f}v{scale:.2f}h-{scale:.2f}z")
    path = "".join(parts)

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}" shape-rendering="crispEdges">',
        f'<rect width="{size}" height="{size}" fill="{_safe_color(bg, "#FFFFFF")}"/>',
        f'<path d="{path}" fill="{_safe_color(fg, "#000000")}"/>',
    ]
    if logo_path:
        data_uri = _logo_data_uri(logo_path)
        if data_uri:
            box = size * 0.22
            off = (size - box) / 2
            pad = box * 0.12
            svg.append(
                f'<rect x="{off - pad:.2f}" y="{off - pad:.2f}" '
                f'width="{box + 2 * pad:.2f}" height="{box + 2 * pad:.2f}" '
                f'rx="{pad:.2f}" fill="{_safe_color(bg, "#FFFFFF")}"/>'
            )
            svg.append(
                f'<image x="{off:.2f}" y="{off:.2f}" width="{box:.2f}" height="{box:.2f}" '
                f'href="{data_uri}" preserveAspectRatio="xMidYMid meet"/>'
            )
    svg.append("</svg>")
    return "".join(svg).encode("utf-8")


def _safe_color(value: str, default: str) -> str:
    v = (value or "").strip()
    return v if v.startswith("#") and len(v) in (4, 7) else default


def _logo_data_uri(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            raw = f.read()
        mime = mimetypes.guess_type(path)[0] or "image/png"
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    except OSError:
        return None

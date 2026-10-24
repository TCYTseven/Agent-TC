"""Screenshot encoding. A screenshot is sent to several models in one step, so
encodings are cached by content and shrunk before upload."""

import base64
from functools import lru_cache
from io import BytesIO

from PIL import Image


def _mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:3] == b"GIF":
        return "image/gif"
    return "image/png"


@lru_cache(maxsize=32)
def encode_image(data: bytes, max_side: int = 0, quality: int = 85) -> str:
    """Return a data: URL for the image, downscaled so its longest side is at
    most max_side (0 keeps the size) and re-encoded as JPEG when shrunk."""
    if max_side:
        img = Image.open(BytesIO(data))
        if max(img.size) > max_side:
            scale = max_side / max(img.size)
            img = img.convert("RGB").resize(
                (round(img.width * scale), round(img.height * scale)),
                Image.BILINEAR,
            )
            out = BytesIO()
            img.save(out, format="JPEG", quality=quality)
            data = out.getvalue()
    return f"data:{_mime(data)};base64,{base64.b64encode(data).decode()}"


def to_bytes(image) -> bytes:
    """Accept bytes, a file path or a numpy array."""
    if isinstance(image, (bytes, bytearray)):
        return bytes(image)
    if isinstance(image, str):
        with open(image, "rb") as f:
            return f.read()
    out = BytesIO()
    Image.fromarray(image).save(out, format="PNG")
    return out.getvalue()


def jpeg_bytes(img: Image.Image, quality: int = 85) -> bytes:
    """Encode a PIL image as JPEG: about 10x faster to write than PNG and a
    fraction of the upload."""
    out = BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=quality)
    return out.getvalue()

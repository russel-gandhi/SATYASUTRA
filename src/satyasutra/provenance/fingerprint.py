"""Image fingerprints: SHA-256 (exact copy), pHash (64-bit DCT hash) and PDQ (Meta's 256-bit hash), each for the image and
its horizontal mirror, so a flipped repost still matches. Nothing here stores an image; callers hash the bytes and drop them.

PDQ comes from the `pdqhash` package (MIT bindings over Meta's BSD-licensed PDQ, see docs/licences.md).
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

import numpy as np
import pdqhash
from PIL import Image, ImageOps, UnidentifiedImageError

from ..errors import ForgeError

PHASH_SIDE = 32                 # image is reduced to this square before the DCT
PHASH_KEEP = 8                  # low-frequency block kept: 8 x 8 = 64 bits
PDQ_BITS = 256
PHASH_BITS = PHASH_KEEP * PHASH_KEEP


class BadImage(ForgeError):
    """The bytes are not an image Pillow can decode."""


@dataclass(frozen=True)
class Fingerprint:
    sha256: str
    phash: int
    pdq: int
    phash_flipped: int
    pdq_flipped: int
    pdq_quality: int            # 0 to 100; PDQ says hashes of flat or tiny images (low quality) are unreliable
    width: int
    height: int


def _dct_matrix(n: int) -> np.ndarray:
    k, i = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
    m = np.cos(np.pi * (2 * i + 1) * k / (2 * n)) * np.sqrt(2 / n)
    m[0] /= np.sqrt(2)
    return m


_DCT = _dct_matrix(PHASH_SIDE)


def _bits_to_int(bits: np.ndarray) -> int:
    value = 0
    for b in bits.astype(np.uint8).tolist():
        value = (value << 1) | b
    return value


def phash(image: Image.Image) -> int:
    gray = np.asarray(ImageOps.grayscale(image).resize((PHASH_SIDE, PHASH_SIDE), Image.Resampling.LANCZOS), dtype=np.float64)
    low = (_DCT @ gray @ _DCT.T)[:PHASH_KEEP, :PHASH_KEEP].flatten()
    return _bits_to_int(low > np.median(low[1:]))        # the DC term (overall brightness) is left out of the median


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def decode(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise BadImage(f"not a decodable image ({type(exc).__name__})") from None
    image = ImageOps.exif_transpose(image)
    if image.mode in ("P", "PA", "LA", "RGBA") or "transparency" in image.info:
        rgba = image.convert("RGBA")                      # transparent pixels become white, not whatever colour the file stores
        flat = Image.new("RGB", rgba.size, (255, 255, 255))     # forge: allow-literal white background colour
        flat.paste(rgba, mask=rgba.getchannel("A"))
        return flat
    return image.convert("RGB")


def fingerprint(data: bytes) -> Fingerprint:
    image = decode(data)
    flipped = ImageOps.mirror(image)
    plain, quality = pdqhash.compute(np.asarray(image))
    mirrored, _ = pdqhash.compute(np.asarray(flipped))     # hashed from the mirrored pixels: compute_dihedral's own mirror drifted by up to 18 bits
    return Fingerprint(
        sha256=hashlib.sha256(data).hexdigest(),
        phash=phash(image),
        pdq=_bits_to_int(plain),
        phash_flipped=phash(flipped),
        pdq_flipped=_bits_to_int(mirrored),
        pdq_quality=int(quality),
        width=image.width,
        height=image.height,
    )

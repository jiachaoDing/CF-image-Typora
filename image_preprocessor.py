from __future__ import annotations

import secrets
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from PIL import Image, ImageOps, UnidentifiedImageError


@dataclass(frozen=True)
class CompressionConfig:
    enabled: bool
    output_format: str
    max_width: int
    max_height: int
    jpeg_quality: int
    webp_quality: int
    png_compress_level: int


@dataclass(frozen=True)
class PreparedImage:
    source_path: Path
    upload_path: Path
    output_ext: str


def ensure_saveable_image(image: Image.Image, target_ext: str) -> Image.Image:
    normalized_ext = target_ext.lower()
    if normalized_ext in {".jpg", ".jpeg"} and image.mode not in {"RGB", "L"}:
        return image.convert("RGB")
    if normalized_ext == ".webp" and image.mode == "P":
        return image.convert("RGBA")
    return image


def resize_image(image: Image.Image, config: CompressionConfig) -> Image.Image:
    resized = image.copy()
    resized.thumbnail((config.max_width, config.max_height), Image.Resampling.LANCZOS)
    return resized


def save_compressed_image(
    source_path: Path,
    image: Image.Image,
    config: CompressionConfig,
    temp_dir: Path,
) -> PreparedImage:
    output_ext = ".webp" if config.output_format == "webp" else source_path.suffix.lower()
    output_path = temp_dir / f"{source_path.stem}-{secrets.token_hex(4)}{output_ext}"
    save_image = ensure_saveable_image(image, output_ext)

    save_kwargs: dict[str, object]
    if output_ext in {".jpg", ".jpeg"}:
        save_kwargs = {
            "format": "JPEG",
            "quality": config.jpeg_quality,
            "optimize": True,
            "progressive": True,
        }
    elif output_ext == ".png":
        save_kwargs = {
            "format": "PNG",
            "optimize": True,
            "compress_level": config.png_compress_level,
        }
    elif output_ext == ".webp":
        save_kwargs = {
            "format": "WEBP",
            "quality": config.webp_quality,
            "method": 6,
        }
    else:
        raise ValueError(f"Unsupported output format for compression: {output_ext}")

    save_image.save(output_path, **save_kwargs)
    return PreparedImage(source_path=source_path, upload_path=output_path, output_ext=output_ext)


def prepare_single_image(source_path: Path, config: CompressionConfig, temp_dir: Path) -> PreparedImage:
    if not config.enabled:
        return PreparedImage(source_path=source_path, upload_path=source_path, output_ext=source_path.suffix.lower())

    if source_path.suffix.lower() == ".gif":
        print(f"Skip compress for GIF: {source_path}", file=sys.stderr)
        return PreparedImage(source_path=source_path, upload_path=source_path, output_ext=source_path.suffix.lower())

    try:
        with Image.open(source_path) as image:
            normalized = ImageOps.exif_transpose(image)
            resized = resize_image(normalized, config)
            prepared = save_compressed_image(source_path, resized, config, temp_dir)
    except UnidentifiedImageError as exc:
        raise ValueError(f"Cannot identify image file: {source_path}") from exc
    except OSError as exc:
        raise RuntimeError(f"Image compression failed: {source_path} -> {exc}") from exc

    original_size = source_path.stat().st_size
    compressed_size = prepared.upload_path.stat().st_size
    if compressed_size >= original_size:
        print(
            f"Compression fallback: {source_path.name} kept original file because {compressed_size} >= {original_size}",
            file=sys.stderr,
        )
        return PreparedImage(
            source_path=source_path,
            upload_path=source_path,
            output_ext=source_path.suffix.lower(),
        )

    ratio = compressed_size / original_size if original_size else 1
    print(
        f"Compressed: {source_path.name} {original_size} -> {compressed_size} bytes ({ratio:.1%})",
        file=sys.stderr,
    )
    return prepared


@contextmanager
def prepared_uploads(paths: list[Path], config: CompressionConfig) -> Iterator[list[PreparedImage]]:
    with tempfile.TemporaryDirectory(prefix="typora-r2-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        prepared = [prepare_single_image(path, config, temp_dir) for path in paths]
        yield prepared

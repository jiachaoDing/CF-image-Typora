from __future__ import annotations

import argparse
import mimetypes
import os
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

from image_preprocessor import CompressionConfig, PreparedImage, prepared_uploads


SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SUPPORTED_OUTPUT_FORMATS = {"original", "webp"}


class CliArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


@dataclass(frozen=True)
class AppConfig:
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket_name: str
    public_base_url: str
    compression: CompressionConfig

    @property
    def endpoint_url(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


def configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def parse_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true/false")


def parse_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default

    try:
        value = int(raw_value.strip())
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数") from exc

    if value < minimum or value > maximum:
        raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
    return value


def load_config(env_path: Path) -> AppConfig:
    load_dotenv(dotenv_path=env_path, override=False, encoding="utf-8")

    values = {
        "ACCOUNT_ID": os.getenv("ACCOUNT_ID", "").strip(),
        "ACCESS_KEY_ID": os.getenv("ACCESS_KEY_ID", "").strip(),
        "SECRET_ACCESS_KEY": os.getenv("SECRET_ACCESS_KEY", "").strip(),
        "BUCKET_NAME": os.getenv("BUCKET_NAME", "").strip(),
        "PUBLIC_BASE_URL": os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/"),
    }
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise ValueError(f"缺少必要配置项: {', '.join(missing)}")

    output_format = os.getenv("OUTPUT_FORMAT", "original").strip().lower() or "original"
    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        supported = ", ".join(sorted(SUPPORTED_OUTPUT_FORMATS))
        raise ValueError(f"OUTPUT_FORMAT 仅支持: {supported}")

    compression = CompressionConfig(
        enabled=parse_bool("ENABLE_LOCAL_COMPRESS", True),
        output_format=output_format,
        max_width=parse_int("MAX_WIDTH", 2560, minimum=1, maximum=20000),
        max_height=parse_int("MAX_HEIGHT", 2560, minimum=1, maximum=20000),
        jpeg_quality=parse_int("JPEG_QUALITY", 85, minimum=1, maximum=100),
        webp_quality=parse_int("WEBP_QUALITY", 82, minimum=1, maximum=100),
        png_compress_level=parse_int("PNG_COMPRESS_LEVEL", 6, minimum=0, maximum=9),
    )

    return AppConfig(
        account_id=values["ACCOUNT_ID"],
        access_key_id=values["ACCESS_KEY_ID"],
        secret_access_key=values["SECRET_ACCESS_KEY"],
        bucket_name=values["BUCKET_NAME"],
        public_base_url=values["PUBLIC_BASE_URL"],
        compression=compression,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = CliArgumentParser(
        description="Upload local image files to Cloudflare R2 for Typora Custom Command."
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="One or more image paths passed by Typora.",
    )
    return parser.parse_args(argv)


def validate_files(file_args: Iterable[str]) -> list[Path]:
    validated: list[Path] = []
    for raw_path in file_args:
        path = Path(raw_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")
        if not path.is_file():
            raise ValueError(f"路径不是文件: {path}")
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
            raise ValueError(f"不支持的图片格式: {path.name}，仅支持: {supported}")
        validated.append(path)
    return validated


def build_object_key(output_ext: str) -> str:
    now = datetime.now()
    ext = output_ext.lower().lstrip(".")
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    random_part = secrets.token_hex(4)
    return f"images/{now:%Y/%m/%d}/{timestamp}-{random_part}.{ext}"


def guess_content_type(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def create_s3_client(config: AppConfig):
    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url,
        region_name="auto",
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
        config=Config(signature_version="s3v4"),
    )


def upload_images(images: list[PreparedImage], config: AppConfig) -> list[str]:
    client = create_s3_client(config)
    uploaded_urls: list[str] = []

    for image in images:
        object_key = build_object_key(image.output_ext)
        content_type = guess_content_type(f"file{image.output_ext}")
        print(f"Uploading: {image.source_path}", file=sys.stderr)

        try:
            client.upload_file(
                Filename=str(image.upload_path),
                Bucket=config.bucket_name,
                Key=object_key,
                ExtraArgs={"ContentType": content_type},
            )
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError(f"上传失败: {image.source_path} -> {exc}") from exc

        uploaded_urls.append(f"{config.public_base_url}/{object_key}")

    return uploaded_urls


def main(argv: list[str]) -> int:
    configure_stdio()
    script_dir = Path(__file__).resolve().parent

    try:
        args = parse_args(argv)
        config = load_config(script_dir / ".env")
        files = validate_files(args.files)
        with prepared_uploads(files, config.compression) as images:
            urls = upload_images(images, config)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    for url in urls:
        print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

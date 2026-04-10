# Typora + Cloudflare R2 Uploader

一个给 Typora 使用的本地图片上传工具。

它会在你粘贴图片后由 Typora 调用，本地压缩图片并直接上传到 Cloudflare R2，然后把最终图片 URL 自动写回 Markdown。

## Why

- 轻量，不依赖 PicGo、Worker 或额外服务
- 稳定，直接使用 R2 的 S3-compatible API
- 适合 Windows + Typora 的本地写作流程
- 支持多图上传、中文文件名、自动压缩、可选 WebP

## Requirements

- Python 3.10+
- Typora
- Cloudflare R2 bucket
- R2 Access Key ID / Secret Access Key
- 一个绑定到 R2 bucket 的公开访问域名（或直接使用公共开发 URL）

## How To Use

### 1. Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r .\requirements.txt
Copy-Item .env.example .env
notepad .env
```

在 `.env` 中填写你自己的配置。

### 2. Configure Typora

Typora 中选择：

- `Preferences`
- `Image`
- 上传方式选择 `Custom Command`

命令填写：

```text
"D:\CursorProject\CF-image-Typora\.venv\Scripts\python.exe" "D:\CursorProject\CF-image-Typora\upload_to_r2.py"
```

### 3. Test

手动测试：

```powershell
python .\upload_to_r2.py .\test.png
```

如果成功，你会得到一行图片 URL。

然后回到 Typora，直接粘贴图片即可。成功后，Markdown 会自动写入 R2 图片地址。

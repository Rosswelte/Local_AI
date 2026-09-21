import re
from pathlib import Path

from app.errors import AppError


MAX_OUTPUT_BYTES = 20 * 1024 * 1024
_FORMATS = {
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
    "image/webp": (".webp", b"RIFF"),
}


def save_output(data_dir: str | Path, job_id: int, output_id: int, content: bytes, media_type: str) -> dict:
    if media_type not in _FORMATS or len(content) > MAX_OUTPUT_BYTES:
        raise AppError("output_invalid", "Sortie image invalide ou trop volumineuse", 502)
    suffix, signature = _FORMATS[media_type]
    if not content.startswith(signature):
        raise AppError("output_invalid", "Signature de fichier image invalide", 502)
    if media_type == "image/webp" and content[8:12] != b"WEBP":
        raise AppError("output_invalid", "Signature WebP invalide", 502)
    output_dir = Path(data_dir) / "outputs" / str(job_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    name = f"{output_id:04d}{suffix}"
    path = output_dir / name
    path.write_bytes(content)
    return {
        "id": output_id,
        "name": name,
        "media_type": media_type,
        "size_bytes": len(content),
        "url": f"/api/v1/image/jobs/{job_id}/outputs/{output_id}",
    }


def output_path(data_dir: str | Path, job_id: int, metadata: dict) -> Path:
    name = str(metadata.get("name", ""))
    if not re.fullmatch(r"\d{4}\.(png|jpg|webp)", name):
        raise AppError("output_invalid", "Nom de sortie invalide", 500)
    root = (Path(data_dir) / "outputs" / str(job_id)).resolve()
    path = (root / name).resolve()
    if root not in path.parents:
        raise AppError("output_invalid", "Chemin de sortie invalide", 500)
    return path

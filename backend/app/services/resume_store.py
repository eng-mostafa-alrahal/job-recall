"""Stores one PDF copy per resume inside the local service data directory."""

import re
import tempfile
from pathlib import Path

from app.config import Settings

MAX_RESUME_PDF_BYTES = 15 * 1024 * 1024


def resume_storage_dir(settings: Settings) -> Path:
    """
    PDFs live next to the SQLite database (`data/resumes/`), which Docker already
    mounts as a persistent volume. In-memory test databases use a temp folder.
    """
    url = settings.jobrecall_db_url
    if url.startswith("sqlite:///") and ":memory:" not in url:
        raw = url.removeprefix("sqlite:///")
        db_path = Path(raw).expanduser()
        if not db_path.is_absolute():
            db_path = Path.cwd() / db_path
        base = db_path.parent
    else:
        base = Path(tempfile.gettempdir()) / "jobrecall" / "memory-db"

    directory = base / "resumes"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def stored_pdf_path(resume_id: str, settings: Settings) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", resume_id)
    return resume_storage_dir(settings) / f"{safe_id}.pdf"


def save_resume_pdf(resume_id: str, pdf_bytes: bytes, settings: Settings) -> Path:
    if not pdf_bytes:
        raise ValueError("PDF file is empty.")
    if len(pdf_bytes) > MAX_RESUME_PDF_BYTES:
        raise ValueError("PDF is larger than 15 MB.")
    path = stored_pdf_path(resume_id, settings)
    path.write_bytes(pdf_bytes)
    return path


def delete_resume_pdf(resume_id: str, settings: Settings) -> None:
    path = stored_pdf_path(resume_id, settings)
    if path.is_file():
        path.unlink()


def resume_pdf_info(resume_id: str, settings: Settings) -> tuple[bool, int | None]:
    path = stored_pdf_path(resume_id, settings)
    if not path.is_file():
        return False, None
    return True, path.stat().st_size


def safe_download_filename(filename: str | None, fallback_label: str) -> str:
    raw = (filename or "").replace("\\", "/").split("/")[-1].strip()
    raw = re.sub(r'[<>:"|?*\x00-\x1f]', "", raw)
    if not raw.lower().endswith(".pdf"):
        stem = re.sub(r"[^\w.\- ]+", "", fallback_label).strip() or "resume"
        raw = f"{stem}.pdf"
    return raw[:180]

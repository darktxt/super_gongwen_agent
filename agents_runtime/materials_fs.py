from __future__ import annotations

from pathlib import Path
import re
import zlib
import zipfile
from xml.etree import ElementTree as ET

try:
    from docx import Document
except ImportError:  # pragma: no cover - dependency presence is environment-specific
    Document = None  # type: ignore[assignment]

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - dependency presence is environment-specific
    PdfReader = None  # type: ignore[assignment]

SUPPORTED_MATERIAL_SUFFIXES = {
    ".pdf",
    ".docx",
    ".md",
    ".txt",
    ".json",
}

TEXT_MATERIAL_SUFFIXES = {
    ".md",
    ".txt",
    ".json",
}

MATERIALS_DIR_NAME = "materials"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve_materials_root(*, working_root: Path) -> Path:
    project_materials_root = (PROJECT_ROOT / MATERIALS_DIR_NAME).resolve()
    if project_materials_root.exists():
        return project_materials_root
    return (working_root / MATERIALS_DIR_NAME).resolve()


def _coerce_material_root(
    root_value: str | Path,
    *,
    materials_root: Path,
) -> Path | None:
    raw_root = str(root_value or "").strip()
    if not raw_root or raw_root in {".", "./", MATERIALS_DIR_NAME, f"./{MATERIALS_DIR_NAME}"}:
        return materials_root

    normalized = Path(raw_root).expanduser()
    project_root = materials_root.parent
    candidates: list[Path] = []

    if normalized.is_absolute():
        candidates.append(normalized.resolve())
    elif normalized.parts and normalized.parts[0] == MATERIALS_DIR_NAME:
        candidates.append((project_root / normalized).resolve())
    else:
        candidates.append((materials_root / normalized).resolve())
        candidates.append((project_root / normalized).resolve())

    for resolved in candidates:
        if resolved == materials_root:
            return resolved
        try:
            resolved.relative_to(materials_root)
            return resolved
        except ValueError:
            continue
    return None


def resolve_material_roots(roots: list[str | Path], *, working_root: Path) -> list[Path]:
    materials_root = resolve_materials_root(working_root=working_root)
    if not materials_root.exists():
        return []

    requested = roots or [MATERIALS_DIR_NAME]
    resolved_roots: list[Path] = []
    for root_value in requested:
        resolved = _coerce_material_root(root_value, materials_root=materials_root)
        if resolved and resolved.exists() and resolved not in resolved_roots:
            resolved_roots.append(resolved)
    return resolved_roots or [materials_root]


def resolve_material_path(path_value: str | Path, *, working_root: Path) -> Path:
    materials_root = resolve_materials_root(working_root=working_root)
    if not materials_root.exists():
        raise FileNotFoundError(f"Materials root does not exist: {materials_root}")

    path = Path(path_value).expanduser()
    if not path.is_absolute():
        normalized = Path(str(path_value or "").strip())
        if normalized.parts and normalized.parts[0] == MATERIALS_DIR_NAME:
            path = materials_root.parent / normalized
        else:
            path = materials_root / normalized
    resolved = path.resolve()
    try:
        resolved.relative_to(materials_root)
    except ValueError as exc:
        raise ValueError(f"Path is outside materials root: {resolved}") from exc
    return resolved


def iter_files(roots: list[str | Path], *, working_root: Path) -> list[Path]:
    discovered: list[Path] = []
    for root in resolve_material_roots(roots, working_root=working_root):
        if root.is_file():
            if not _is_supported_material_file(root):
                continue
            discovered.append(root)
            continue
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and _is_supported_material_file(path):
                discovered.append(path)
    return discovered


def read_material_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in TEXT_MATERIAL_SUFFIXES:
        return _safe_read_text(path)
    if suffix == ".docx":
        return _read_docx_text(path)
    if suffix == ".pdf":
        return _read_pdf_text(path)
    raise ValueError(f"Unsupported material format: {path.suffix or '<none>'}")


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def _is_supported_material_file(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_MATERIAL_SUFFIXES


def _read_docx_text(path: Path) -> str:
    if Document is not None:
        document = Document(path)
        lines = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
        return "\n".join(lines)

    with zipfile.ZipFile(path) as archive:
        xml_bytes = archive.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    texts = [
        node.text.strip()
        for node in root.iter()
        if node.tag.endswith("}t") and str(node.text or "").strip()
    ]
    return "\n".join(texts)


def _read_pdf_text(path: Path) -> str:
    if PdfReader is not None:
        reader = PdfReader(str(path))
        pages = [str(page.extract_text() or "").strip() for page in reader.pages]
        text = "\n".join(page for page in pages if page).strip()
        if text:
            return text

    data = path.read_bytes()
    chunks: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, flags=re.DOTALL):
        stream = match.group(1)
        decoded_candidates = [stream]
        try:
            decoded_candidates.append(zlib.decompress(stream))
        except zlib.error:
            pass
        for candidate in decoded_candidates:
            extracted = _extract_text_from_pdf_stream(candidate)
            if extracted:
                chunks.append(extracted)
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def _extract_text_from_pdf_stream(stream: bytes) -> str:
    pieces: list[str] = []
    for raw in re.findall(rb"\((.*?)\)\s*Tj", stream, flags=re.DOTALL):
        text = _decode_pdf_literal(raw)
        if text:
            pieces.append(text)

    for raw_group in re.findall(rb"\[(.*?)\]\s*TJ", stream, flags=re.DOTALL):
        for raw in re.findall(rb"\((.*?)\)", raw_group, flags=re.DOTALL):
            text = _decode_pdf_literal(raw)
            if text:
                pieces.append(text)

    normalized = "\n".join(piece.strip() for piece in pieces if piece.strip())
    return re.sub(r"\n{3,}", "\n\n", normalized).strip()


def _decode_pdf_literal(raw: bytes) -> str:
    text = raw.replace(rb"\(", b"(").replace(rb"\)", b")").replace(rb"\n", b"\n")
    text = text.replace(rb"\r", b"\r").replace(rb"\t", b"\t").replace(rb"\\", b"\\")
    for encoding in ("utf-8", "utf-16-be", "latin-1"):
        try:
            return text.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return text.decode("latin-1", errors="ignore").strip()

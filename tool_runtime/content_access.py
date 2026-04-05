from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import difflib
import re
import zlib
import zipfile
from xml.etree import ElementTree as ET

from utils.serialization import JsonDataclassMixin

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

MATERIALS_DIR_NAME = "materials"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(slots=True)
class SearchHit(JsonDataclassMixin):
    path: str
    title: str
    kind: str
    size: int
    last_modified: str | None
    discovered_by: str
    preview: str = ""


@dataclass(slots=True)
class GrepHit(JsonDataclassMixin):
    path: str
    line_no: int
    line_text: str


@dataclass(slots=True)
class _SearchMatch:
    hit: SearchHit
    sort_key: tuple[object, ...]


def resolve_path(path_value: str | Path, *, working_root: Path) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = working_root / path
    return path.resolve()


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
        resolved = _coerce_material_root(
            root_value,
            materials_root=materials_root,
        )
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


def list_materials(
    roots: list[str | Path],
    *,
    working_root: Path,
    limit: int = 200,
) -> list[SearchHit]:
    items: list[SearchHit] = []
    for path in iter_files(roots, working_root=working_root)[:limit]:
        items.append(_build_search_hit(path, discovered_by="list"))
    return items


def search_materials(
    query: str,
    roots: list[str | Path],
    *,
    working_root: Path,
    limit: int = 20,
) -> list[SearchHit]:
    normalized_query = _normalize_search_query(query)
    search_terms = _extract_search_terms(normalized_query)
    matches: list[_SearchMatch] = []

    if not normalized_query:
        return []

    for path in iter_files(roots, working_root=working_root):
        name_text = path.name.lower()
        exact_name_hit = normalized_query in name_text
        name_term_hits = _collect_query_hits(name_text, search_terms)

        preview = ""
        exact_content_hit = False
        content_term_hits: set[str] = set()
        material_text = ""

        if is_text_file(path):
            material_text = read_material_text(path)
            if material_text:
                preview, exact_content_hit, content_term_hits = _find_preview(
                    material_text,
                    normalized_query,
                    search_terms,
                )

        if not (exact_name_hit or name_term_hits or exact_content_hit or content_term_hits):
            continue

        discovered_by = "search_name" if (exact_name_hit or name_term_hits) else "search_content"
        hit = _build_search_hit(path, discovered_by=discovered_by)
        hit.preview = preview or _build_preview(material_text)
        matches.append(
            _SearchMatch(
                hit=hit,
                sort_key=(
                    -int(exact_name_hit),
                    -len(name_term_hits),
                    -int(exact_content_hit),
                    -len(content_term_hits),
                    -int(bool(hit.preview)),
                    path.name.lower(),
                    str(path),
                ),
            )
        )

    matches.sort(key=lambda item: item.sort_key)
    return [match.hit for match in matches[:limit]]


def grep_materials(
    pattern: str,
    targets: list[str | Path],
    *,
    working_root: Path,
    case_sensitive: bool = False,
    limit: int = 50,
) -> list[GrepHit]:
    flags = 0 if case_sensitive else re.IGNORECASE
    regex = re.compile(pattern, flags=flags)
    hits: list[GrepHit] = []

    for path in iter_files(targets, working_root=working_root):
        if not is_text_file(path):
            continue
        text = read_material_text(path)
        if not text:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                hits.append(
                    GrepHit(
                        path=str(path),
                        line_no=line_no,
                        line_text=line.strip(),
                    )
                )
                if len(hits) >= limit:
                    return hits
    return hits


def read_material(
    target: str | Path,
    *,
    working_root: Path,
    start_line: int | None = None,
    end_line: int | None = None,
    max_chars: int | None = None,
) -> dict[str, object]:
    path = resolve_material_path(target, working_root=working_root)
    text = read_material_text(path)
    lines = text.splitlines()
    resolved_start_line = max((start_line or 1), 1) if lines else 0
    resolved_end_line = end_line if end_line is not None else len(lines)
    if lines:
        resolved_end_line = min(max(resolved_end_line, resolved_start_line), len(lines))
    else:
        resolved_end_line = 0
    if start_line is not None or end_line is not None:
        start_index = max((start_line or 1) - 1, 0)
        end_index = resolved_end_line if resolved_end_line else len(lines)
        text = "\n".join(lines[start_index:end_index])
    if max_chars is not None:
        text = text[:max_chars]
    return {
        "path": str(path),
        "text": text,
        "start_line": resolved_start_line,
        "end_line": resolved_end_line,
        "preview": _build_preview(text),
        "selected_files": [str(path)],
    }


def diff_text(old_text: str, new_text: str, *, context: int = 2) -> str:
    return "\n".join(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile="before",
            tofile="after",
            n=context,
            lineterm="",
        )
    )


def is_text_file(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_MATERIAL_SUFFIXES


def safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def read_material_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt", ".json"}:
        return safe_read_text(path)
    if suffix == ".docx":
        return _read_docx_text(path)
    if suffix == ".pdf":
        return _read_pdf_text(path)
    raise ValueError(f"Unsupported material format: {path.suffix or '<none>'}")


def _normalize_search_query(query: str) -> str:
    return re.sub(r"\s+", " ", str(query or "").strip().lower())


def _extract_search_terms(normalized_query: str) -> list[str]:
    if not normalized_query:
        return []

    raw_terms = [part.strip() for part in normalized_query.split(" ")] if " " in normalized_query else [normalized_query]
    terms: list[str] = []
    for term in raw_terms:
        if not term or term in terms:
            continue
        terms.append(term)
    return terms


def _collect_query_hits(haystack: str, search_terms: list[str]) -> set[str]:
    return {term for term in search_terms if term and term in haystack}


def _find_preview(
    text: str,
    normalized_query: str,
    search_terms: list[str],
) -> tuple[str, bool, set[str]]:
    normalized_text = text.lower()
    exact_content_hit = normalized_query in normalized_text
    content_term_hits = _collect_query_hits(normalized_text, search_terms)

    if not exact_content_hit and not content_term_hits:
        return "", False, set()

    best_preview = ""
    best_score = (-1, -1, "")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        normalized_line = line.lower()
        line_exact_hit = int(normalized_query in normalized_line)
        line_term_hits = _collect_query_hits(normalized_line, search_terms)
        if not line_exact_hit and not line_term_hits:
            continue
        score = (line_exact_hit, len(line_term_hits), line)
        if score > best_score:
            best_score = score
            best_preview = line

    if best_preview:
        return best_preview, exact_content_hit, content_term_hits
    return _build_preview(text), exact_content_hit, content_term_hits


def _build_preview(text: str, limit: int = 240) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 3, 0)].rstrip() + "..."


def _build_search_hit(path: Path, *, discovered_by: str) -> SearchHit:
    stat = path.stat()
    kind = path.suffix.lower().lstrip(".") or "text"
    return SearchHit(
        path=str(path),
        title=path.name,
        kind=kind,
        size=stat.st_size,
        last_modified=str(stat.st_mtime),
        discovered_by=discovered_by,
    )


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

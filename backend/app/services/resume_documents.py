"""Bounded, in-memory text extraction for uploaded resume documents."""

from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.exceptions import AppError
from app.schemas.resumes import ResumeDocumentExtractResponse

MAX_RESUME_FILE_BYTES = 5 * 1024 * 1024
MAX_RESUME_TEXT_CHARS = 50_000
MAX_DOCX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 20
SUPPORTED_EXTENSIONS = frozenset({".pdf", ".docx", ".txt"})
MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}


class ResumeDocumentService:
    """Extract normalized resume text without retaining the uploaded file."""

    def extract(
        self, *, filename: str, media_type: str, content: bytes
    ) -> ResumeDocumentExtractResponse:
        safe_name = Path(filename.replace("\\", "/")).name.strip()
        suffix = Path(safe_name).suffix.casefold()
        if not safe_name or suffix not in SUPPORTED_EXTENSIONS:
            raise _document_error(
                "RESUME_FILE_FORMAT_UNSUPPORTED",
                "仅支持 PDF、DOCX 和 TXT 格式的简历",
            )
        if not content or len(content) > MAX_RESUME_FILE_BYTES:
            raise _document_error(
                "RESUME_FILE_SIZE_INVALID",
                "简历文件不能为空且不能超过 5 MiB",
                status_code=413,
            )
        normalized_media_type = media_type.partition(";")[0].strip().casefold()
        allowed_media_types = {MEDIA_TYPES[suffix], "application/octet-stream"}
        if normalized_media_type and normalized_media_type not in allowed_media_types:
            raise _document_error(
                "RESUME_FILE_FORMAT_UNSUPPORTED",
                "文件扩展名与内容类型不匹配",
            )

        try:
            if suffix == ".pdf":
                text = self._extract_pdf(content)
            elif suffix == ".docx":
                text = self._extract_docx(content)
            else:
                text = self._extract_txt(content)
        except AppError:
            raise
        except (
            BadZipFile,
            ElementTree.ParseError,
            OSError,
            PdfReadError,
            UnicodeError,
            ValueError,
        ) as exc:
            raise _document_error(
                "RESUME_FILE_PARSE_FAILED",
                "无法解析该简历文件，请确认文件未损坏或改用粘贴文本",
            ) from exc

        normalized_text = _normalize_text(text)
        if len(normalized_text) < 20:
            raise _document_error(
                "RESUME_FILE_TEXT_EMPTY",
                "未提取到足够的文本；扫描版 PDF 暂不支持，请改用可复制文本的文件",
            )
        if len(normalized_text) > MAX_RESUME_TEXT_CHARS:
            raise _document_error(
                "RESUME_FILE_TEXT_TOO_LONG",
                "提取后的简历文本不能超过 50000 个字符",
            )
        return ResumeDocumentExtractResponse(
            filename=safe_name,
            media_type=MEDIA_TYPES[suffix],
            character_count=len(normalized_text),
            source_text=normalized_text,
        )

    @staticmethod
    def _extract_pdf(content: bytes) -> str:
        if not content.startswith(b"%PDF-"):
            raise ValueError("invalid PDF signature")
        reader = PdfReader(BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise _document_error(
                "RESUME_FILE_PARSE_FAILED",
                "暂不支持加密 PDF，请解除密码后重试",
            )
        if len(reader.pages) > MAX_PDF_PAGES:
            raise _document_error(
                "RESUME_FILE_SIZE_INVALID",
                "PDF 页数不能超过 20 页",
                status_code=413,
            )
        return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)

    @staticmethod
    def _extract_docx(content: bytes) -> str:
        if not content.startswith(b"PK"):
            raise ValueError("invalid DOCX signature")
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if sum(item.file_size for item in entries) > MAX_DOCX_UNCOMPRESSED_BYTES:
                raise _document_error(
                    "RESUME_FILE_SIZE_INVALID",
                    "DOCX 解压后的内容超过安全限制",
                    status_code=413,
                )
            try:
                document = archive.read("word/document.xml")
            except KeyError as exc:
                raise ValueError("missing DOCX document.xml") from exc
        if b"<!DOCTYPE" in document.upper() or b"<!ENTITY" in document.upper():
            raise ValueError("unsafe DOCX XML declaration")
        root = ElementTree.fromstring(document)
        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs: list[str] = []
        for paragraph in root.iter(f"{namespace}p"):
            value = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t"))
            if value.strip():
                paragraphs.append(value.strip())
        return "\n".join(paragraphs)

    @staticmethod
    def _extract_txt(content: bytes) -> str:
        if content.startswith((b"\xff\xfe", b"\xfe\xff")):
            return content.decode("utf-16")
        return content.decode("utf-8-sig")


def _normalize_text(value: str) -> str:
    lines = [" ".join(line.replace("\x00", "").split()) for line in value.splitlines()]
    normalized: list[str] = []
    previous_blank = False
    for line in lines:
        if line:
            normalized.append(line)
            previous_blank = False
        elif normalized and not previous_blank:
            normalized.append("")
            previous_blank = True
    return "\n".join(normalized).strip()


def _document_error(code: str, message: str, *, status_code: int = 422) -> AppError:
    return AppError(code=code, message=message, status_code=status_code)

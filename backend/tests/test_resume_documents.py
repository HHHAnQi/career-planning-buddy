"""Resume file extraction and confirmed uploaded-version contracts."""

from http import HTTPStatus
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from httpx import AsyncClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.core.exceptions import AppError
from app.services.resume_documents import MAX_RESUME_FILE_BYTES, ResumeDocumentService
from tests.test_profile_api import bearer, guest_login


def _docx_bytes(*paragraphs: str) -> bytes:
    document = "".join(
        f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>" for paragraph in paragraphs
    )
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "word/document.xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f"<w:body>{document}</w:body></w:document>"
            ),
        )
    return output.getvalue()


def _blank_pdf_bytes() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(output)
    return output.getvalue()


def _text_pdf_bytes() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}  # noqa: SLF001
            )
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 150 Td (Backend engineer FastAPI PostgreSQL testing) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)  # noqa: SLF001
    writer.write(output)
    return output.getvalue()


def test_resume_document_service_extracts_txt_and_docx() -> None:
    service = ResumeDocumentService()
    text = "张三\n后端工程师\n负责 FastAPI、PostgreSQL 服务开发与自动化测试。"

    txt = service.extract(filename="张三.txt", media_type="text/plain", content=text.encode())
    docx = service.extract(
        filename="张三.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        content=_docx_bytes("张三 · 后端工程师", "负责 FastAPI、PostgreSQL 服务开发与自动化测试。"),
    )

    assert txt.source_text == text
    assert docx.source_text == "张三 · 后端工程师\n负责 FastAPI、PostgreSQL 服务开发与自动化测试。"
    assert docx.character_count == len(docx.source_text)


def test_resume_document_service_extracts_text_pdf() -> None:
    extracted = ResumeDocumentService().extract(
        filename="resume.pdf", media_type="application/pdf", content=_text_pdf_bytes()
    )

    assert extracted.source_text == "Backend engineer FastAPI PostgreSQL testing"
    assert extracted.media_type == "application/pdf"


@pytest.mark.parametrize(
    ("filename", "media_type", "content", "code"),
    [
        ("resume.exe", "application/octet-stream", b"test", "RESUME_FILE_FORMAT_UNSUPPORTED"),
        ("resume.pdf", "application/pdf", _blank_pdf_bytes(), "RESUME_FILE_TEXT_EMPTY"),
        (
            "resume.txt",
            "text/plain",
            b"x" * (MAX_RESUME_FILE_BYTES + 1),
            "RESUME_FILE_SIZE_INVALID",
        ),
    ],
    ids=["unsupported", "blank-pdf", "oversized"],
)
def test_resume_document_service_rejects_unsupported_empty_and_oversized_files(
    filename: str, media_type: str, content: bytes, code: str
) -> None:
    with pytest.raises(AppError) as raised:
        ResumeDocumentService().extract(
            filename=filename, media_type=media_type, content=content
        )
    assert raised.value.code == code


@pytest.mark.asyncio
async def test_resume_extract_api_requires_auth_and_returns_editable_preview(
    api_client: AsyncClient,
) -> None:
    content = "李四\nPython 后端工程师\n负责接口设计、数据库建模和持续交付。".encode()
    unauthorized = await api_client.post(
        "/api/v1/resume-versions/extract",
        files={"file": ("resume.txt", content, "text/plain")},
    )
    token, _, _ = await guest_login(api_client)
    extracted = await api_client.post(
        "/api/v1/resume-versions/extract",
        files={"file": ("resume.txt", content, "text/plain")},
        headers=bearer(token),
    )

    assert unauthorized.status_code == HTTPStatus.UNAUTHORIZED
    assert extracted.status_code == HTTPStatus.OK
    assert extracted.json() == {
        "filename": "resume.txt",
        "media_type": "text/plain",
        "character_count": len(content.decode()),
        "source_text": content.decode(),
    }


@pytest.mark.asyncio
async def test_confirmed_upload_is_persisted_as_uploaded_file(api_client: AsyncClient) -> None:
    token, _, _ = await guest_login(api_client)
    created = await api_client.post(
        "/api/v1/resume-versions",
        json={
            "label": "上传简历",
            "source_text": "王五，后端工程师，负责 Python API、PostgreSQL 和自动化测试。",
            "source_type": "uploaded_file",
            "source_filename": "resume.pdf",
            "source_media_type": "application/pdf",
        },
        headers={**bearer(token), "Idempotency-Key": "uploaded-resume"},
    )

    assert created.status_code == HTTPStatus.CREATED
    assert created.json()["source_type"] == "uploaded_file"
    assert created.json()["structured"]["source_file"] == {
        "filename": "resume.pdf",
        "media_type": "application/pdf",
    }

    mismatched = await api_client.post(
        "/api/v1/resume-versions",
        json={
            "label": "伪造格式",
            "source_text": "这是一段长度足够但文件扩展名和媒体类型不匹配的简历文本。",
            "source_type": "uploaded_file",
            "source_filename": "resume.exe",
            "source_media_type": "application/pdf",
        },
        headers={**bearer(token), "Idempotency-Key": "mismatched-upload"},
    )
    assert mismatched.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

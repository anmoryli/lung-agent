"""患者聊天上传文件的结构化文本提取。"""

from __future__ import annotations

import csv
import io
from email import policy
from email.parser import BytesParser
from pathlib import PurePath
from typing import Any

MAX_REQUEST_BYTES = 20 * 1024 * 1024
MAX_FILE_COUNT = 5
MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_EXTRACTED_CHARS = 12_000
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".xlsm", ".xls", ".png", ".jpg", ".jpeg", ".txt", ".csv"}


def parse_multipart(content_type: str, body: bytes) -> list[dict[str, Any]]:
    """拆出 multipart 文件，并在交给解析库前限制大小和格式。"""
    if len(body) > MAX_REQUEST_BYTES:
        raise ValueError("上传请求不能超过 20 MB。")
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii") + body
    )
    if not message.is_multipart():
        raise ValueError("请用 multipart/form-data 上传文件。")

    files = []
    for part in message.iter_parts():
        filename = part.get_filename()
        if not filename:
            continue
        name = PurePath(filename.replace("\\", "/")).name.strip()
        extension = PurePath(name).suffix.lower()
        content = part.get_payload(decode=True) or b""
        if not name or len(name) > 240 or "\x00" in name:
            raise ValueError("文件名无效或过长。")
        if extension not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"不支持 {name}，可上传 PDF、DOCX、XLSX、XLS、PNG、JPEG、TXT 或 CSV。")
        if not content or len(content) > MAX_FILE_BYTES:
            raise ValueError(f"{name} 不能为空，且不能超过 15 MB。")
        files.append({"name": name, "extension": extension, "content": content})
    if not files:
        raise ValueError("请求中没有文件。")
    if len(files) > MAX_FILE_COUNT:
        raise ValueError("一次最多上传 5 个文件。")
    return files


def extract_files(files: list[dict[str, Any]]) -> dict[str, Any]:
    """按文件类型提取文本，保留文档页、表格和工作表的边界。"""
    results = []
    chunks = []
    remaining = MAX_EXTRACTED_CHARS
    truncated = False
    for item in files:
        extracted = _extract_one(item)
        text = extracted.strip()
        take = min(len(text), remaining)
        clipped = text[:take]
        if len(text) > take:
            truncated = True
        results.append({"name": item["name"], "text": clipped, "truncated": len(text) > take})
        if clipped:
            chunks.append(f"[文件名：{item['name']}]\n{clipped}")
        remaining -= take
    if remaining <= 0:
        truncated = True
    return {"files": results, "combinedText": "\n\n".join(chunks), "truncated": truncated}


def _extract_one(item: dict[str, Any]) -> str:
    extension = item["extension"]
    content = item["content"]
    if extension == ".pdf":
        return _extract_pdf(content)
    if extension == ".docx":
        return _extract_docx(content)
    if extension in {".xlsx", ".xlsm"}:
        return _extract_xlsx(content)
    if extension == ".xls":
        return _extract_xls(content)
    if extension in {".png", ".jpg", ".jpeg"}:
        return _extract_image(content, extension[1:])
    return _decode_text(content)


def _extract_pdf(content: bytes) -> str:
    import pymupdf

    document = pymupdf.open(stream=content, filetype="pdf")
    pages = []
    for page_number, page in enumerate(document, start=1):
        tables = []
        try:
            tables = page.find_tables().tables
        except Exception:
            tables = []
        elements = []
        table_boxes = []
        for table in tables:
            box = table.bbox
            table_boxes.append(box)
            rows = table.extract()
            if rows:
                elements.append((box[1], box[0], _markdown_table(rows)))
        for block in page.get_text("blocks", sort=True):
            x0, y0, x1, y1, text = block[:5]
            if not text.strip() or any(_overlap((x0, y0, x1, y1), box) > 0.35 for box in table_boxes):
                continue
            elements.append((y0, x0, text.strip()))
        page_text = "\n\n".join(text for _, _, text in sorted(elements))
        if len(page_text.strip()) < 20:
            page_text = page.get_text("text", textpage=page.get_textpage_ocr(language="chi_sim+eng", dpi=200, full=True), sort=True)
        pages.append(f"[第 {page_number} 页]\n{page_text.strip()}".rstrip())
    document.close()
    return "\n\n".join(pages)


def _extract_image(content: bytes, extension: str) -> str:
    import pymupdf

    filetype = "jpeg" if extension in {"jpg", "jpeg"} else "png"
    document = pymupdf.open(stream=content, filetype=filetype)
    page = document[0]
    text = page.get_text("text", textpage=page.get_textpage_ocr(language="chi_sim+eng", dpi=200, full=True), sort=True)
    document.close()
    return text


def _extract_docx(content: bytes) -> str:
    from docx import Document
    from docx.oxml.text.paragraph import CT_P
    from docx.oxml.table import CT_Tbl
    from docx.text.paragraph import Paragraph
    from docx.table import Table

    document = Document(io.BytesIO(content))
    parts = []
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            text = Paragraph(child, document).text.strip()
            if text:
                parts.append(text)
        elif isinstance(child, CT_Tbl):
            table = Table(child, document)
            parts.append(_markdown_table([[cell.text.strip() for cell in row.cells] for row in table.rows]))
    return "\n\n".join(parts)


def _extract_xlsx(content: bytes) -> str:
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    sheets = []
    for sheet in workbook.worksheets:
        rows = [["" if value is None else str(value) for value in row] for row in sheet.iter_rows(values_only=True)]
        while rows and not any(cell.strip() for cell in rows[-1]):
            rows.pop()
        if rows:
            sheets.append(f"[工作表：{sheet.title}]\n" + _markdown_table(rows))
    workbook.close()
    return "\n\n".join(sheets)


def _extract_xls(content: bytes) -> str:
    import xlrd

    workbook = xlrd.open_workbook(file_contents=content, on_demand=True)
    sheets = []
    for sheet in workbook.sheets():
        rows = [[str(value) for value in sheet.row_values(index)] for index in range(sheet.nrows)]
        if rows:
            sheets.append(f"[工作表：{sheet.name}]\n" + _markdown_table(rows))
    workbook.release_resources()
    return "\n\n".join(sheets)


def _decode_text(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("gb18030")


def _markdown_table(rows: list[list[Any]]) -> str:
    normalized = [[str(value or "").replace("\r", " ").replace("\n", " / ").replace("|", "\\|").strip() for value in row] for row in rows]
    width = max((len(row) for row in normalized), default=0)
    if not width:
        return ""
    normalized = [row + [""] * (width - len(row)) for row in normalized]
    header = normalized[0]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in normalized[1:])
    return "\n".join(lines)


def _overlap(first: tuple[float, ...], second: tuple[float, ...]) -> float:
    x0, y0, x1, y1 = first
    a0, b0, a1, b1 = second
    intersection = max(0, min(x1, a1) - max(x0, a0)) * max(0, min(y1, b1) - max(y0, b0))
    area = max(1, (x1 - x0) * (y1 - y0))
    return intersection / area

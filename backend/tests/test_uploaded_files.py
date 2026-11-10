from __future__ import annotations

import io
import unittest
from email.message import EmailMessage
from email.policy import SMTP

import pymupdf
from docx import Document
from openpyxl import Workbook

from backend.api.v1.uploaded_files import MAX_EXTRACTED_CHARS, extract_files, parse_multipart


class UploadedFileTests(unittest.TestCase):
    def test_pdf_table_is_kept_as_markdown(self) -> None:
        document = pymupdf.open()
        page = document.new_page()
        for rect in ((40, 40, 160, 70), (160, 40, 280, 70), (40, 70, 160, 100), (160, 70, 280, 100)):
            page.draw_rect(rect, width=1)
        for point, text in (((45, 60), "Test"), ((165, 60), "Result"), ((45, 90), "Value"), ((165, 90), "120")):
            page.insert_text(point, text)

        result = extract_files([self._file("report.pdf", ".pdf", document.tobytes())])

        self.assertIn("| Test | Result |", result["files"][0]["text"])
        self.assertIn("| Value | 120 |", result["files"][0]["text"])

    def test_docx_paragraphs_and_tables_are_extracted(self) -> None:
        document = Document()
        document.add_paragraph("Report summary")
        table = document.add_table(rows=2, cols=2)
        for cell, value in zip(
            (table.cell(0, 0), table.cell(0, 1), table.cell(1, 0), table.cell(1, 1)),
            ("Test", "Result", "Value", "120"),
        ):
            cell.text = value
        content = io.BytesIO()
        document.save(content)

        text = extract_files([self._file("report.docx", ".docx", content.getvalue())])["files"][0]["text"]

        self.assertIn("Report summary", text)
        self.assertIn("| Value | 120 |", text)

    def test_xlsx_sheet_name_and_rows_are_extracted(self) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Results"
        sheet.append(["Test", "Result"])
        sheet.append(["Value", 120])
        content = io.BytesIO()
        workbook.save(content)

        text = extract_files([self._file("report.xlsx", ".xlsx", content.getvalue())])["files"][0]["text"]

        self.assertIn("[工作表：Results]", text)
        self.assertIn("| Value | 120 |", text)

    def test_text_limit_is_applied_across_files(self) -> None:
        files = [self._file(f"notes-{index}.txt", ".txt", b"x" * 4_000) for index in range(5)]

        result = extract_files(files)

        self.assertEqual(sum(len(item["text"]) for item in result["files"]), MAX_EXTRACTED_CHARS)
        self.assertTrue(result["truncated"])
        self.assertTrue(result["files"][-1]["truncated"])

    def test_multipart_file_is_extracted_and_unsupported_suffix_is_rejected(self) -> None:
        message = EmailMessage(policy=SMTP)
        message.set_content("ignored")
        message.add_attachment(b"hello", maintype="text", subtype="plain", filename="hello.txt")
        raw = message.as_bytes()
        header, body = raw.split(b"\r\n\r\n", 1)
        content_type = next(
            line.split(b": ", 1)[1].decode()
            for line in header.split(b"\r\n")
            if line.lower().startswith(b"content-type:")
        )

        parsed = parse_multipart(content_type, body)
        self.assertEqual(parsed[0]["name"], "hello.txt")
        self.assertEqual(parsed[0]["content"], b"hello")
        with self.assertRaises(ValueError):
            parse_multipart(content_type, body.replace(b"hello.txt", b"hello.exe"))

    @staticmethod
    def _file(name: str, extension: str, content: bytes) -> dict[str, object]:
        return {"name": name, "extension": extension, "content": content}


if __name__ == "__main__":
    unittest.main()

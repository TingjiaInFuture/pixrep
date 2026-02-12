from __future__ import annotations

import zlib
from pathlib import Path


def pdf_escape_literal(s: str) -> str:
    # PDF literal string escaping.
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class MinimalPDFWriter:
    """
    A tiny PDF writer for text-only pages using built-in PDF Type1 fonts.

    This keeps ONEPDF_CORE output small and dependency-free.
    """

    def __init__(self, title: str):
        self.title = title
        self._objects: list[bytes] = []

    def _add_obj(self, body: bytes) -> int:
        self._objects.append(body)
        return len(self._objects)  # 1-based object numbers

    def build(self, page_streams: list[bytes], out_path: Path) -> None:
        # Object 1: Shared resources (built-in fonts, no embedding)
        resources_obj = self._add_obj(
            b"<< /ProcSet [/PDF /Text]\n"
            b"/Font <<\n"
            b"  /F1 << /Type /Font /Subtype /Type1 /BaseFont /Courier >>\n"
            b"  /F2 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\n"
            b">>\n"
            b">>"
        )

        # Object 2: Pages placeholder
        pages_placeholder = self._add_obj(b"")

        # Object 3: Catalog
        catalog_obj = self._add_obj(
            f"<< /Type /Catalog /Pages {pages_placeholder} 0 R >>".encode("ascii")
        )

        page_obj_ids: list[int] = []

        # A4 in points.
        media_box = b"[0 0 595 842]"
        for stream in page_streams:
            compressed = zlib.compress(stream, level=9)
            content_obj = self._add_obj(
                b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(compressed)
                + compressed
                + b"\nendstream"
            )

            page_obj = self._add_obj(
                b"<< /Type /Page\n"
                + b"/Parent %d 0 R\n" % pages_placeholder
                + b"/MediaBox "
                + media_box
                + b"\n/Resources %d 0 R\n" % resources_obj
                + b"/Contents %d 0 R\n" % content_obj
                + b">>"
            )
            page_obj_ids.append(page_obj)

        pages_obj_body = (
            b"<< /Type /Pages\n"
            + b"/Count %d\n" % len(page_obj_ids)
            + b"/Kids ["
            + b" ".join(b"%d 0 R" % pid for pid in page_obj_ids)
            + b"]\n>>"
        )
        self._objects[pages_placeholder - 1] = pages_obj_body

        # Write file with xref.
        header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
        chunks: list[bytes] = [header]
        offsets: list[int] = [0]  # obj 0
        offset = len(header)

        for i, body in enumerate(self._objects, start=1):
            offsets.append(offset)
            obj = b"%d 0 obj\n" % i + body + b"\nendobj\n"
            chunks.append(obj)
            offset += len(obj)

        xref_offset = offset
        xref_lines = [b"xref\n", b"0 %d\n" % (len(self._objects) + 1)]
        xref_lines.append(b"0000000000 65535 f \n")
        for off in offsets[1:]:
            xref_lines.append(f"{off:010d} 00000 n \n".encode("ascii"))
        chunks.append(b"".join(xref_lines))

        trailer = (
            b"trailer\n"
            b"<<\n"
            b"/Size %d\n" % (len(self._objects) + 1)
            + b"/Root %d 0 R\n" % catalog_obj
            + b">>\n"
            b"startxref\n"
            + str(xref_offset).encode("ascii")
            + b"\n%%EOF\n"
        )
        chunks.append(trailer)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"".join(chunks))


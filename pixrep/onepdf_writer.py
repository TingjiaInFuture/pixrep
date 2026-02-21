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

    def build(self, page_streams: list[bytes], out_path: Path) -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        page_count = len(page_streams)
        object_count = 3 + page_count * 2
        page_obj_ids = [5 + i * 2 for i in range(page_count)]
        compressed_streams = [zlib.compress(stream, level=6) for stream in page_streams]

        def build_obj(obj_num: int, body: bytes) -> bytes:
            return b"%d 0 obj\n" % obj_num + body + b"\nendobj\n"

        header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
        offsets: list[int] = [0]
        offset = len(header)

        resources_body = (
            b"<< /ProcSet [/PDF /Text]\n"
            b"/Font <<\n"
            b"  /F1 << /Type /Font /Subtype /Type1 /BaseFont /Courier >>\n"
            b"  /F2 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\n"
            b">>\n"
            b">>"
        )

        pages_body = (
            b"<< /Type /Pages\n"
            + b"/Count %d\n" % page_count
            + b"/Kids ["
            + b" ".join(b"%d 0 R" % pid for pid in page_obj_ids)
            + b"]\n>>"
        )
        catalog_body = b"<< /Type /Catalog /Pages 2 0 R >>"

        with out_path.open("wb") as f:
            f.write(header)

            for obj_num, body in ((1, resources_body), (2, pages_body), (3, catalog_body)):
                offsets.append(offset)
                obj = build_obj(obj_num, body)
                f.write(obj)
                offset += len(obj)

            media_box = b"[0 0 595 842]"
            for page_index, compressed in enumerate(compressed_streams):
                content_obj_num = 4 + page_index * 2
                page_obj_num = 5 + page_index * 2

                content_body = (
                    b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(compressed)
                    + compressed
                    + b"\nendstream"
                )
                offsets.append(offset)
                content_obj = build_obj(content_obj_num, content_body)
                f.write(content_obj)
                offset += len(content_obj)

                page_body = (
                    b"<< /Type /Page\n"
                    b"/Parent 2 0 R\n"
                    + b"/MediaBox "
                    + media_box
                    + b"\n/Resources 1 0 R\n"
                    + b"/Contents %d 0 R\n" % content_obj_num
                    + b">>"
                )
                offsets.append(offset)
                page_obj = build_obj(page_obj_num, page_body)
                f.write(page_obj)
                offset += len(page_obj)

            xref_offset = offset
            f.write(b"xref\n")
            f.write(b"0 %d\n" % (object_count + 1))
            f.write(b"0000000000 65535 f \n")
            for off in offsets[1:]:
                f.write(f"{off:010d} 00000 n \n".encode("ascii"))

            trailer = (
                b"trailer\n"
                b"<<\n"
                b"/Size %d\n" % (object_count + 1)
                + b"/Root 3 0 R\n"
                + b">>\n"
                b"startxref\n"
                + str(xref_offset).encode("ascii")
                + b"\n%%EOF\n"
            )
            f.write(trailer)


class StreamingPDFWriter:
    """
    Stream-oriented PDF writer for ONEPDF_CORE.

    Writes each page immediately without retaining all page streams in memory,
    then writes Pages/Catalog/XRef at finalize time.
    """

    def __init__(self, title: str, out_path: Path):
        self.title = title
        self.out_path = out_path
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self._f = out_path.open("wb")
        self._obj_offsets: dict[int, int] = {}
        self._offset = 0
        self._page_obj_ids: list[int] = []
        self._next_obj_num = 4
        self.page_count = 0

        self._write_raw(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        self._write_obj(
            1,
            (
                b"<< /ProcSet [/PDF /Text]\n"
                b"/Font <<\n"
                b"  /F1 << /Type /Font /Subtype /Type1 /BaseFont /Courier >>\n"
                b"  /F2 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\n"
                b">>\n"
                b">>"
            ),
        )

    def _write_raw(self, data: bytes) -> None:
        self._f.write(data)
        self._offset += len(data)

    def _write_obj(self, obj_num: int, body: bytes) -> None:
        self._obj_offsets[obj_num] = self._offset
        obj = b"%d 0 obj\n" % obj_num + body + b"\nendobj\n"
        self._write_raw(obj)

    def add_page(self, page_stream: bytes) -> None:
        compressed = zlib.compress(page_stream, level=6)

        content_obj_num = self._next_obj_num
        page_obj_num = self._next_obj_num + 1
        self._next_obj_num += 2

        content_body = (
            b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(compressed)
            + compressed
            + b"\nendstream"
        )
        self._write_obj(content_obj_num, content_body)

        page_body = (
            b"<< /Type /Page\n"
            b"/Parent 2 0 R\n"
            b"/MediaBox [0 0 595 842]\n"
            b"/Resources 1 0 R\n"
            + b"/Contents %d 0 R\n" % content_obj_num
            + b">>"
        )
        self._write_obj(page_obj_num, page_body)

        self._page_obj_ids.append(page_obj_num)
        self.page_count += 1

    def finalize(self) -> None:
        pages_body = (
            b"<< /Type /Pages\n"
            + b"/Count %d\n" % self.page_count
            + b"/Kids ["
            + b" ".join(b"%d 0 R" % pid for pid in self._page_obj_ids)
            + b"]\n>>"
        )
        self._write_obj(2, pages_body)
        self._write_obj(3, b"<< /Type /Catalog /Pages 2 0 R >>")

        object_count = self._next_obj_num - 1
        xref_offset = self._offset

        self._write_raw(b"xref\n")
        self._write_raw(b"0 %d\n" % (object_count + 1))
        self._write_raw(b"0000000000 65535 f \n")
        for obj_num in range(1, object_count + 1):
            off = self._obj_offsets.get(obj_num, 0)
            self._write_raw(f"{off:010d} 00000 n \n".encode("ascii"))

        trailer = (
            b"trailer\n"
            b"<<\n"
            + b"/Size %d\n" % (object_count + 1)
            + b"/Root 3 0 R\n"
            + b">>\n"
            b"startxref\n"
            + str(xref_offset).encode("ascii")
            + b"\n%%EOF\n"
        )
        self._write_raw(trailer)
        self._f.close()


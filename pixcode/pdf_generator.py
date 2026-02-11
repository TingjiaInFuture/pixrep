from datetime import datetime
from pathlib import Path
import re

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)

from .flowables import CodeBlockChunk, HeaderBar, StatBox
from .fonts import FontRegistry, register_fonts
from .models import FileInfo, RepoInfo
from .theme import COLORS
from .utils import xml_escape


class PDFGenerator:
    def __init__(self, repo: RepoInfo, output_dir: str,
                 fonts: FontRegistry | None = None):
        self.repo = repo
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.fonts = fonts or register_fonts()
        self.page_width, self.page_height = A4
        self.margin = 15 * mm
        self.content_width = self.page_width - 2 * self.margin
        self.avail_height = self.page_height - self.margin - 15 * mm

    def generate_all(self):
        print(f"\n📦 Project: {self.repo.name}")
        print(f"   Files: {len(self.repo.files)}, Lines: {self.repo.total_lines:,}")
        print(f"   Output: {self.output_dir}\n")
        self._generate_index_pdf()
        for info in self.repo.files:
            self._generate_file_pdf(info)
        print(f"\n✅ Done! Generated {len(self.repo.files) + 1} PDFs")

    def _page_footer(self, canvas, doc):
        canvas.saveState()
        canvas.setFont(self.fonts.normal, 7)
        canvas.setFillColor(HexColor("#999999"))
        canvas.drawString(self.margin, 10 * mm,
                          f"pixcode · {self.repo.name}")
        canvas.drawRightString(self.page_width - self.margin, 10 * mm,
                               f"Page {doc.page}")
        canvas.restoreState()

    def _make_doc(self, filename: str):
        return SimpleDocTemplate(
            str(filename), pagesize=A4,
            leftMargin=self.margin, rightMargin=self.margin,
            topMargin=self.margin, bottomMargin=15 * mm,
        )

    def _cjk_style(self, name, parent_name="Normal", **kwargs):
        styles = getSampleStyleSheet()
        parent = styles[parent_name]
        defaults = {"fontName": self.fonts.normal, "fontSize": parent.fontSize}
        defaults.update(kwargs)
        return ParagraphStyle(name, parent=parent, **defaults)

    def _max_lines_for_height(self, avail_h, font_size=6.5):
        line_h = font_size * 1.6
        padding = 12
        return max(1, int((avail_h - padding) / line_h))

    def _generate_index_pdf(self):
        filename = self.output_dir / "00_INDEX.pdf"
        doc = self._make_doc(filename)
        story = []
        cw = self.content_width

        story.append(Spacer(1, 10 * mm))
        title_style = self._cjk_style(
            "CTitle", "Title", fontSize=28,
            textColor=COLORS["accent"], fontName=self.fonts.bold,
            spaceAfter=4 * mm,
        )
        story.append(Paragraph(xml_escape(self.repo.name), title_style))

        sub_style = self._cjk_style(
            "CSub", fontSize=10,
            textColor=HexColor("#888888"), spaceAfter=8 * mm,
        )
        story.append(Paragraph(
            "Code Repository Overview · Generated "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            sub_style))

        bw = (cw - 20) / 4
        stat_data = [[
            StatBox("FILES", str(len(self.repo.files)),
                    COLORS["accent"], fonts=self.fonts, width=bw, height=50),
            StatBox("LINES", f"{self.repo.total_lines:,}",
                    COLORS["accent2"], fonts=self.fonts, width=bw, height=50),
            StatBox("SIZE", self._fmt_size(self.repo.total_size),
                    COLORS["green"], fonts=self.fonts, width=bw, height=50),
            StatBox("LANGUAGES", str(len(self.repo.language_stats)),
                    COLORS["type"], fonts=self.fonts, width=bw, height=50),
        ]]
        table = Table(stat_data, colWidths=[bw + 5] * 4)
        table.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(table)
        story.append(Spacer(1, 8 * mm))

        story.append(HeaderBar("Language Statistics", fonts=self.fonts, width=cw))
        story.append(Spacer(1, 3 * mm))

        ns = self._cjk_style("CN", fontSize=8)
        lang_data = [[
            Paragraph("<b>Language</b>", ns),
            Paragraph("<b>Files</b>", ns),
            Paragraph("<b>Lines</b>", ns),
            Paragraph("<b>%</b>", ns),
        ]]
        for lang, stats in self.repo.language_stats.items():
            pct = (stats["lines"] / max(self.repo.total_lines, 1)) * 100
            lang_data.append([
                Paragraph(
                    f'<font color="{COLORS["accent"].hexval()}">'
                    f"{xml_escape(lang)}</font>", ns),
                Paragraph(str(stats["files"]), ns),
                Paragraph(f"{stats['lines']:,}", ns),
                Paragraph(f"{pct:.1f}%", ns),
            ])
        lang_table = Table(lang_data,
                           colWidths=[cw * 0.35, cw * 0.2, cw * 0.25, cw * 0.2])
        lang_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), COLORS["header_bg"]),
            ("TEXTCOLOR", (0, 0), (-1, 0), white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("GRID", (0, 0), (-1, -1), 0.5, COLORS["border"]),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [white, HexColor("#f8f9fa")]),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ]))
        story.append(lang_table)
        story.append(Spacer(1, 8 * mm))

        story.append(HeaderBar("Directory Structure", fonts=self.fonts, width=cw))
        story.append(Spacer(1, 3 * mm))

        tree_lines = self.repo.tree_str.split("\n")
        if len(tree_lines) > 120:
            tree_lines = tree_lines[:120] + [
                f"  ... ({len(tree_lines)} entries total)"]

        self._add_code_chunks(story, tree_lines, "text", cw,
                              first_avail=self.avail_height - 300,
                              later_avail=self.avail_height - 10)
        story.append(Spacer(1, 6 * mm))

        story.append(PageBreak())
        story.append(HeaderBar("File Index",
                               f"{len(self.repo.files)} files",
                               fonts=self.fonts, width=cw))
        story.append(Spacer(1, 3 * mm))

        fs = self._cjk_style("FE", fontSize=7, fontName=self.fonts.normal)
        header = [
            Paragraph("<b>#</b>", fs),
            Paragraph("<b>File Path</b>", fs),
            Paragraph("<b>Lang</b>", fs),
            Paragraph("<b>Lines</b>", fs),
            Paragraph("<b>Size</b>", fs),
            Paragraph("<b>PDF</b>", fs),
        ]
        data = [header]
        for info in self.repo.files:
            pdf_name = self._file_pdf_name(info)
            data.append([
                Paragraph(str(info.index), fs),
                Paragraph(
                    f'<font color="{COLORS["accent"].hexval()}">'
                    f"{xml_escape(str(info.path))}</font>", fs),
                Paragraph(info.language, fs),
                Paragraph(f"{info.line_count:,}", fs),
                Paragraph(self._fmt_size(info.size), fs),
                Paragraph(
                    f'<font color="{COLORS["accent2"].hexval()}">'
                    f"{xml_escape(pdf_name)}</font>", fs),
            ])
        cols = [cw * 0.06, cw * 0.38, cw * 0.12,
                cw * 0.12, cw * 0.12, cw * 0.20]
        file_table = Table(data, colWidths=cols, repeatRows=1)
        file_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), COLORS["header_bg"]),
            ("TEXTCOLOR", (0, 0), (-1, 0), white),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("GRID", (0, 0), (-1, -1), 0.3, COLORS["border"]),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [white, HexColor("#f8f9fa")]),
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ("ALIGN", (3, 0), (4, -1), "RIGHT"),
        ]))
        story.append(file_table)

        doc.build(story,
                  onFirstPage=self._page_footer,
                  onLaterPages=self._page_footer)
        print(f"  📄 00_INDEX.pdf ({len(self.repo.files)} files indexed)")

    def _generate_file_pdf(self, file_info: FileInfo):
        pdf_name = self._file_pdf_name(file_info)
        filename = self.output_dir / pdf_name
        doc = self._make_doc(filename)
        story = []
        cw = self.content_width

        story.append(HeaderBar(
            str(file_info.path),
            f"{file_info.language} · {file_info.line_count:,} lines · "
            f"{self._fmt_size(file_info.size)}",
            fonts=self.fonts,
            width=cw,
        ))
        story.append(Spacer(1, 4 * mm))

        meta = self._cjk_style("Meta", fontSize=8,
                               textColor=HexColor("#666666"))
        for item in [
            f"<b>Path:</b> {xml_escape(str(file_info.path))}",
            f"<b>Language:</b> {file_info.language}",
            f"<b>Lines:</b> {file_info.line_count:,}",
            f"<b>Size:</b> {self._fmt_size(file_info.size)}",
        ]:
            story.append(Paragraph(item, meta))
        story.append(Spacer(1, 4 * mm))

        all_lines = file_info.content.split("\n")
        first_page_used = 28 + 4 * mm + 4 * 14 + 4 * mm + 10
        first_avail = self.avail_height - first_page_used
        later_avail = self.avail_height - 10

        self._add_code_chunks(story, all_lines, file_info.language, cw,
                              first_avail=first_avail,
                              later_avail=later_avail)

        doc.build(story,
                  onFirstPage=self._page_footer,
                  onLaterPages=self._page_footer)
        print(f"  📄 {pdf_name} ({file_info.line_count} lines)")

    def _add_code_chunks(self, story, all_lines, language, width,
                         first_avail, later_avail, font_size=6.5):
        offset = 0
        first_chunk = True
        while offset < len(all_lines):
            avail = first_avail if first_chunk else later_avail
            n = self._max_lines_for_height(avail, font_size)
            chunk = all_lines[offset:offset + n]

            story.append(CodeBlockChunk(
                chunk, language,
                fonts=self.fonts,
                start_line=offset + 1,
                width=width, font_size=font_size,
            ))

            offset += n
            first_chunk = False
            if offset < len(all_lines):
                story.append(Spacer(1, 1))

    def _file_pdf_name(self, info: FileInfo) -> str:
        safe_path = str(info.path).replace("/", "_").replace("\\", "_")
        safe_path = re.sub(r"[^\w\-_.]", "_", safe_path)
        return f"{info.index:03d}_{safe_path}.pdf"

    @staticmethod
    def _fmt_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / 1024 / 1024:.1f} MB"

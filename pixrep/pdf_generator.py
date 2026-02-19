import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
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

from .analysis import CodeInsightEngine
from .flowables import CodeBlockChunk, HeaderBar, LintLegend, SemanticMiniMap, StatBox
from .fonts import FontRegistry, register_fonts
from .models import FileInfo, RepoInfo
from .theme import COLORS
from .utils import xml_escape


log = logging.getLogger(__name__)


class PDFGenerator:
    def __init__(self, repo: RepoInfo, output_dir: str,
                 fonts: FontRegistry | None = None,
                 enable_semantic_minimap: bool = True,
                 enable_lint_heatmap: bool = True,
                 linter_timeout: int = 20,
                 incremental: bool = False,
                 max_workers: int | None = None):
        self.repo = repo
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.fonts = fonts or register_fonts()
        self.page_width, self.page_height = A4
        self.margin = 15 * mm
        self.content_width = self.page_width - 2 * self.margin
        self.avail_height = self.page_height - self.margin - 15 * mm
        self.enable_semantic_minimap = enable_semantic_minimap
        self.enable_lint_heatmap = enable_lint_heatmap
        self.incremental = incremental
        # None → use CPU count, capped at 8 to avoid over-subscription.
        self.max_workers = max_workers if max_workers is not None else min(8, os.cpu_count() or 1)
        self.insight_engine = CodeInsightEngine(
            repo,
            enable_semantic_minimap=enable_semantic_minimap,
            enable_lint_heatmap=enable_lint_heatmap,
            linter_timeout=linter_timeout,
        )

    def generate_all(self):
        """Generate index PDF and one PDF per file into output_dir.

        Files are processed in parallel (ThreadPoolExecutor) so that I/O and
        ReportLab rendering overlap.  When `incremental=True` any file whose
        source mtime is older than its existing PDF is skipped.
        """
        log.info("")
        log.info("Project: %s", self.repo.name)
        log.info("Files: %d, Lines: %d", len(self.repo.files), self.repo.total_lines)
        log.info("Output: %s", self.output_dir)
        if self.incremental:
            log.info("Mode: incremental (skipping up-to-date files)")
        log.info("")
        self.insight_engine.enrich_repo()
        self._generate_index_pdf()

        pending = [
            info for info in self.repo.files
            if self._needs_regeneration(info)
        ]
        skipped = len(self.repo.files) - len(pending)
        if skipped:
            log.info("  Skipping %d up-to-date file PDFs", skipped)

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures = {pool.submit(self._generate_file_pdf, info): info for info in pending}
            total = len(futures)
            for index, fut in enumerate(as_completed(futures), start=1):
                exc = fut.exception()
                if exc:
                    info = futures[fut]
                    log.warning("  Failed to generate PDF for %s: %s", info.path, exc)
                if index % 10 == 0 or index == total:
                    log.info("  Progress: %d/%d files", index, total)

        log.info("")
        log.info("Done! Generated %d PDFs (+ index)", len(pending))

    def generate_index_only(self) -> None:
        """Generate only `00_INDEX.pdf` into output_dir."""
        self.insight_engine.enrich_repo()
        self._generate_index_pdf()

    def _needs_regeneration(self, info: "FileInfo") -> bool:
        """Return True if the file PDF must be (re-)generated."""
        if not self.incremental:
            return True
        pdf_path = self.output_dir / self._file_pdf_name(info)
        if not pdf_path.exists():
            return True
        try:
            return info.abs_path.stat().st_mtime > pdf_path.stat().st_mtime
        except OSError:
            return True

    def _page_footer(self, canvas, doc):
        canvas.saveState()
        canvas.setFont(self.fonts.normal, 7)
        canvas.setFillColor(HexColor("#999999"))
        canvas.drawString(self.margin, 10 * mm,
                          f"pixrep · {self.repo.name}")
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

        # Use ASCII tree connectors to avoid missing glyphs in fallback fonts.
        tree_str = (
            self.repo.tree_str
            .replace("├── ", "|-- ")
            .replace("└── ", "`-- ")
            .replace("│   ", "|   ")
            .replace("─", "-")
            .replace("│", "|")
        )
        tree_lines = tree_str.split("\n")
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
        log.info("  00_INDEX.pdf (%d files indexed)", len(self.repo.files))

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

        if self.enable_semantic_minimap:
            if file_info.semantic_map.kind != "none":
                stats = (
                    f"<b>Semantic Map:</b> {xml_escape(file_info.semantic_map.kind)} "
                    f"({file_info.semantic_map.node_count} nodes / "
                    f"{file_info.semantic_map.edge_count} edges)"
                )
                story.append(Paragraph(stats, meta))

        lint_counts = self._lint_counts(file_info)
        if self.enable_lint_heatmap:
            story.append(Paragraph(
                f"<b>Linter:</b> {lint_counts['high']} high / "
                f"{lint_counts['medium']} medium findings",
                meta,
            ))

        story.append(Spacer(1, 4 * mm))

        legend_budget = 0
        minimap_budget = 0
        minimap_spacer_budget = 0
        if self.enable_semantic_minimap and file_info.semantic_map.kind != "none":
            minimap = SemanticMiniMap(file_info.semantic_map, fonts=self.fonts, width=cw)
            _, minimap_h = minimap.wrap(cw, self.avail_height)
            story.append(minimap)
            story.append(Spacer(1, 4 * mm))
            minimap_budget = minimap_h
            minimap_spacer_budget = 4 * mm

        if self.enable_lint_heatmap and (lint_counts["high"] + lint_counts["medium"]) > 0:
            legend = LintLegend(fonts=self.fonts, width=cw)
            _, legend_h = legend.wrap(cw, self.avail_height)
            story.append(Spacer(1, 2 * mm))
            story.append(legend)
            story.append(Spacer(1, 2 * mm))
            legend_budget = legend_h + 4 * mm

        all_lines = file_info.load_content().split("\n")
        base_meta_lines = 4
        semantic_meta_lines = 1 if (self.enable_semantic_minimap and file_info.semantic_map.kind != "none") else 0
        lint_meta_lines = 1 if self.enable_lint_heatmap else 0

        header_budget = 28
        spacing_budget = 4 * mm + 4 * mm
        meta_lines_budget = (base_meta_lines + semantic_meta_lines + lint_meta_lines) * 14
        first_page_used = (
            header_budget
            + spacing_budget
            + meta_lines_budget
            + legend_budget
            + minimap_budget
            + minimap_spacer_budget
        )
        first_avail = self.avail_height - first_page_used
        later_avail = self.avail_height - 10

        line_heat = self._line_heat_map(file_info) if self.enable_lint_heatmap else {}
        self._add_code_chunks(story, all_lines, file_info.language, cw,
                              first_avail=first_avail,
                              later_avail=later_avail,
                              line_heat=line_heat)

        doc.build(story,
                  onFirstPage=self._page_footer,
                  onLaterPages=self._page_footer)
        file_info.release_content()
        log.info("  %s (%d lines)", pdf_name, file_info.line_count)

    def _add_code_chunks(self, story, all_lines, language, width,
                         first_avail, later_avail, font_size=6.5,
                         line_heat: dict[int, str] | None = None):
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
                line_heat=line_heat,
            ))

            offset += n
            first_chunk = False
            if offset < len(all_lines):
                if line_heat and (line_heat.get(offset) or line_heat.get(offset + 1)):
                    story.append(Spacer(1, 0))
                else:
                    story.append(Spacer(1, 1))

    def _file_pdf_name(self, info: FileInfo) -> str:
        safe_path = str(info.path).replace("/", "_").replace("\\", "_")
        safe_path = re.sub(r"[^\w\-_.]", "_", safe_path)
        return f"{info.index:03d}_{safe_path}.pdf"

    @staticmethod
    def _line_heat_map(info: FileInfo) -> dict[int, str]:
        line_map: dict[int, str] = {}
        for issue in info.lint_issues:
            if issue.line < 1:
                continue
            current = line_map.get(issue.line)
            if current == "high":
                continue
            if issue.severity == "high":
                line_map[issue.line] = "high"
            elif current is None:
                line_map[issue.line] = "medium"
        return line_map

    @staticmethod
    def _lint_counts(info: FileInfo) -> dict[str, int]:
        high = sum(1 for issue in info.lint_issues if issue.severity == "high")
        medium = sum(1 for issue in info.lint_issues if issue.severity != "high")
        return {"high": high, "medium": medium}

    @staticmethod
    def _fmt_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / 1024 / 1024:.1f} MB"

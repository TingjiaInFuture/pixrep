import re

from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus.flowables import Flowable

from .fonts import FontRegistry
from .models import SemanticMap
from .syntax import BUILTIN_FUNCTIONS, COMMENT_STYLES, KEYWORDS
from .theme import COLORS
from .utils import str_width, truncate_to_width


class CodeBlockChunk(Flowable):
    """
    渲染一段代码行，保证高度不超页面。
    所有文字用注册的 mono 字体绘制。
    """

    def __init__(self, lines: list[str], language: str,
                 fonts: FontRegistry,
                 start_line: int = 1, width: float | None = None,
                 font_size: float = 6.5,
                 line_heat: dict[int, str] | None = None):
        super().__init__()
        self.code_lines = lines
        self.language = language
        self.start_line = start_line
        self.block_width = width or (A4[0] - 30 * mm)
        self.font_size = font_size
        self.line_height = font_size * 1.6
        self.padding = 6
        self.fonts = fonts
        self.line_heat = line_heat or {}

        self.kw_set = KEYWORDS.get(language, set())
        self.builtin_set = BUILTIN_FUNCTIONS.get(language, set())
        self.line_comment = COMMENT_STYLES.get(language)
        self._ml_string_mask = self._compute_multiline_string_mask()

    def _compute_multiline_string_mask(self) -> list[bool]:
        """
        Best-effort multiline string/comment masking.

        This is intentionally heuristic: we avoid expensive parsing but prevent
        obvious false highlighting in long blocks.
        """
        mask: list[bool] = []
        in_ml = False
        if self.language == "python":
            for line in self.code_lines:
                mask.append(in_ml)
                # Toggle on odd number of triple quotes.
                toggles = (line.count("'''") + line.count('"""')) % 2
                if toggles:
                    # The line with the delimiter is part of the string region.
                    mask[-1] = True
                    in_ml = not in_ml
            return mask

        if self.language in {"javascript", "typescript"}:
            for line in self.code_lines:
                mask.append(in_ml)
                # Toggle on unescaped backticks.
                n = 0
                esc = False
                for ch in line:
                    if esc:
                        esc = False
                        continue
                    if ch == "\\":
                        esc = True
                        continue
                    if ch == "`":
                        n += 1
                if n % 2:
                    mask[-1] = True
                    in_ml = not in_ml
            return mask

        return [False] * len(self.code_lines)

    def wrap(self, availWidth, availHeight):
        self.block_width = min(self.block_width, availWidth)
        h = len(self.code_lines) * self.line_height + self.padding * 2
        return (self.block_width, h)

    def draw(self):
        canv = self.canv
        num_lines = len(self.code_lines)
        total_h = num_lines * self.line_height + self.padding * 2

        canv.setFillColor(COLORS["bg"])
        canv.roundRect(0, 0, self.block_width, total_h, 4, fill=1, stroke=0)

        max_no = self.start_line + num_lines
        # Heuristic sizing: line numbers are rendered using monospace; keep a minimum gutter.
        min_gutter = 35
        digit_width = 7
        padding = 14
        line_no_width = max(min_gutter, len(str(max_no)) * digit_width + padding)

        canv.setFillColor(COLORS["header_bg"])
        canv.roundRect(0, 0, line_no_width, total_h, 4, fill=1, stroke=0)
        canv.rect(line_no_width - 4, 0, 4, total_h, fill=1, stroke=0)

        canv.setStrokeColor(COLORS["border"])
        canv.setLineWidth(0.5)
        canv.line(line_no_width, 0, line_no_width, total_h)

        y = total_h - self.padding - self.line_height + 3
        code_x = line_no_width + 8
        code_area_width = self.block_width - code_x - 6

        for i, line in enumerate(self.code_lines):
            line_no = self.start_line + i
            level = self.line_heat.get(line_no)
            if level:
                canv.setFillColor(COLORS["heat_high"] if level == "high" else COLORS["heat_medium"])
                canv.rect(line_no_width, y - 2, self.block_width - line_no_width, self.line_height, fill=1, stroke=0)

            canv.setFont("Courier", self.font_size)
            canv.setFillColor(COLORS["line_no"])
            canv.drawRightString(line_no_width - 6, y, str(line_no))

            display = truncate_to_width(line, self.font_size, code_area_width)
            self._draw_line(canv, code_x, y, display, in_multiline_string=self._ml_string_mask[i])
            y -= self.line_height

    def _draw_line(self, canv, x, y, line, in_multiline_string: bool = False):
        fs = self.font_size
        stripped = line.lstrip()

        if in_multiline_string:
            canv.setFont(self.fonts.mono, fs)
            canv.setFillColor(COLORS["string"])
            canv.drawString(x, y, line)
            return

        if self.line_comment and stripped.startswith(self.line_comment):
            canv.setFont(self.fonts.mono, fs)
            canv.setFillColor(COLORS["comment"])
            canv.drawString(x, y, line)
            return

        if self.language == "python" and stripped.startswith("@"):
            canv.setFont(self.fonts.mono, fs)
            canv.setFillColor(COLORS["function"])
            canv.drawString(x, y, line)
            return

        if stripped and stripped[0] in ('"', "'", "`"):
            canv.setFont(self.fonts.mono, fs)
            canv.setFillColor(COLORS["string"])
            canv.drawString(x, y, line)
            return

        segments = self._split_line_segments(line)
        cur_x = x

        for seg_text, seg_kind in segments:
            if not seg_text:
                continue
            if seg_text.isspace():
                cur_x += str_width(seg_text, fs)
                continue

            if seg_kind == "comment":
                canv.setFont(self.fonts.mono, fs)
                canv.setFillColor(COLORS["comment"])
                canv.drawString(cur_x, y, seg_text)
                cur_x += str_width(seg_text, fs)
                continue

            if seg_kind == "string":
                canv.setFont(self.fonts.mono, fs)
                canv.setFillColor(COLORS["string"])
                canv.drawString(cur_x, y, seg_text)
                cur_x += str_width(seg_text, fs)
                continue

            # code segment: keep whitespace as-is, but colorize tokens.
            tokens = re.split(r"(\s+)", seg_text)
            for token in tokens:
                if not token:
                    continue
                if token.isspace():
                    cur_x += str_width(token, fs)
                    continue

                color = COLORS["fg"]
                bold = False

                word = re.sub(r"^[^\w]*|[^\w]*$", "", token)

                if word in self.kw_set:
                    color = COLORS["keyword"]
                    bold = True
                elif word in self.builtin_set:
                    color = COLORS["type"]
                elif self.language == "python" and word in ("self", "cls"):
                    color = COLORS["red"]
                elif re.match(r"^\d+\.?\d*$", word):
                    color = COLORS["number"]

                canv.setFillColor(color)
                font = self.fonts.mono_bold if bold else self.fonts.mono
                canv.setFont(font, fs)
                canv.drawString(cur_x, y, token)
                cur_x += str_width(token, fs)

    def _split_line_segments(self, line: str) -> list[tuple[str, str]]:
        """
        Split a line into (text, kind) segments where kind is one of:
        - "code"
        - "string"
        - "comment"
        """
        comment = self.line_comment
        out: list[tuple[str, str]] = []
        buf: list[str] = []
        kind = "code"
        quote: str | None = None
        esc = False

        def flush():
            nonlocal buf
            if buf:
                out.append(("".join(buf), kind))
                buf = []

        i = 0
        while i < len(line):
            ch = line[i]

            if kind == "code" and comment and quote is None:
                # Start of line comment (outside any string).
                if line.startswith(comment, i):
                    flush()
                    out.append((line[i:], "comment"))
                    return out

            if kind == "code":
                if ch in ('"', "'", "`"):
                    flush()
                    kind = "string"
                    quote = ch
                    buf.append(ch)
                    esc = False
                    i += 1
                    continue
                buf.append(ch)
                i += 1
                continue

            # string
            if esc:
                buf.append(ch)
                esc = False
                i += 1
                continue
            if ch == "\\":
                buf.append(ch)
                esc = True
                i += 1
                continue
            buf.append(ch)
            i += 1
            if quote and ch == quote:
                flush()
                kind = "code"
                quote = None

        flush()
        return out


class SemanticMiniMap(Flowable):
    def __init__(self, semantic_map: SemanticMap, fonts: FontRegistry, width: float | None = None):
        super().__init__()
        self.semantic_map = semantic_map
        self.fonts = fonts
        self.map_width = width or (A4[0] - 30 * mm)
        self.padding = 6
        self.line_height = 9
        self.title_height = 14

    def wrap(self, availWidth, availHeight):
        self.map_width = min(self.map_width, availWidth)
        rows = max(1, len(self.semantic_map.lines))
        total_h = self.title_height + rows * self.line_height + self.padding * 2
        return (self.map_width, total_h)

    def draw(self):
        canv = self.canv
        rows = max(1, len(self.semantic_map.lines))
        total_h = self.title_height + rows * self.line_height + self.padding * 2

        canv.setFillColor(COLORS["bg_light"])
        canv.roundRect(0, 0, self.map_width, total_h, 4, fill=1, stroke=0)

        canv.setFillColor(COLORS["header_bg"])
        canv.roundRect(0, total_h - self.title_height - self.padding, self.map_width, self.title_height + self.padding, 4, fill=1, stroke=0)

        canv.setFont(self.fonts.bold, 8)
        canv.setFillColor(COLORS["white"])
        title = "Semantic Minimap"
        kind = self.semantic_map.kind.upper()
        canv.drawString(8, total_h - self.title_height, f"{title}  [{kind}]")

        y = total_h - self.title_height - self.padding - self.line_height + 2
        canv.setFont(self.fonts.mono, 7)
        canv.setFillColor(COLORS["fg"])
        for line in self.semantic_map.lines[:16]:
            canv.drawString(8, y, truncate_to_width(line, 7, self.map_width - 16))
            y -= self.line_height


class HeaderBar(Flowable):
    def __init__(self, text: str, subtext: str = "",
                 fonts: FontRegistry | None = None,
                 width: float | None = None):
        super().__init__()
        self.text = text
        self.subtext = subtext
        self.bar_width = width or (A4[0] - 30 * mm)
        self.bar_height = 28 if subtext else 22
        self.fonts = fonts

    def wrap(self, availWidth, availHeight):
        self.bar_width = min(self.bar_width, availWidth)
        return (self.bar_width, self.bar_height)

    def draw(self):
        canv = self.canv
        canv.setFillColor(COLORS["accent"])
        canv.roundRect(0, 0, self.bar_width, self.bar_height, 4, fill=1, stroke=0)
        canv.setFont(self.fonts.bold if self.fonts else "Helvetica-Bold", 10)
        canv.setFillColor(COLORS["white"])
        canv.drawString(10, self.bar_height - 15, self.text)
        if self.subtext:
            canv.setFont(self.fonts.normal if self.fonts else "Helvetica", 7)
            canv.setFillColor(HexColor("#d0e8ff"))
            canv.drawString(10, 5, self.subtext)


class StatBox(Flowable):
    def __init__(self, label: str, value: str, color: Color,
                 fonts: FontRegistry | None = None,
                 width: float = 80, height: float = 50):
        super().__init__()
        self.label = label
        self.value = value
        self.color = color
        self.box_width = width
        self.box_height = height
        self.fonts = fonts

    def wrap(self, availWidth, availHeight):
        return (self.box_width, self.box_height)

    def draw(self):
        canv = self.canv
        canv.setFillColor(self.color)
        canv.roundRect(0, 0, self.box_width, self.box_height, 6, fill=1, stroke=0)
        canv.setFillColor(white)
        canv.setFont(self.fonts.bold if self.fonts else "Helvetica-Bold", 16)
        canv.drawCentredString(self.box_width / 2, self.box_height - 25,
                               str(self.value))
        canv.setFont(self.fonts.normal if self.fonts else "Helvetica", 8)
        canv.setFillColor(HexColor("#ffffffcc"))
        canv.drawCentredString(self.box_width / 2, 8, self.label)

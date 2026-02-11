import re

from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus.flowables import Flowable

from .fonts import FontRegistry
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
                 font_size: float = 6.5):
        super().__init__()
        self.code_lines = lines
        self.language = language
        self.start_line = start_line
        self.block_width = width or (A4[0] - 30 * mm)
        self.font_size = font_size
        self.line_height = font_size * 1.6
        self.padding = 6
        self.fonts = fonts

        self.kw_set = KEYWORDS.get(language, set())
        self.builtin_set = BUILTIN_FUNCTIONS.get(language, set())
        self.line_comment = COMMENT_STYLES.get(language)

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
        line_no_width = max(35, len(str(max_no)) * 7 + 14)

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
            canv.setFont("Courier", self.font_size)
            canv.setFillColor(COLORS["line_no"])
            canv.drawRightString(line_no_width - 6, y, str(line_no))

            display = truncate_to_width(line, self.font_size, code_area_width)
            self._draw_line(canv, code_x, y, display)
            y -= self.line_height

    def _draw_line(self, canv, x, y, line):
        fs = self.font_size
        stripped = line.lstrip()

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

        tokens = re.split(r"(\s+)", line)
        cur_x = x

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
            elif any(token.startswith(c) for c in
                     ('"', "'", "`", 'f"', "f'", 'r"', "r'", 'b"', "b'")):
                color = COLORS["string"]

            canv.setFillColor(color)
            font = self.fonts.mono_bold if bold else self.fonts.mono
            canv.setFont(font, fs)
            canv.drawString(cur_x, y, token)
            cur_x += str_width(token, fs)


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

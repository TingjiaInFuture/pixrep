from functools import lru_cache


def xml_escape(text: str) -> str:
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


@lru_cache(maxsize=8192)
def char_width(char: str, font_size: float) -> float:
    """
    估算单个字符的渲染宽度。
    CJK字符约为 font_size 宽，ASCII约为 font_size * 0.6。
    """
    cp = ord(char)
    if (0x2E80 <= cp <= 0x9FFF) or \
       (0xF900 <= cp <= 0xFAFF) or \
       (0xFE30 <= cp <= 0xFE4F) or \
       (0xFF00 <= cp <= 0xFFEF) or \
       (0x20000 <= cp <= 0x2FA1F) or \
       (0x3000 <= cp <= 0x303F):
        return font_size * 1.0
    return font_size * 0.6


@lru_cache(maxsize=32768)
def _str_width_cached(text: str, font_size: float) -> float:
    return sum(char_width(c, font_size) for c in text)


def str_width(text: str, font_size: float) -> float:
    """
    估算字符串的渲染宽度。

    Note: caching full strings can create memory pressure for huge files.
    We only cache "short" strings; long strings are computed directly.
    """
    if text.isascii():
        return len(text) * font_size * 0.6
    if len(text) > 256:
        return sum(char_width(c, font_size) for c in text)
    return _str_width_cached(text, font_size)


def truncate_to_width(text: str, font_size: float, max_width: float) -> str:
    """将字符串截断到不超过 max_width 像素宽度"""
    if text.isascii():
        max_chars = int(max_width / (font_size * 0.6))
        if len(text) <= max_chars:
            return text
        return text[:max(0, max_chars)] + "…"

    w = 0.0
    for i, c in enumerate(text):
        w += char_width(c, font_size)
        if w > max_width:
            return text[:i] + "…"
    return text

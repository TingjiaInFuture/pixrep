from dataclasses import dataclass
import os

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


@dataclass(frozen=True)
class FontRegistry:
    normal: str
    bold: str
    mono: str
    mono_bold: str


def register_fonts() -> FontRegistry:
    """
    注册 CJK 字体。关键点：
    - 比例字体: 用于标题、段落、表格等
    - 等宽字体: 用于代码块。系统一般没有中文等宽字体，
      所以用同一个 CJK 字体同时作为 mono 使用。
    """
    candidates = [
        (r"C:\Windows\Fonts\msyh.ttc", "CJK_Normal"),
        (r"C:\Windows\Fonts\msyhbd.ttc", "CJK_Bold"),
        (r"C:\Windows\Fonts\simhei.ttf", "CJK_Normal"),
        (r"C:\Windows\Fonts\simsun.ttc", "CJK_Normal"),
        ("/System/Library/Fonts/PingFang.ttc", "CJK_Normal"),
        ("/System/Library/Fonts/STHeiti Medium.ttc", "CJK_Normal"),
        ("/System/Library/Fonts/STHeiti Light.ttc", "CJK_Normal"),
        ("/Library/Fonts/Arial Unicode.ttf", "CJK_Normal"),
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", "CJK_Normal"),
        ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "CJK_Normal"),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "CJK_Normal"),
        ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", "CJK_Normal"),
        ("/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc", "CJK_Normal"),
    ]

    registered_normal = False
    registered_bold = False

    for font_path, font_name in candidates:
        if not os.path.exists(font_path):
            continue
        try:
            if font_name == "CJK_Bold" and not registered_bold:
                pdfmetrics.registerFont(TTFont("CJK_Bold", font_path))
                registered_bold = True
                print(f"  🔤 CJK Bold : {font_path}")
            elif font_name == "CJK_Normal" and not registered_normal:
                pdfmetrics.registerFont(TTFont("CJK_Normal", font_path))
                registered_normal = True
                print(f"  🔤 CJK Normal: {font_path}")
                if not registered_bold:
                    try:
                        pdfmetrics.registerFont(TTFont("CJK_Bold", font_path))
                        registered_bold = True
                    except Exception:
                        pass
        except Exception as exc:
            print(f"  ⚠️  Font registration failed for {font_path}: {exc}")
            continue

        if registered_normal and registered_bold:
            break

    if registered_normal:
        print("  ✅ All rendering will use CJK font")
        return FontRegistry(
            normal="CJK_Normal",
            bold="CJK_Bold" if registered_bold else "CJK_Normal",
            mono="CJK_Normal",
            mono_bold="CJK_Bold" if registered_bold else "CJK_Normal",
        )

    print("  ⚠️  No CJK font found! Chinese characters will show as □")
    print("     On Windows: msyh.ttc should exist in C:\\Windows\\Fonts\\")
    print("     On Linux : apt install fonts-wqy-microhei")
    print("     On macOS : PingFang should be built-in")
    return FontRegistry(
        normal="Helvetica",
        bold="Helvetica-Bold",
        mono="Courier",
        mono_bold="Courier-Bold",
    )

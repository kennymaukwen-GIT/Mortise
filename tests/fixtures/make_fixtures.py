"""產生測試用的原生 PDF（內容與設定都已知，可當標準答案）。需要 LibreOffice。

    python tests/fixtures/make_fixtures.py
"""

from __future__ import annotations

import io
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent


def _photo(w: int = 640, h: int = 400, seed: int = 0) -> io.BytesIO:
    """一張有漸層與雜訊的假照片。"""
    import numpy as np

    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    base = np.stack([80 + 120 * x / w, 60 + 140 * y / h, 160 - 80 * x / w], axis=2)
    img = Image.fromarray(np.clip(base + rng.normal(0, 12, base.shape), 0, 255).astype("uint8"))
    d = ImageDraw.Draw(img)
    d.ellipse((w * 0.2, h * 0.2, w * 0.6, h * 0.8), fill=(235, 200, 70))
    d.rectangle((w * 0.55, h * 0.45, w * 0.9, h * 0.85), fill=(40, 60, 90))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


def _to_pdf(path: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["soffice", f"-env:UserInstallation=file://{tmp}/p", "--headless", "--convert-to", "pdf", "--outdir", str(HERE), str(path)], capture_output=True, timeout=180, check=True)


def slide_dark() -> None:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Inches, Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    s = prs.slides.add_slide(prs.slide_layouts[6])
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = RGBColor(0x14, 0x1E, 0x30)

    def tb(x, y, w, h, paras, align=None):
        tf = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)).text_frame
        tf.word_wrap = True
        for i, runs in enumerate(paras):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            if align:
                p.alignment = align
            for text, size, bold, italic, color, font in runs:
                r = p.add_run()
                r.text, r.font.size, r.font.bold, r.font.italic, r.font.name = text, Pt(size), bold, italic, font
                r.font.color.rgb = RGBColor(*color)

    white, gold, grey = (0xFF, 0xFF, 0xFF), (0xF2, 0xC1, 0x4E), (0xB8, 0xC4, 0xD6)
    tb(1.0, 0.6, 11.3, 1.2, [[("產品路線圖 Product Roadmap 2027", 44, True, False, white, "Liberation Sans")]], PP_ALIGN.CENTER)
    tb(1.0, 2.1, 5.6, 3.6, [
        [("• 第一季：完成影像辨識產線", 20, False, False, white, "Liberation Sans")],
        [("• 第二季：", 20, False, False, white, "Liberation Sans"), ("Word 與 Excel 輸出", 20, True, False, gold, "Liberation Sans")],
        [("• 第三季：", 20, False, False, white, "Liberation Sans"), ("desktop beta", 20, False, True, grey, "Liberation Serif")],
        [("• 第四季：正式上線", 20, False, False, white, "Liberation Sans")],
    ])
    tb(8.3, 6.6, 4.0, 0.5, [[("機密文件 Confidential", 12, False, False, grey, "Liberation Sans")]], PP_ALIGN.RIGHT)
    for i, col in enumerate([(0x2E, 0x86, 0xAB), (0xF2, 0xC1, 0x4E), (0xE8, 0x6A, 0x33)]):
        r = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(7.2 + i * 1.8), Inches(2.2), Inches(1.5), Inches(0.9))
        r.fill.solid()
        r.fill.fore_color.rgb = RGBColor(*col)
        r.line.fill.background()
        r.shadow.inherit = False
    e = s.shapes.add_shape(MSO_SHAPE.OVAL, Inches(7.2), Inches(3.5), Inches(1.5), Inches(1.5))
    e.fill.solid()
    e.fill.fore_color.rgb = RGBColor(0x8E, 0xC0, 0x7C)
    e.line.fill.background()
    e.shadow.inherit = False
    ln = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(1.0), Inches(1.9), Inches(12.3), Inches(1.9))
    ln.line.color.rgb = RGBColor(*gold)
    ln.line.width = Pt(2)
    s.shapes.add_picture(_photo(seed=1), Inches(9.2), Inches(3.5), Inches(3.1), Inches(1.94))
    path = HERE / "slide_dark.pptx"
    prs.save(path)
    _to_pdf(path)
    path.unlink()


def doc_a4() -> None:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    d = Document()
    st = d.styles["Normal"]
    st.font.name, st.font.size = "Liberation Serif", Pt(11)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "WenQuanYi Zen Hei")
    h = d.add_paragraph()
    r = h.add_run("年度營運報告 Annual Report")
    r.font.size, r.font.bold, r.font.name = Pt(22), True, "Liberation Sans"
    r.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
    for text in [
        "本報告說明公司在過去一年的營運成果。營收較前一年成長百分之十八，主要來自企業客戶的訂閱收入，以及新推出的文件還原服務。The company expanded into two new markets and doubled the size of its engineering team during the year.",
        "下一年度的重點是提升辨識的精準度。我們會持續收集真實樣本，並依使用者的回饋調整各階段的參數。Accuracy on photographed posters remains the hardest problem and is the main focus of the roadmap.",
    ]:
        p = d.add_paragraph(text)
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.space_after = Pt(8)
    for i, item in enumerate(["收集一百份真實文件作為評測集", "建立每個階段的評分指標", "每次升級前先跑回歸測試"], start=1):
        d.add_paragraph(f"{i}. {item}")
    t = d.add_table(rows=4, cols=3)
    t.style = "Table Grid"
    rows = [("項目 Item", "上半年 H1", "下半年 H2"), ("營收（萬元）", "1,250", "1,480"), ("客戶數", "86", "112"), ("毛利率", "61%", "64%")]
    for ri, row in enumerate(rows):
        for ci, text in enumerate(row):
            cell = t.cell(ri, ci)
            cell.text = text
            if ri == 0:
                cell.paragraphs[0].runs[0].font.bold = True
    d.add_paragraph()
    d.add_page_break()
    p = d.add_paragraph("附錄：名詞說明")
    p.runs[0].font.size, p.runs[0].font.bold = Pt(16), True
    d.add_paragraph("還原度報告：列出每個元素的每個欄位是精確擷取、量測、推論，還是無法還原。")
    path = HERE / "doc_a4.docx"
    d.save(path)
    _to_pdf(path)
    path.unlink()


def poster() -> None:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    fonts = {"LS": "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "LSB": "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", "LSI": "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf"}
    for name, path in fonts.items():
        pdfmetrics.registerFont(TTFont(name, path))
    w, h = 841.89, 1190.551  # A3 直
    c = canvas.Canvas(str(HERE / "poster.pdf"), pagesize=(w, h))
    c.setTitle("poster")
    c.setFillColor(HexColor("#F4EBD9"))
    c.rect(0, 0, w, h, stroke=0, fill=1)
    c.setFillColor(HexColor("#C8553D"))
    c.rect(0, h - 300, w, 300, stroke=0, fill=1)
    c.setFillColor(HexColor("#FFFFFF"))
    c.setFont("LSB", 96)
    c.drawString(60, h - 170, "SUMMER FEST")
    c.setFont("LS", 37.2)  # 刻意用不整齊的字級（風險 R3：海報的數值是任意的）
    c.drawString(64, h - 240, "Music, food and design market")
    c.drawImage(ImageReader(_photo(900, 600, seed=3)), 60, 380, width=721.89, height=440)
    c.setFillColor(HexColor("#2B2D42"))
    c.setFont("LSB", 54)
    c.drawString(60, 290, "12 – 14 JULY 2027")
    c.setFont("LS", 22)
    c.drawString(60, 240, "Riverside Park, gates open at 10:00")
    c.drawString(60, 210, "Free entry for children under twelve")
    c.setFont("LSI", 18)
    c.drawRightString(w - 60, 70, "Tickets at summerfest.example")
    c.setFillColor(HexColor("#EDAE49"))
    c.roundRect(560, 200, 221.89, 120, 18, stroke=0, fill=1)
    c.setFillColor(HexColor("#2B2D42"))
    c.setFont("LSB", 30)
    c.drawCentredString(670.9, 250, "FROM $25")
    c.saveState()
    c.translate(w - 40, 420)
    c.rotate(90)
    c.setFont("LS", 14)
    c.drawString(0, 0, "Organised by the city arts council")
    c.restoreState()
    c.setStrokeColor(HexColor("#2B2D42"))
    c.setLineWidth(3)
    c.line(60, 340, w - 60, 340)
    c.showPage()
    c.save()


if __name__ == "__main__":
    slide_dark()
    doc_a4()
    poster()
    print(sorted(p.name for p in HERE.glob("*.pdf")))

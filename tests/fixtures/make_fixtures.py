"""產生測試用的原生 PDF（內容與設定都已知，可當標準答案）。需要 LibreOffice。

    python tests/fixtures/make_fixtures.py            全部重新產生
    python tests/fixtures/make_fixtures.py vectors    只產生向量圖的三個（vector_*.pdf；不需要 LibreOffice，輸出的位元組固定）
    python tests/fixtures/make_fixtures.py flattened  只產生被扁平化內容的兩個（flattened_*.pdf；不需要 LibreOffice，輸出的位元組固定）
    python tests/fixtures/make_fixtures.py cv         只產生假資料的兩欄履歷（cv_two_column.pdf；不需要 LibreOffice，輸出的位元組固定）
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


def deck() -> None:
    """三頁簡報：每頁相同位置有標誌、頁尾文字與色帶（母片候選），頁碼每頁不同。"""
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches, Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    logo = Image.new("RGB", (240, 80), (31, 58, 95))
    ImageDraw.Draw(logo).ellipse((10, 10, 70, 70), fill=(242, 193, 78))
    buf = io.BytesIO()
    logo.save(buf, "PNG")
    titles = ["市場概況 Market Overview", "產品策略 Product Strategy", "財務預測 Financial Outlook"]
    bodies = ["今年的文件還原需求持續成長，企業客戶占比超過六成。", "第一階段專注檔案急救，第二階段推出靈感模板。", "預計明年營收成長百分之四十，毛利率維持在六成以上。"]
    for i, (title, body) in enumerate(zip(titles, bodies), start=1):
        s = prs.slides.add_slide(prs.slide_layouts[6])
        band = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, Inches(7.1), prs.slide_width, Inches(0.4))
        band.fill.solid()
        band.fill.fore_color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
        band.line.fill.background()
        band.shadow.inherit = False
        buf.seek(0)
        s.shapes.add_picture(buf, Inches(11.3), Inches(0.3), Inches(1.6), Inches(0.53))
        for (x, y, w, h, text, size, bold, color) in [
            (0.8, 0.4, 10.0, 1.0, title, 36, True, (0x1F, 0x3A, 0x5F)),
            (0.8, 1.8, 11.5, 2.0, body, 20, False, (0x33, 0x33, 0x33)),
            (0.8, 7.15, 6.0, 0.3, "Mortise 內部資料 Internal", 12, False, (0xFF, 0xFF, 0xFF)),
            (12.3, 7.15, 0.6, 0.3, str(i), 12, False, (0xFF, 0xFF, 0xFF)),
        ]:
            tf = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)).text_frame
            tf.word_wrap = True
            r = tf.paragraphs[0].add_run()
            r.text, r.font.size, r.font.bold, r.font.name = text, Pt(size), bold, "Liberation Sans"
            r.font.color.rgb = RGBColor(*color)
    path = HERE / "deck.pptx"
    prs.save(path)
    _to_pdf(path)
    path.unlink()


# ====================================================================== 向量圖的 fixture
#
# 三個檔的內容與預期結果（tests/test_native_vectors.py 逐項檢查）：
#   vector_logo.pdf     多路徑標誌（帶孔、筆畫樣式、半透明、虛線、夾在中間的矩形）、巢狀表單裡的標誌、
#                       被文字隔開的圖形、單獨的形狀、三個箭頭、表格、底線與文字框
#   vector_paint.pdf    漸層（花紋填色、sh 運算子、表單內）、讀不出來的東西（非線性漸層、花紋、混合模式、
#                       透明遮罩、裁切、透明群組）、被蓋住的路徑、可以化簡的裁切、
#                       畫表單之前的裁切（真的切到的、整個包住所以沒有影響的）
#   vector_outlined.pdf 文字轉外框（一字一條路徑、整段一條路徑）、緊鄰的插畫、超過數量上限的密集插畫

_SANS = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
_SANS_BOLD = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"


def _vector_canvas(name: str, size: tuple[float, float]):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    for font, path in (("LS", _SANS), ("LSB", _SANS_BOLD)):
        pdfmetrics.registerFont(TTFont(font, path))
    c = canvas.Canvas(str(HERE / name), pagesize=size, invariant=1)  # invariant：不寫日期與隨機編號，輸出固定
    c.setTitle(name.removesuffix(".pdf"))
    return c


def _poly(c, pts, close=True):
    p = c.beginPath()
    p.moveTo(*pts[0])
    for x, y in pts[1:]:
        p.lineTo(x, y)
    if close:
        p.close()
    return p


def vector_logo() -> None:
    """標誌與一般形狀同頁：標誌要成為向量圖，其他的維持是形狀、表格、文字。"""
    import math

    from reportlab.lib.colors import Color, HexColor

    w, h = 595.276, 841.89
    c = _vector_canvas("vector_logo.pdf", (w, h))
    c.setFillColor(HexColor("#1F3A5F"))
    c.setFont("LSB", 20)
    c.drawString(60, 790, "Vector logo fixture")

    # ---- 標誌 A：八條路徑疊在一起（盾、環、小方塊、菱形、波浪線、鋸齒線、半透明色塊、虛線）
    p = c.beginPath()  # 盾
    p.moveTo(70, 740)
    p.curveTo(110, 750, 150, 750, 190, 740)
    p.lineTo(190, 670)
    p.curveTo(190, 630, 150, 610, 130, 600)
    p.curveTo(110, 610, 70, 630, 70, 670)
    p.close()
    c.setFillColor(HexColor("#1F3A5F"))
    c.drawPath(p, stroke=0, fill=1)
    p = c.beginPath()  # 環：兩個同方向的圓，用奇偶規則挖孔
    p.circle(130, 690, 34)
    p.circle(130, 690, 20)
    c.setFillColor(HexColor("#F2C14E"))
    c.drawPath(p, stroke=0, fill=1, fillMode=0)
    c.setFillColor(HexColor("#C8553D"))  # 小方塊：基本形狀，夾在標誌中間
    c.rect(124, 684, 12, 12, stroke=0, fill=1)
    p = _poly(c, [(130, 712), (152, 690), (130, 668), (108, 690)])  # 菱形：內圈反方向，用非零規則挖孔
    p.moveTo(130, 702)
    for x, y in [(118, 690), (130, 678), (142, 690)]:
        p.lineTo(x, y)
    p.close()
    c.setFillColor(HexColor("#2E86AB"))
    c.drawPath(p, stroke=0, fill=1, fillMode=1)
    c.setStrokeColor(HexColor("#E86A33"))  # 波浪線：圓頭、圓角
    c.setLineWidth(4)
    c.setLineCap(1)
    c.setLineJoin(1)
    p = c.beginPath()
    p.moveTo(80, 655)
    p.curveTo(95, 670, 110, 640, 125, 655)
    p.curveTo(140, 670, 155, 640, 180, 655)
    c.drawPath(p, stroke=1, fill=0)
    c.setStrokeColor(HexColor("#2B2D42"))  # 鋸齒線：方頭、斜角
    c.setLineWidth(3)
    c.setLineCap(2)
    c.setLineJoin(2)
    c.drawPath(_poly(c, [(85, 625), (100, 640), (115, 625), (130, 640), (145, 625), (160, 640), (175, 625)], close=False), stroke=1, fill=0)
    c.setLineCap(0)
    c.setLineJoin(0)
    c.saveState()  # 半透明色塊：填色 50%、筆畫 80%
    c.setFillColor(Color(0x8E / 255, 0xC0 / 255, 0x7C / 255, alpha=0.5))
    c.setStrokeColor(Color(0x2B / 255, 0x2D / 255, 0x42 / 255, alpha=0.8))
    c.setLineWidth(1.5)
    p = c.beginPath()
    p.moveTo(150, 720)
    p.curveTo(175, 735, 195, 715, 185, 695)
    p.curveTo(175, 680, 150, 690, 150, 720)
    p.close()
    c.drawPath(p, stroke=1, fill=1)
    c.restoreState()
    c.saveState()  # 虛線
    c.setStrokeColor(HexColor("#C8553D"))
    c.setLineWidth(2)
    c.setDash([6, 3], 0)
    p = c.beginPath()
    p.moveTo(75, 745)
    p.curveTo(110, 765, 150, 765, 185, 745)
    c.drawPath(p, stroke=1, fill=0)
    c.restoreState()

    # ---- 標誌 B：畫在兩層表單（Form XObject）裡，每層都有旋轉與縮放。
    # 表單的範圍框（BBox）要包住內容：ReportLab 預設用整頁大小，圍繞原點畫的內容會被切掉四分之三
    c.beginForm("mark", -45, -45, 45, 45)
    for k, col in enumerate(["#F2C14E", "#E86A33", "#8EC07C", "#2E86AB"]):
        c.saveState()
        c.rotate(90 * k)
        c.setFillColor(HexColor(col))
        p = c.beginPath()
        p.moveTo(4, 0)
        p.curveTo(14, 10, 30, 14, 40, 0)
        p.curveTo(30, -14, 14, -10, 4, 0)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
        c.restoreState()
    c.endForm()
    c.beginForm("badge", -60, -60, 60, 60)
    c.setFillColor(HexColor("#141E30"))
    p = c.beginPath()  # 不規則的圓盤（不是正圓，所以是自由路徑）
    p.moveTo(50, 0)
    p.curveTo(50, 30, 28, 52, 0, 50)
    p.curveTo(-30, 48, -52, 28, -50, 0)
    p.curveTo(-48, -30, -28, -52, 0, -50)
    p.curveTo(30, -48, 52, -28, 50, 0)
    p.close()
    c.drawPath(p, stroke=0, fill=1)
    c.saveState()
    c.rotate(45)
    c.scale(0.8, 0.8)
    c.doForm("mark")
    c.restoreState()
    star = [(11 * math.cos(math.radians(90 + 36 * i)) * (1 if i % 2 == 0 else 0.45), 11 * math.sin(math.radians(90 + 36 * i)) * (1 if i % 2 == 0 else 0.45)) for i in range(10)]
    c.setFillColor(HexColor("#FFFFFF"))
    c.setStrokeColor(HexColor("#C8553D"))
    c.setLineWidth(1)
    c.setLineJoin(1)
    c.drawPath(_poly(c, star), stroke=1, fill=1)
    c.endForm()
    c.saveState()
    c.translate(430, 680)
    c.rotate(-15)
    c.scale(1.2, 1.2)
    c.doForm("badge")
    c.restoreState()

    # ---- 單獨的形狀：要維持是形狀
    c.setFillColor(HexColor("#2E86AB"))
    c.rect(60, 500, 80, 50, stroke=0, fill=1)
    c.setFillColor(HexColor("#EDAE49"))
    c.roundRect(160, 500, 80, 50, 10, stroke=0, fill=1)
    c.setFillColor(HexColor("#8EC07C"))
    c.ellipse(260, 500, 340, 550, stroke=0, fill=1)
    c.setStrokeColor(HexColor("#2B2D42"))
    c.setLineWidth(2)
    c.line(360, 525, 440, 525)
    c.setFillColor(HexColor("#C8553D"))
    c.drawPath(_poly(c, [(460, 500), (500, 550), (540, 500)]), stroke=0, fill=1)  # 三角形：單獨的簡單路徑
    for k, col in enumerate(["#1F3A5F", "#2E86AB", "#8EC07C"]):  # 三個相鄰的箭頭：少量簡單路徑
        x = 60 + 64 * k
        c.setFillColor(HexColor(col))
        c.drawPath(_poly(c, [(x, 430), (x + 45, 430), (x + 60, 450), (x + 45, 470), (x, 470), (x + 15, 450)]), stroke=0, fill=1)

    # ---- 文字的底線與框：不可被吸進向量圖
    c.setFillColor(HexColor("#2B2D42"))
    c.setFont("LS", 14)
    c.drawString(60, 385, "Underlined heading")
    c.setLineWidth(1)
    c.line(60, 381, 181, 381)
    c.rect(300, 375, 150, 26, stroke=1, fill=0)
    c.drawString(310, 383, "Boxed label")

    # ---- 表格：格線是直線
    c.setLineWidth(0.75)
    for y in (340, 310, 280, 250):
        c.line(60, y, 420, y)
    for x in (60, 180, 300, 420):
        c.line(x, 250, x, 340)
    c.setFont("LS", 11)
    for r, row in enumerate([("Item", "Q1", "Q2"), ("Revenue", "1,250", "1,480"), ("Margin", "61%", "64%")]):
        for k, text in enumerate(row):
            c.drawString(68 + 120 * k, 320 - 30 * r, text)

    # ---- 標誌 C：底板、文字、蓋在文字上的裝飾。文字夾在中間，所以底板與裝飾不能合成同一個向量圖
    c.setFillColor(HexColor("#F4EBD9"))
    p = c.beginPath()
    p.moveTo(330, 190)
    p.curveTo(330, 240, 380, 236, 435, 232)
    p.curveTo(490, 236, 545, 240, 545, 190)
    p.curveTo(545, 140, 490, 146, 435, 150)
    p.curveTo(380, 146, 330, 140, 330, 190)
    p.close()
    c.drawPath(p, stroke=0, fill=1)
    c.setFillColor(HexColor("#1F3A5F"))
    c.setFont("LSB", 24)
    c.drawString(360, 181, "MORTISE")
    c.setStrokeColor(HexColor("#C8553D"))
    c.setLineWidth(1.5)
    c.setLineJoin(1)
    for y0 in (207, 177):  # 兩條波浪線（曲線）
        p = c.beginPath()
        p.moveTo(352, y0)
        for i in range(8):
            x0, dy = 352 + 15 * i, (5 if i % 2 == 0 else -5)
            p.curveTo(x0 + 5, y0 + dy, x0 + 10, y0 + dy, x0 + 15, y0)
        c.drawPath(p, stroke=1, fill=0)
    star = [(492 + 22 * math.cos(math.radians(90 + 36 * i)) * (1 if i % 2 == 0 else 0.45), 190 + 22 * math.sin(math.radians(90 + 36 * i)) * (1 if i % 2 == 0 else 0.45)) for i in range(10)]
    c.setFillColor(HexColor("#F2C14E"))
    c.drawPath(_poly(c, star), stroke=0, fill=1)
    c.showPage()
    c.save()


def vector_paint() -> None:
    """漸層、花紋、混合模式、透明遮罩、裁切、透明群組：哪些讀得出來、哪些要留在底圖。內容串流直接手寫。"""
    import pikepdf
    from pikepdf import Array, Dictionary, Name, Stream

    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(600, 400))

    def fn(c0, c1, n=1):
        return Dictionary(FunctionType=2, Domain=[0, 1], C0=c0, C1=c1, N=n)

    def axial(coords, function, extend=True):
        return Dictionary(ShadingType=2, ColorSpace=Name.DeviceRGB, Coords=coords, Function=function, Extend=[extend, extend])

    def pattern(shading, matrix):
        return pdf.make_indirect(Dictionary(Type=Name.Pattern, PatternType=2, Shading=shading, Matrix=matrix))

    three = Dictionary(FunctionType=3, Domain=[0, 1], Bounds=[0.4], Encode=[0, 1, 0, 1], Functions=Array([fn([0.12, 0.23, 0.37], [0.95, 0.76, 0.31]), fn([0.95, 0.76, 0.31], [0.78, 0.33, 0.24])]))
    tile = Stream(pdf, b"0.78 0.33 0.24 rg 0 0 4 4 re f")
    tile.Type, tile.PatternType, tile.PaintType, tile.TilingType = Name.Pattern, 1, 1, 1
    tile.BBox, tile.XStep, tile.YStep, tile.Resources = [0, 0, 8, 8], 8, 8, Dictionary()
    mask = Stream(pdf, b"0.6 g 0 0 600 400 re f", Type=Name.XObject, Subtype=Name.Form, BBox=[0, 0, 600, 400], Group=Dictionary(S=Name.Transparency, CS=Name.DeviceGray))
    group = Stream(pdf, b"0.18 0.53 0.67 rg 170 70 m 250 70 l 250 130 l 170 140 l h f 0.91 0.42 0.2 rg 210 80 m 290 80 l 290 140 l 210 130 l h f",
                   Type=Name.XObject, Subtype=Name.Form, BBox=[0, 0, 600, 400], Group=Dictionary(S=Name.Transparency, CS=Name.DeviceRGB))
    inner = Stream(pdf, b"/Pattern cs /PF scn 0 0 m 0 60 l 40 70 l 80 60 l 80 0 l h f", Type=Name.XObject, Subtype=Name.Form, BBox=[0, 0, 80, 70], Matrix=[1, 0, 0, 1, 320, 70],
                   Resources=Dictionary(Pattern=Dictionary(PF=pattern(axial([0, 0, 80, 0], fn([0.56, 0.75, 0.49], [0.12, 0.23, 0.37])), [1, 0, 0, 1, 0, 0]))))
    # 兩個一樣的小表單（各畫一片葉子與一個小三角形），差別在畫它之前的裁切：一個真的被三角形切到，一個的裁切框（轉了 30 度的矩形）整個包住它
    leaf = b"0.18 0.53 0.67 rg 0 0 m 10 25 30 30 40 10 c 30 -5 10 -5 0 0 c h f 0.95 0.76 0.31 rg 12 6 m 28 6 l 20 18 l h f"
    cut = Stream(pdf, leaf, Type=Name.XObject, Subtype=Name.Form, BBox=[-5, -10, 45, 35])
    kept = Stream(pdf, leaf, Type=Name.XObject, Subtype=Name.Form, BBox=[-5, -10, 45, 35])
    page.Resources = Dictionary(
        Font=Dictionary(F1=Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)),
        Pattern=Dictionary(
            P1=pattern(axial([0, 0, 100, 0], fn([0.95, 0.76, 0.31], [0.78, 0.33, 0.24])), [0.8, 0.6, -0.6, 0.8, 70, 270]),  # 轉 36.87 度的線性漸層
            P2=pdf.make_indirect(tile),
            P3=pattern(axial([500, 0, 580, 0], fn([1, 1, 1], [0.12, 0.23, 0.37], n=2)), [1, 0, 0, 1, 0, 0]),  # 指數 2：顏色不是直線變化
            P4=pattern(axial([380, 0, 480, 0], three), [1, 0, 0, 1, 0, 0]),  # 三個色標
        ),
        Shading=Dictionary(
            Sh1=Dictionary(ShadingType=3, ColorSpace=Name.DeviceRGB, Coords=[300, 320, 0, 300, 320, 40], Function=fn([1, 1, 1], [0.18, 0.53, 0.67]), Extend=[True, True]),
            Sh2=axial([0, 0, 0, 400], fn([0.96, 0.92, 0.85], [0.85, 0.9, 0.96])),
        ),
        ExtGState=Dictionary(GSm=Dictionary(BM=Name.Multiply), GSs=Dictionary(SMask=Dictionary(Type=Name.Mask, S=Name.Luminosity, G=pdf.make_indirect(mask))), GSa=Dictionary(ca=0.5)),
        XObject=Dictionary(Grp=pdf.make_indirect(group), Inner=pdf.make_indirect(inner), Cut=pdf.make_indirect(cut), Kept=pdf.make_indirect(kept)),
    )
    k = 0.5523 * 40
    circle = f"340 320 m 340 {320 + k:.3f} {300 + k:.3f} 360 300 360 c {300 - k:.3f} 360 260 {320 + k:.3f} 260 320 c 260 {320 - k:.3f} {300 - k:.3f} 280 300 280 c {300 + k:.3f} 280 340 {320 - k:.3f} 340 320 c h"
    k2 = 0.5523 * 30
    clipped_circle = f"475 205 m 475 {205 + k2:.3f} {445 + k2:.3f} 235 445 235 c {445 - k2:.3f} 235 415 {205 + k2:.3f} 415 205 c 415 {205 - k2:.3f} {445 - k2:.3f} 175 445 175 c {445 + k2:.3f} 175 475 {205 - k2:.3f} 475 205 c h"
    import math

    star = [(550 + 30 * math.cos(math.radians(90 + 36 * i)) * (1 if i % 2 == 0 else 0.45), 210 + 30 * math.sin(math.radians(90 + 36 * i)) * (1 if i % 2 == 0 else 0.45)) for i in range(10)]
    star_path = f"{star[0][0]:.3f} {star[0][1]:.3f} m " + " ".join(f"{x:.3f} {y:.3f} l" for x, y in star[1:]) + " h"
    content = f"""
/Sh2 sh
/Pattern cs /P1 scn 60 300 m 60 360 140 380 180 340 c 200 300 160 260 120 270 c 90 275 60 280 60 300 c h f
0.12 0.23 0.37 rg 100 310 m 120 350 l 150 320 l 135 290 l h f
1 1 1 RG 3 w 1 J 70 300 m 90 320 110 280 130 300 c 150 320 170 280 190 300 c S 0 J
q {circle} W n /Sh1 sh Q
/Pattern cs /P4 scn 380 290 100 60 re f
/Pattern cs /P3 scn 500 290 80 60 re f
/Pattern cs /P2 scn 60 180 m 100 240 l 140 180 l h f
0.78 0.33 0.24 rg 170 180 m 210 240 l 250 180 l h f
q /GSm gs 0 0.5 0 rg 200 170 m 240 250 l 280 170 l h f Q
0.95 0.76 0.31 rg 255 200 m 275 235 l 295 200 l h f
q /GSs gs 0 0 0.5 rg 310 180 m 350 240 l 390 180 l h f Q
q 420 190 50 30 re W n 0.03 0.23 0.3 rg {clipped_circle} f Q
q {star_path} W n 0.56 0.75 0.49 rg 500 170 100 80 re f Q
q 60 80 60 60 re W n 0.18 0.53 0.67 rg 40 100 120 20 re f Q
q /GSa gs /Grp Do Q
/Inner Do
q 430 75 m 470 75 l 450 110 l h W n 1 0 0 1 430 80 cm /Cut Do Q
q 0.866 0.5 -0.5 0.866 530 70 cm -8 -12 56 50 re W n /Kept Do Q
0 0 0 rg BT /F1 12 Tf 60 30 Td (Paint fixture) Tj ET
"""
    page.Contents = Stream(pdf, content.encode("ascii"))
    pdf.save(HERE / "vector_paint.pdf", static_id=True)


def _outline_text(c, font_path: str, text: str, x: float, y: float, size: float, single_path: bool = False, lines: list[str] | None = None, leading: float = 1.25, font_number: int = 0) -> float:
    """把文字畫成外框（每個字一條填色路徑；single_path 時整段只用一條路徑）。回傳最後一行結尾的 x。font_number 是字型集合檔（.ttc）裡的第幾個。"""
    from fontTools.pens.basePen import BasePen
    from fontTools.ttLib import TTFont

    font = TTFont(font_path, fontNumber=font_number) if font_path.endswith(".ttc") else TTFont(font_path)
    glyphs, cmap, scale = font.getGlyphSet(), font.getBestCmap(), size / font["head"].unitsPerEm

    class Pen(BasePen):  # TrueType 的二次曲線由 BasePen 升成三次曲線（數學上相等）
        def __init__(self, path, ox, oy):
            super().__init__(glyphs)
            self.path, self.ox, self.oy = path, ox, oy

        def _pt(self, pt):
            return self.ox + pt[0] * scale, self.oy + pt[1] * scale

        def _moveTo(self, pt):
            self.path.moveTo(*self._pt(pt))

        def _lineTo(self, pt):
            self.path.lineTo(*self._pt(pt))

        def _curveToOne(self, a, b, d):
            self.path.curveTo(*self._pt(a), *self._pt(b), *self._pt(d))

        def _closePath(self):
            self.path.close()

    whole = c.beginPath() if single_path else None
    end = x
    for row, line in enumerate(lines or [text]):
        cx = x
        for ch in line:
            name = cmap.get(ord(ch))
            if name is None:
                continue
            if ch.strip():
                path = whole if single_path else c.beginPath()
                glyphs[name].draw(Pen(path, cx, y - row * size * leading))
                if not single_path:
                    c.drawPath(path, stroke=0, fill=1, fillMode=1)
            cx += glyphs[name].width * scale
        end = cx
    if single_path:
        c.drawPath(whole, stroke=0, fill=1, fillMode=1)
    return end


def vector_outlined() -> None:
    """文字轉外框。第 1 頁有真的文字（①不會起疑）；第 2 頁完全沒有文字、路徑很多（①會標記疑似轉外框）。"""
    import math

    from reportlab.lib.colors import HexColor

    w, h = 595.276, 841.89
    c = _vector_canvas("vector_outlined.pdf", (w, h))
    c.setFillColor(HexColor("#555555"))
    c.setFont("LS", 12)
    c.drawString(60, 790, "Outlined text fixture: the lines below are paths, not text.")

    def sun(cx: float, cy: float, r: float) -> None:  # 小太陽：一個不規則圓盤加八道光芒
        c.setFillColor(HexColor("#F2C14E"))
        p = c.beginPath()
        p.moveTo(cx + r, cy)
        p.curveTo(cx + r, cy + 0.6 * r, cx + 0.5 * r, cy + r, cx, cy + r)
        p.curveTo(cx - 0.6 * r, cy + r, cx - r, cy + 0.5 * r, cx - r, cy)
        p.curveTo(cx - r, cy - 0.6 * r, cx - 0.5 * r, cy - r, cx, cy - r)
        p.curveTo(cx + 0.6 * r, cy - r, cx + r, cy - 0.5 * r, cx + r, cy)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
        c.setFillColor(HexColor("#E86A33"))
        for i in range(8):
            a = math.radians(45 * i)
            ux, uy = math.cos(a), math.sin(a)
            c.drawPath(_poly(c, [(cx + 1.15 * r * ux - 0.2 * r * uy, cy + 1.15 * r * uy + 0.2 * r * ux), (cx + 1.7 * r * ux, cy + 1.7 * r * uy), (cx + 1.15 * r * ux + 0.2 * r * uy, cy + 1.15 * r * uy - 0.2 * r * ux)]), stroke=0, fill=1)

    c.setFillColor(HexColor("#2B2D42"))
    end = _outline_text(c, _SANS_BOLD, "", 60, 700, 26, lines=["Summer sale starts today", "Riverside Park, 10:00"])
    sun(431, 709, 9)  # 緊貼第一行的結尾：不可以和文字混成一群
    c.setFillColor(HexColor("#1F3A5F"))
    _outline_text(c, _SANS, "", 60, 600, 18, single_path=True, lines=["Whole paragraph drawn", "as one single path"])
    c.setFillColor(HexColor("#C8553D"))
    _outline_text(c, _SANS_BOLD, "SALE", 60, 480, 44)  # 只有四個字：這一頁有真的文字，不夠認定成文字轉外框
    c.showPage()

    # 第 2 頁：沒有任何文字
    c.setFillColor(HexColor("#C8553D"))
    _outline_text(c, _SANS_BOLD, "SALE", 60, 740, 44)
    cx, cy = 300, 420  # 密集插畫：320 片小葉子繞成螺旋，加一個 500 多段的鋸齒圓
    c.setFillColor(HexColor("#141E30"))
    c.drawPath(_poly(c, [(cx + (150 if i % 2 else 138) * math.cos(2 * math.pi * i / 520), cy + (150 if i % 2 else 138) * math.sin(2 * math.pi * i / 520)) for i in range(520)]), stroke=0, fill=1)
    palette = ["#2E86AB", "#F2C14E", "#E86A33", "#8EC07C"]
    for i in range(320):
        a, r = 0.35 * i, 12 + 0.36 * i
        x, y = cx + r * math.cos(a), cy + r * math.sin(a)
        ux, uy = math.cos(a + 0.6), math.sin(a + 0.6)
        c.setFillColor(HexColor(palette[i % 4]))
        p = c.beginPath()
        p.moveTo(x, y)
        p.curveTo(x + 6 * ux - 4 * uy, y + 6 * uy + 4 * ux, x + 12 * ux - 3 * uy, y + 12 * uy + 3 * ux, x + 16 * ux, y + 16 * uy)
        p.curveTo(x + 12 * ux + 3 * uy, y + 12 * uy - 3 * ux, x + 6 * ux + 4 * uy, y + 6 * uy - 4 * ux, x, y)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
    c.showPage()
    c.save()


# ====================================================================== 被扁平化的內容（tests/test_flattened.py）
#
#   flattened_outlined.pdf  整頁的字都轉成外框（沒有任何真的文字）：標題、內文（一行裡有一個紅字）、中文、圖說，加一個裝飾圖形
#   flattened_embedded.pdf  第 1 頁：原生標題與內文，加一張嵌入的圖表截圖（圖裡有字、長條、座標軸）
#                           第 2 頁：原生標題，加一張照片（不可以被拆）
#                           第 3 頁：整頁一張掃描的公文（JPEG），上面疊一行原生的紅字（①判成混合 PDF）
# 圖片裡的字畫在已知位置（PIL 以基線左端為錨點），測試依 FLATTENED_TRUTH 換算成頁面座標對答案。

_WQY = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"

FLATTENED_TRUTH = {
    # 外框字：（文字、字型、字級 pt、基線左端的 PDF 座標 x、y、顏色）；同一行分段畫的寫成同一行
    "outlined": [
        ("Flattened brochure", "bold", 30.0, 60.0, 760.0, "1F3A5F"),
        ("Every word on this page is a path.", "regular", 14.0, 60.0, 715.0, "222222"),
        ("Prices start at $49 per month.", "regular", 14.0, 60.0, 695.0, "222222"),
        ("文件反編譯器測試", "wqy", 20.0, 60.0, 650.0, "1F3A5F"),
        ("Printed 2026-11-22 in Taipei", "regular", 10.0, 60.0, 620.0, "555555"),
    ],
    # 圖表截圖：1000 × 640 像素，放在 PDF 的 x=60、頁頂往下 120 pt，寬 475 pt
    "chart": {"pixels": (1000, 640), "x": 60.0, "top": 120.0, "width": 475.0,
              "texts": [("Quarterly Sales 2026", "bold", 44, 40, 70), ("Units sold per quarter", "regular", 28, 40, 112)],
              "bars": [(160 + 200 * i, 560 - hgt, 280 + 200 * i, 560, col) for i, (hgt, col) in enumerate(
                  [(220, (46, 134, 171)), (300, (242, 193, 78)), (260, (232, 106, 51)), (360, (142, 192, 124))])],
              "labels": [(f"Q{i + 1}", 220 + 200 * i, 605) for i in range(4)]},
    # 掃描的公文：1240 × 1754 像素（A4、150 dpi），佔滿整頁
    "memo": {"pixels": (1240, 1754),
             "texts": [("MEMORANDUM", "bold", 48, 120, 200), ("To: All staff", "regular", 32, 120, 290),
                       ("Subject: Office move on December 1", "regular", 32, 120, 340),
                       ("Please pack your desks by Friday afternoon.", "regular", 30, 120, 440),
                       ("Movers arrive at nine in the morning.", "regular", 30, 120, 490),
                       ("Thank you for your patience.", "regular", 30, 120, 540)]},
}


def flattened_outlined() -> None:
    from reportlab.lib.colors import HexColor

    w, h = 595.276, 841.89
    c = _vector_canvas("flattened_outlined.pdf", (w, h))
    fonts = {"bold": _SANS_BOLD, "regular": _SANS, "wqy": _WQY}
    for text, font, size, x, y, color in FLATTENED_TRUTH["outlined"]:
        c.setFillColor(HexColor("#" + color))
        if text.startswith("Prices"):  # 一行分三段畫，中間的價格是紅字
            x = _outline_text(c, fonts[font], "Prices start at ", x, y, size)
            c.setFillColor(HexColor("#C8553D"))
            x = _outline_text(c, fonts[font], "$49", x, y, size)
            c.setFillColor(HexColor("#" + color))
            _outline_text(c, fonts[font], " per month.", x, y, size)
        else:
            _outline_text(c, fonts[font], text, x, y, size)
    c.setFillColor(HexColor("#2E86AB"))  # 裝飾：一片葉子形狀加一個圓，不是字
    p = c.beginPath()
    p.moveTo(450, 740)
    p.curveTo(470, 790, 520, 790, 530, 760)
    p.curveTo(510, 730, 470, 720, 450, 740)
    p.close()
    c.drawPath(p, stroke=0, fill=1)
    c.circle(490, 700, 18, stroke=0, fill=1)
    c.showPage()
    c.save()


def _flattened_images() -> tuple[Image.Image, Image.Image]:
    from PIL import ImageFont

    def font(kind: str, px: int):
        return ImageFont.truetype(_SANS_BOLD if kind == "bold" else _SANS, px)

    chart = FLATTENED_TRUTH["chart"]
    img = Image.new("RGB", chart["pixels"], (255, 255, 255))
    d = ImageDraw.Draw(img)
    for text, kind, px, x, y in chart["texts"]:
        d.text((x, y), text, font=font(kind, px), fill=(31, 58, 95) if kind == "bold" else (90, 90, 90), anchor="ls")
    d.line((100, 170, 100, 560), fill=(60, 60, 60), width=3)
    d.line((100, 560, 940, 560), fill=(60, 60, 60), width=3)
    for x0, y0, x1, y1, col in chart["bars"]:
        d.rectangle((x0, y0, x1 - 1, y1 - 1), fill=col)
        d.text(((x0 + x1) / 2, y0 - 14), str(560 - y0), font=font("bold", 26), fill=(40, 40, 40), anchor="ms")
    for text, x, y in chart["labels"]:
        d.text((x, y), text, font=font("regular", 28), fill=(40, 40, 40), anchor="ms")
    memo = FLATTENED_TRUTH["memo"]
    scan = Image.new("RGB", memo["pixels"], (252, 251, 248))
    d = ImageDraw.Draw(scan)
    for text, kind, px, x, y in memo["texts"]:
        d.text((x, y), text, font=font(kind, px), fill=(25, 25, 30), anchor="ls")
    d.line((120, 380, 1120, 380), fill=(25, 25, 30), width=2)
    return img, scan


def flattened_embedded() -> None:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.utils import ImageReader

    w, h = 595.276, 841.89
    c = _vector_canvas("flattened_embedded.pdf", (w, h))
    chart, scan = _flattened_images()
    # 第 1 頁：原生文字 + 圖表截圖
    c.setFillColor(HexColor("#1F3A5F"))
    c.setFont("LSB", 24)
    c.drawString(60, h - 80, "Annual Report Highlights")
    c.setFillColor(HexColor("#333333"))
    c.setFont("LS", 12)
    c.drawString(60, h - 105, "The chart below was pasted as a picture.")
    info = FLATTENED_TRUTH["chart"]
    ih = info["width"] * info["pixels"][1] / info["pixels"][0]
    buf = io.BytesIO()
    chart.save(buf, "PNG")
    buf.seek(0)
    c.drawImage(ImageReader(buf), info["x"], h - info["top"] - ih, width=info["width"], height=ih)
    c.setFont("LS", 12)
    c.drawString(60, h - info["top"] - ih - 30, "Source: internal sales data.")
    c.showPage()
    # 第 2 頁：照片
    c.setFillColor(HexColor("#1F3A5F"))
    c.setFont("LSB", 24)
    c.drawString(60, h - 80, "Field photo")
    photo = io.BytesIO()
    Image.open(_photo(900, 600, seed=5)).save(photo, "JPEG", quality=85)  # 照片多半是 JPEG
    photo.seek(0)
    c.drawImage(ImageReader(photo), 60, h - 120 - 316.7, width=475, height=316.7)
    c.showPage()
    # 第 3 頁：整頁掃描 + 原生紅字
    buf = io.BytesIO()
    scan.save(buf, "JPEG", quality=90)
    buf.seek(0)
    c.drawImage(ImageReader(buf), 0, 0, width=w, height=h)
    c.setFillColor(HexColor("#C8553D"))
    c.setFont("LSB", 18)
    c.drawString(360, h - 50, "APPROVED 2026-11-22")
    c.showPage()
    c.save()


def cv_two_column() -> None:
    """假資料的兩欄履歷（人名、經歷都是虛構的）：左邊灰底側欄（技能、軟體、語言）、右邊主欄（簡介、經歷、學歷），
    最上方姓名在左、聯絡方式靠右，經歷標題右邊有靠右的日期，內容一路排到接近紙張底部。
    用來測 Word 輸出在別的裝置上不跑版（tests/exporters/test_docx_two_column.py）。不需要 LibreOffice，輸出的位元組固定。"""
    from reportlab.lib.colors import HexColor
    from reportlab.pdfbase.pdfmetrics import stringWidth

    w, h = 595.276, 841.89
    c = _vector_canvas("cv_two_column.pdf", (w, h))
    ink, accent, grey = HexColor("#222222"), HexColor("#2F5D73"), HexColor("#555555")

    def text(x, top, s, size, font="LS", color=ink, align="left", space=0.0):
        c.setFillColor(color)
        t = c.beginText()
        t.setFont(font, size)
        t.setCharSpace(space)
        width = stringWidth(s, font, size) + space * (len(s) - 1)
        t.setTextOrigin(x - width if align == "right" else x, h - top)
        t.textOut(s)
        c.drawText(t)

    def rule(x0, x1, top, color=accent, width=0.8):
        c.setStrokeColor(color)
        c.setLineWidth(width)
        c.line(x0, h - top, x1, h - top)

    def wrap(s, font, size, width):
        lines, cur = [], ""
        for word in s.split():
            trial = (cur + " " + word).strip()
            if stringWidth(trial, font, size) <= width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        return lines + [cur]

    # 側欄底色（左緣貼齊紙邊、往下到紙張底部）與頁首分隔線
    c.setFillColor(HexColor("#F3F2EE"))
    c.rect(0, 0, 177, h - 122, stroke=0, fill=1)
    rule(0, w, 121, HexColor("#DDDDDD"), 0.6)
    # 頁首：姓名在左、聯絡方式靠右
    text(34, 58, "RILEY QUINN", 26, "LSB")
    text(34, 80, "Riley Q.", 12, color=accent)
    text(34, 99, "Data analysis · Urban research · Web & SEO", 9.5, color=grey)
    for top, s in ((68, "riley.quinn@example.com"), (81, "+1 555 0100"), (94, "Springfield, Exampleland")):
        text(562, top, s, 9, color=accent if "@" in s else grey, align="right")
    # 側欄
    y = 150
    for head, groups in (("SKILLS", [("Research & analysis", ["Site surveys", "Market research", "Database building"]),
                                      ("Digital", ["Website design", "SEO and GEO", "AI-assisted coding"])]),
                         ("SOFTWARE", [(None, ["Notion", "QGIS", "AutoCAD", "Rhino", "Office suite"])]),
                         ("LANGUAGES", [(None, ["English (native)", "Spanish (B2)"])])):
        text(33, y, head, 8.5, "LSB", accent, space=1.6)
        rule(33, 160, y + 7)
        y += 26
        for sub, items in groups:
            if sub:
                text(33, y, sub, 8.5, "LSB")
                y += 14
            for it in items:
                text(33, y, it, 8.5, color=grey)
                y += 14
            y += 6
        y += 14
    # 主欄
    x0, x1 = 198.0, 580.0

    def heading(top, s):
        text(x0, top, s, 8.5, "LSB", accent, space=1.6)
        rule(x0, x1, top + 7)

    def bullets(top, items, size=9.0):
        for it in items:
            for k, line in enumerate(wrap(it, "LS", size, x1 - x0 - 14)):
                if k == 0:
                    text(x0 + 3, top, "•", size, color=grey)
                text(x0 + 12, top, line, size, color=grey)
                top += 12.5
            top += 2
        return top

    heading(150, "PROFILE")
    top = 176
    for line in wrap("Final-year urban planning student with field research experience and two small client websites built "
                     "from scratch, including search and generative engine optimisation for a local bakery.", "LS", 9, x1 - x0):
        text(x0, top, line, 9, color=grey)
        top += 12.5
    heading(top + 12, "EXPERIENCE")
    top += 38
    jobs = [("NORTHWIND LTD — junior data analyst, Springfield", "2025 – present",
             ["Cleaned and joined three years of footfall counts so the team could compare weekday and weekend demand by street.",
              "Built a small dashboard that shows vacancy rates per block and updates every Monday from a shared sheet.",
              "Wrote a short guide on naming fields consistently, now used by four new colleagues.",
              "Presented the findings to the client in a twenty-minute review."]),
            ("Riverside Studio — freelance web projects", "Jan 2024 · 3 months",
             ["Designed and built a website for a neighbourhood bakery, including an online pre-order form.",
              "Rewrote page titles and descriptions around the words real customers search for; visits doubled in two months."]),
            ("Volunteer mapping project", "2023",
             ["Surveyed street trees with a phone app and checked every record against aerial photos before upload.",
              "Trained six volunteers to use the same checklist so the data stayed consistent."])]
    for title, date, items in jobs:
        text(x0, top, title, 9.5, "LSB")
        text(x1, top, date, 8.5, color=grey, align="right")
        top = bullets(top + 16, items) + 8
    heading(top + 6, "EDUCATION")
    top += 32
    text(x0, top, "BSc Urban Planning — Example University", 9.5, "LSB")
    text(x1, top, "2021 – 2025", 8.5, color=grey, align="right")
    bullets(top + 16, ["Dissertation on how walking distance to a station changes shop rents along a high street.",
                       "Exchange term at a partner university abroad (urban design studio)."])
    c.showPage()
    c.save()


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["cv"]:
        cv_two_column()
        print(["cv_two_column.pdf"])
        sys.exit(0)
    if sys.argv[1:] == ["flattened"]:
        flattened_outlined()
        flattened_embedded()
        print(sorted(p.name for p in HERE.glob("flattened_*.pdf")))
        sys.exit(0)
    if sys.argv[1:] != ["vectors"]:
        slide_dark()
        doc_a4()
        poster()
        deck()
    vector_logo()
    vector_paint()
    vector_outlined()
    if sys.argv[1:] != ["vectors"]:
        flattened_outlined()
        flattened_embedded()
        cv_two_column()
    print(sorted(p.name for p in HERE.glob("*.pdf")))

# 查證紀錄：PDF 解析、渲染與寫出的函式庫

- 查證日期：2026-11-20
- 適用產品：Mortise（榫）文件反編譯 SaaS，未來可能有桌面版
- 授權底線：所有元件必須可商用；排除 GPL、AGPL 元件（另有可購買商業授權者列為「需付費」）；排除非商用授權的模型權重
- 需求：從原生 PDF 讀出每個字形的位置、字型名稱、字級、顏色、變換矩陣；讀出向量路徑、內嵌圖片與遮罩、頁框（MediaBox、CropBox、TrimBox）、旋轉、圖層、文字渲染模式（偵測隱藏的 OCR 文字層）、是否有 ToUnicode 對照表、Producer 中繼資料；把頁面渲染成影像；寫出文字可編輯的 PDF

## 0. 查證方式與限制

本次查證的工具狀況，會影響下面每一條結論的可信度，先講清楚。

| 來源 | 狀況 |
|---|---|
| 網路搜尋 | 可用 |
| github.com、raw.githubusercontent.com | 可直接讀取（LICENSE、README、標頭檔、原始碼、文件原檔） |
| pypi.org、files.pythonhosted.org | 可直接讀取（版本、上傳日期、授權欄位、原始碼包） |
| registry.npmjs.org、crates.io | 可直接讀取（版本、授權欄位） |
| pdfium.googlesource.com | **無法讀取**（被工作環境的網路規則擋下）。改讀 GitHub 上 chromium 組織的 PDFium 鏡像 |
| artifex.com、ghostscript.com、mupdf.readthedocs.io、pymupdf.readthedocs.io | **無法讀取**。改讀 GitHub 上的 README、COPYING 與文件原檔；Artifex 授權頁只有搜尋摘要 |
| poppler.freedesktop.org、gitlab.freedesktop.org、sources.debian.org | **無法讀取**。Poppler 的授權只能靠打包者（Homebrew、conda-forge）的中繼資料與搜尋摘要 |
| docs.reportlab.com、www.reportlab.com | **無法讀取**。改讀 PyPI 上 reportlab 5.1.0 原始碼包裡的 LICENSE、原始碼與使用手冊原檔 |
| docs.rs、mozilla.github.io、pypdfium2.readthedocs.io、qpdf.readthedocs.io | **無法讀取**。改讀 GitHub 原始碼 |
| api.github.com | 回傳 403，沒有用到 |

下文每條事實後面標註查證方式：

- **【直讀】** 直接讀到官方來源的原文（LICENSE、官方標頭檔、官方文件原檔、原始碼、套件登錄站的中繼資料）
- **【實測】** 在暫存環境實際安裝套件、跑過程式得到的結果（見 3.13）
- **【搜尋摘要】** 搜尋工具轉述了頁面內容，但我沒有親自打開該頁
- **【第三方】** 只有非官方來源

實測的範圍很小：只用自己產生的三個合成 PDF（拉丁字母加一行繁體中文），不是真實世界的檔案集。它能證明「這個介面叫得到、回傳值合理」，不能證明「遇到各種怪 PDF 都正確」。沒有做任何效能測試。

## 1. 總表

「可否商用」指用在收費的線上服務；「可否隨桌面版散布」指把程式打包進安裝檔交給使用者。這個主題的元件都不是機器學習模型，「權重授權」一欄除特別註明外皆為不適用。

### 1.1 解析與渲染：授權可用

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **PDFium** | 解析、文字抽取、渲染、有限度的寫出（C/C++，Chrome 的 PDF 引擎） | BSD-3-Clause 加 Apache-2.0（LICENSE 檔同時含兩份授權文） | 不適用 | 可 | 可，需附 PDFium 與其相依套件的授權文 | 逐字：Unicode、緊框與鬆框、原點、字級、字型名稱與旗標、字重、填色、描邊色、角度、有效變換矩陣、是否為引擎補出的字、Unicode 對照是否有誤。文字物件的渲染模式（含 3 = 不可見）。路徑線段、填色規則、描邊、線寬、虛線、裁切路徑。圖片原始位元組、解碼後位元組、濾鏡、中繼資料、套用遮罩後的點陣圖。五種頁框、旋轉。Info 字典八個欄位（含 Producer）。內容標記（可讀到物件所屬圖層的名稱）。頁面渲染成點陣圖 | 公開介面**沒有**列舉圖層、讀寫圖層開關的函式；沒有專門取軟遮罩的函式；不提供 PDF 字典與串流的原始存取；許多需要的函式標為 Experimental API；不是執行緒安全；寫出時不做字型子集化，也沒有字距設定 | 是（標頭檔直讀加實測） | [LICENSE](https://github.com/chromium/pdfium/blob/main/LICENSE)、[fpdf_text.h](https://github.com/chromium/pdfium/blob/main/public/fpdf_text.h)、[fpdf_edit.h](https://github.com/chromium/pdfium/blob/main/public/fpdf_edit.h)、[fpdfview.h](https://github.com/chromium/pdfium/blob/main/public/fpdfview.h)、[fpdf_transformpage.h](https://github.com/chromium/pdfium/blob/main/public/fpdf_transformpage.h)、[fpdf_doc.h](https://github.com/chromium/pdfium/blob/main/public/fpdf_doc.h) |
| **pypdfium2 5.15.2** | PDFium 的 Python 綁定（ctypes），輪檔內含 PDFium 二進位檔 | 專案自述 Apache-2.0 / BSD-3-Clause；PyPI 欄位寫「BSD-3-Clause, Apache-2.0, dependency licenses」 | 不適用 | 可 | 可，輪檔已附 PDFium 與相依套件授權文 | 高階輔助介面涵蓋渲染、文字範圍、字框、頁框、旋轉、中繼資料、頁面物件、圖片抽取；`pypdfium2.raw` 可直接呼叫 PDFium 全部 C 函式 | 逐字的字型、顏色、矩陣、渲染模式不在高階介面，要走 raw 介面；專案自述有物件生命週期風險與 ctypes 的技術限制；部分建置會連結 libgcc | 是（直讀加實測） | [PyPI](https://pypi.org/project/pypdfium2/)、[README](https://github.com/pypdfium2-team/pypdfium2/blob/main/README.md)、[textpage.py](https://github.com/pypdfium2-team/pypdfium2/blob/main/src/pypdfium2/_helpers/textpage.py) |
| **pikepdf 10.18.2 與 qpdf 12.4.2** | PDF 物件層的讀、寫、修復、轉換 | pikepdf：MPL-2.0；qpdf：Apache-2.0 | 不適用 | 可 | 可；MPL-2.0 是檔案層級的著佐權，若修改 pikepdf 自己的原始檔，須公開那些檔案的修改 | 直接讀寫任何字典與串流：頁框、旋轉、Info 與 XMP、`/OCProperties`（圖層清單與預設開關）、字型字典（有無 `/ToUnicode`、有無內嵌字型檔）、圖片的 `/SMask`；把內容串流拆成運算元；修復壞檔；圖片抽取（可把遮罩合成進透明通道） | 官方明講：不做文字抽取、不做渲染；內容串流只拆成記號，不維護繪圖狀態（座標矩陣要另用 `pikepdf.ctm` 或自己算） | 是（直讀加實測） | [pikepdf README](https://github.com/pikepdf/pikepdf/blob/main/README.md)、[第三方元件清單](https://github.com/pikepdf/pikepdf/blob/main/third-party-licenses/README.md)、[content_streams.md](https://github.com/pikepdf/pikepdf/blob/main/docs/topics/content_streams.md)、[images.md](https://github.com/pikepdf/pikepdf/blob/main/docs/topics/images.md)、[qpdf README](https://github.com/qpdf/qpdf/blob/main/README.md) |
| **pdfminer.six 20260107** | 純 Python 的文字與版面抽取 | MIT | 不適用 | 可 | 可 | 每個字（LTChar）有外框、矩陣、字型名稱、填色與描邊色、前進寬度；有線、矩形、曲線、圖片物件；頁面有 mediabox、cropbox、rotate | LTChar **沒有**文字渲染模式（直譯器內部有記錄，但沒有傳給 LTChar）；`size` 是外框高度不是字級，旋轉文字會失真（實測 10pt 回報 12.0）；不能渲染；沒有圖層；純 Python 較慢 | 是（直讀加實測） | [LICENSE](https://github.com/pdfminer/pdfminer.six/blob/master/LICENSE)、[layout.py](https://github.com/pdfminer/pdfminer.six/blob/master/pdfminer/layout.py)、[pdfinterp.py](https://github.com/pdfminer/pdfminer.six/blob/master/pdfminer/pdfinterp.py)、[converter.py](https://github.com/pdfminer/pdfminer.six/blob/master/pdfminer/converter.py) |
| **pdfplumber 0.11.10** | 建在 pdfminer.six 上的抽取與除錯工具 | MIT | 不適用 | 可 | 可 | 字元屬性：字型名稱、size、矩陣、填色、描邊色、直立與否、標記內容編號；線、矩形、曲線、圖片；表格偵測；頁面影像由 pypdfium2 渲染 | 繼承 pdfminer.six 的限制（沒有渲染模式、size 的定義）；相依版本鎖死 `pdfminer.six==20260107` | 是（直讀） | [README](https://github.com/jsvine/pdfplumber/blob/stable/README.md)、[LICENSE](https://github.com/jsvine/pdfplumber/blob/stable/LICENSE.txt)、[PyPI](https://pypi.org/project/pdfplumber/) |
| **pypdf 7.1.1** | 純 Python 的 PDF 拆併與基本抽取 | BSD-3-Clause | 不適用 | 可 | 可 | 五種頁框、旋轉、圖片清單；文字抽取可掛 visitor，每次文字操作回報文字、目前矩陣、文字矩陣、字型字典、字級 | 官方文件自己說複雜 PDF 的 visitor 座標可能是錯的；回報單位是文字操作不是逐字；README 列出的功能範圍裡沒有渲染，也沒有繪製文字 | 是（直讀） | [LICENSE](https://github.com/py-pdf/pypdf/blob/main/LICENSE)、[README](https://github.com/py-pdf/pypdf/blob/main/README.md)、[extract-text.md](https://github.com/py-pdf/pypdf/blob/main/docs/user/extract-text.md)、[_page.py](https://github.com/py-pdf/pypdf/blob/main/pypdf/_page.py) |
| **pdf.js（pdfjs-dist 6.5.438）** | 瀏覽器與 Node 的 PDF 檢視引擎 | Apache-2.0 | 不適用 | 可 | 可 | 渲染到 canvas；`getTextContent` 回傳文字段落（字串、變換矩陣、寬高、字型代號、方向）；`getOperatorList` 回傳底層繪圖指令；`getOptionalContentConfig` 讀圖層設定；`getMetadata` | 文字是一段一段，不是逐字；文字項目沒有顏色、沒有渲染模式（渲染模式只出現在運算元清單）；`fontName` 是 pdf.js 轉換後的代號 | 是（直讀） | [LICENSE](https://github.com/mozilla/pdf.js/blob/master/LICENSE)、[api.js](https://github.com/mozilla/pdf.js/blob/master/src/display/api.js)、[evaluator.js](https://github.com/mozilla/pdf.js/blob/master/src/core/evaluator.js)、[npm](https://www.npmjs.com/package/pdfjs-dist) |
| **PLAYA-PDF 1.1.0**（清單外，順帶查到） | pdfminer 的改寫版，純 Python | MIT | 不適用 | 可 | 可 | 逐字形物件，圖形狀態裡有 `render_mode`（README 明講可用 `render_mode == 3` 略過不可見字）；標記內容區段；路徑線段；可多行程平行 | 純 Python；不渲染；我只讀了 README，沒有實測 | 部分（只直讀 README 與 LICENSE） | [README](https://github.com/dhdaines/playa/blob/main/README.md)、[PyPI](https://pypi.org/project/playa-pdf/) |
| **pdftext 0.7.1**（清單外，順帶查到） | 建在 pypdfium2 上的文字區塊抽取 | Apache-2.0 | 不適用 | 可 | 可 | 輸出區塊、行、字與字型資訊；README 自述定位是「像 PyMuPDF 但沒有 AGPL」 | 鎖死 `pypdfium2==5.10.1`；沒有查渲染模式是否輸出 | 部分（只直讀 README 與 PyPI） | [README](https://github.com/datalab-to/pdftext/blob/master/README.md)、[PyPI](https://pypi.org/project/pdftext/) |

### 1.2 寫出文字可編輯的 PDF：授權可用

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **ReportLab 5.1.0（開源版）** | 用 Python 產生 PDF | BSD-3-Clause 條款（LICENSE 檔原文；PyPI 分類為 BSD License） | 不適用 | 可 | 可 | 內嵌 TrueType 字型並自動做子集、產生 ToUnicode；文字物件可設字型、字級、文字矩陣、字距、詞距、水平縮放、上升量、渲染模式、填色；可選裝 uharfbuzz 做字形排版；實測寫出繁體中文後 PDFium 與 pdfminer.six 都能正確讀回 | `TTFont` 不支援 PostScript 外框的 OpenType（CFF）字型，原始碼直接拋錯；子集是 8 位元的簡單字型，一個子集最多 256 個字形，中文多的頁面會被拆成多個子集字型；從 TTC 載入時 BaseFont 會被加上「-序號」字尾；不能讀既有 PDF | 是（直讀原始碼包加實測） | [PyPI](https://pypi.org/project/reportlab/)（原始碼包內的 `LICENSE`、`src/reportlab/pdfbase/ttfonts.py`、`src/reportlab/pdfgen/textobject.py`、`docs/userguide/ch2a_fonts.py`） |
| **krilla 0.8.2**（Rust） | 高階 PDF 產生函式庫 | MIT 或 Apache-2.0 | 不適用 | 可 | 可 | 高階文字介面與低階「定位字形序列」介面；CFF 與 TrueType 兩種字型都做子集；路徑、裁切、遮罩、漸層、圖片；Tagged PDF；PDF 1.4 到 2.0；PDF/A 驗證模式 | 自述不做文字排版、分頁、表格；加密與數位簽章不在範圍內；我沒有實測 | 部分（只直讀 README 與 crates.io） | [README](https://github.com/LaurenzV/krilla/blob/main/README.md)、[crates.io](https://crates.io/crates/krilla) |
| **pdf-writer 0.15.0**（Rust） | 低階逐物件 PDF 寫出器（krilla 的底層） | MIT 或 Apache-2.0 | 不適用 | 可 | 可 | 強型別地寫出每個 PDF 物件與內容串流 | 字型子集、ToUnicode 等都要自己做 | 部分（只直讀 README 與 crates.io） | [README](https://github.com/typst/pdf-writer/blob/main/README.md)、[crates.io](https://crates.io/crates/pdf-writer) |
| **PDFium 的編輯介面** | 用同一顆引擎寫出 | 同 PDFium | 不適用 | 可 | 可 | 建立文字物件、載入字型（自動產生 ToUnicode，或由呼叫端提供 ToUnicode 與 CIDToGIDMap）、設定文字、設定渲染模式、產生內容、另存 | 實測：載入 16.8 MB 的中文字型寫一行字，輸出檔 9.5 MB，**整個字型原封內嵌，不做子集**；公開標頭檔沒有字距、詞距的設定函式 | 是（直讀加實測） | [fpdf_edit.h](https://github.com/chromium/pdfium/blob/main/public/fpdf_edit.h)、[fpdf_save.h](https://github.com/chromium/pdfium/blob/main/public/fpdf_save.h) |

### 1.3 排除，或需付費才能用

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **PyMuPDF 2.1 與 MuPDF 1.28.6** | 解析、渲染、編輯的全功能引擎 | AGPL-3.0，或向 Artifex 購買商業授權（雙授權） | 2.x 起主套件內含版面分析模型（相依 onnxruntime）；模型權重的授權沒有另外查到 | AGPL 版：**排除**。商業授權：可，**需付費** | AGPL 版：**排除**。商業授權：需付費，條款未查到 | 本次查到功能最完整的一個：`get_texttrace` 逐字給 Unicode、字形編號、原點、外框，並給字型、字級、顏色、不透明度、span 類型（0 填色、1 描邊、3 隱藏文字）、所屬圖層名稱、繪製順序號（可判斷文字是否被後畫的東西蓋住）；`get_drawings` 給向量路徑與圖層；圖片資訊含有無遮罩；頁框、旋轉、圖層設定、中繼資料；渲染；插入文字、字型子集 | 授權不符底線；價格未公開，需向 Artifex 詢價 | 是（授權與能力皆直讀；價格查不到） | [PyPI](https://pypi.org/project/PyMuPDF/)、[README](https://github.com/pymupdf/PyMuPDF/blob/main/README.md)、[COPYING](https://github.com/pymupdf/PyMuPDF/blob/main/COPYING)、[functions.rst](https://github.com/pymupdf/PyMuPDF/blob/main/docs/functions.rst)、[textpage.rst](https://github.com/pymupdf/PyMuPDF/blob/main/docs/textpage.rst)、[page.rst](https://github.com/pymupdf/PyMuPDF/blob/main/docs/page.rst)、[MuPDF license.md](https://github.com/ArtifexSoftware/mupdf/blob/master/docs/license.md) |
| **Ghostscript 10.08.0** | PostScript 與 PDF 直譯、渲染、轉檔 | AGPL-3.0-or-later，或向 Artifex 購買商業授權 | 不適用 | AGPL 版：**排除**。商業授權：需付費 | 同左 | 本次沒有逐項查能力 | 授權不符底線；價格未公開 | 是（只查授權） | [LICENSE](https://github.com/ArtifexSoftware/ghostpdl/blob/master/LICENSE)、[Readme.rst](https://github.com/ArtifexSoftware/ghostpdl/blob/master/doc/src/Readme.rst) |
| **Poppler 26.11.0** | 解析與渲染（多數 Linux 桌面的 PDF 引擎） | GPL。確切版本標示來源不一致：Homebrew 寫「GPL-2.0-only 或 GPL-3.0-only」，conda-forge 寫「GPL-2.0-or-later」 | 不適用 | **排除** | **排除** | 本次沒有逐項查能力 | 授權不符底線；沒有查到可購買的商業授權 | 部分（官方來源讀不到，只有打包者中繼資料與搜尋摘要） | [Homebrew formula](https://github.com/Homebrew/homebrew-core/blob/master/Formula/p/poppler.rb)、[conda-forge recipe](https://github.com/conda-forge/poppler-feedstock/blob/main/recipe/meta.yaml) |
| **borb 3.0.9**（清單外） | Python PDF 讀寫 | AGPL-3.0-or-later | 不適用 | **排除** | **排除** | 未查 | 授權不符底線 | 是（只查授權） | [PyPI](https://pypi.org/project/borb/) |
| **fpdf2 2.9.0**（清單外） | Python PDF 產生 | LGPL-3.0-only | 不適用 | 可 | **要法務判斷**：不是 GPL 或 AGPL，不在排除條款內，但 LGPL 對散布有「使用者可替換函式庫」等義務 | 未查 | 列在這裡只為提醒：OCRmyPDF 17.14.2 把 `fpdf2>=2.9.0` 列為必要相依 | 是（只查授權） | [PyPI](https://pypi.org/project/fpdf2/)、[OCRmyPDF 的 PyPI](https://pypi.org/project/ocrmypdf/) |

### 1.4 Rust 生態

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **pdfium-render 0.9.5** | PDFium 的 Rust 高階包裝 | MIT 或 Apache-2.0（PDFium 本體另計） | 不適用 | 可 | 可 | 逐字：Unicode、字級、字型名稱與旗標、字重、渲染模式、填色、描邊色、角度、緊框與鬆框、原點、是否為補出字、Unicode 對照錯誤；頁面物件、渲染、建立文字與路徑物件；可動態或靜態連結 PDFium，可編成 WASM | 自己不編 PDFium，要另備二進位檔；繼承 PDFium 的所有限制（含非執行緒安全） | 是（直讀 README 與原始碼） | [README](https://github.com/ajrcarey/pdfium-render/blob/master/README.md)、[char.rs](https://github.com/ajrcarey/pdfium-render/blob/master/src/pdf/document/page/text/char.rs)、[crates.io](https://crates.io/crates/pdfium-render) |
| **hayro 0.9.0 系列**（hayro-syntax、hayro-interpret、hayro、hayro-svg、hayro-write） | 純 Rust 的 PDF 解析、直譯、渲染 | Apache-2.0 或 MIT | 不適用 | 可 | 可 | 唯讀解析；直譯器把繪圖指令送進自訂的 `Device`（畫路徑、裁切、透明群組與軟遮罩、定位字形序列、圖片、標記內容起訖）；渲染成點陣圖或 SVG；作者自述正確性由 1,600 多份 PDF 的視覺回歸測試支撐 | hayro-interpret 自述「仍在開發、幾乎沒有文件、目前介面只真正適合渲染成 PNG 或 SVG」；穩健性與效能作者自述仍在補；沒有實測 | 部分（只直讀 README 與 `device.rs`） | [README](https://github.com/LaurenzV/hayro/blob/main/README.md)、[hayro-interpret README](https://github.com/LaurenzV/hayro/blob/main/hayro-interpret/README.md)、[device.rs](https://github.com/LaurenzV/hayro/blob/main/hayro-interpret/src/device.rs) |
| **lopdf 0.47.0** | PDF 物件層的讀寫 | MIT | 不適用 | 可 | 可 | 物件與串流操作、物件串流、基本 `extract_text` | 低階；不渲染；沒有逐字版面資訊 | 部分（只直讀 README） | [README](https://github.com/J-F-Liu/lopdf/blob/main/README.md)、[crates.io](https://crates.io/crates/lopdf) |
| **pdf（pdf-rs）0.10.0** | PDF 讀取 | MIT | 不適用 | 可 | 可 | 讀取、抽圖與內嵌字型 | README 自述修改與寫出仍是實驗性 | 部分 | [README](https://github.com/pdf-rs/pdf/blob/master/README.md) |
| **pdf-extract 0.12.1** | 文字抽取 | MIT | 不適用 | 可 | 可 | 抽純文字 | 只查到純文字介面 | 部分 | [README](https://github.com/jrmuizel/pdf-extract/blob/master/README.md) |
| **printpdf 0.12.13** | 產生與讀取 PDF | MIT | 不適用 | 可 | 可 | 寫：圖層、圖形、內嵌字型並自動子集；讀：圖層與圖形 | README 自述讀取圖片與內嵌字型是實驗性 | 部分 | [README](https://github.com/fschutt/printpdf/blob/master/README.md) |
| **pdf_oxide 0.3.78** | 文字抽取、轉 Markdown、建立與編輯 | MIT 或 Apache-2.0 | 不適用 | 可 | 可 | README 列出逐字（`page.chars`）、圖片抽取、多語言綁定 | 速度與通過率全是作者自己公布的數字；沒有實測；專案名稱與商標另有聲明 | 部分（只直讀 README；效能主張未驗證） | [README](https://github.com/yfedoseev/pdf_oxide/blob/main/README.md) |
| **oxidize-pdf 5.6.1** | 產生、解析、抽取 | MIT | 不適用 | 可 | 可 | 未逐項查 | 只查到授權 | 部分 | [crates.io](https://crates.io/crates/oxidize-pdf) |
| **mupdf（mupdf-rs）0.8.0** | MuPDF 的 Rust 綁定 | AGPL-3.0 | 不適用 | **排除** | **排除** | 同 MuPDF | 授權不符底線 | 是（只查授權） | [README](https://github.com/messense/mupdf-rs/blob/main/README.md)、[crates.io](https://crates.io/crates/mupdf) |
| **pdfium 0.10.4**（newinnovations/pdfium-rs） | 另一個 PDFium 的 Rust 介面 | **GPL-3.0** | 不適用 | **排除** | **排除** | 未查 | 名字容易跟 pdfium-render 搞混，授權完全不同 | 是（只查授權） | [crates.io](https://crates.io/crates/pdfium) |
| **poppler-rs 0.26.0** | Poppler 的 Rust 綁定 | 綁定本身 MIT，但連結的 Poppler 是 GPL | 不適用 | **排除** | **排除** | 未查 | 綁定的授權寬鬆不代表整體可用 | 是（只查授權） | [crates.io](https://crates.io/crates/poppler-rs) |

## 2. 需求對照

「有」代表官方標頭檔或文件寫明、或實測叫得到。「付費」一欄是 PyMuPDF，僅供對照。

| 需求 | PDFium（經 pypdfium2 的 raw 介面） | pikepdf | pdfminer.six | pdf.js | PyMuPDF（付費） |
|---|---|---|---|---|---|
| 逐字位置 | 有：緊框、鬆框、原點（實測） | 無 | 有：外框（實測） | 無，只有文字段落 | 有 |
| 字型名稱 | 有（實測） | 可讀字型字典 | 有（實測） | 只有轉換後代號與推測的字族 | 有 |
| 字級 | 有（實測，旋轉文字也正確） | 無 | `size` 是外框高度，旋轉時失真（實測） | 要從矩陣推算 | 有 |
| 顏色 | 有：填色與描邊色（實測） | 無（要自己追繪圖狀態） | 有：填色與描邊色（實測） | 文字項目沒有 | 有 |
| 變換矩陣 | 有（實測） | `pikepdf.ctm` 可算目前矩陣 | 有（實測） | 有（段落層級） | 有（方向向量與外框） |
| 向量路徑 | 有：線段、填色規則、描邊、線寬、虛線、裁切路徑（實測前四項） | 只有原始運算元 | 有：線、矩形、曲線 | 運算元清單 | 有，含圖層與繪製順序 |
| 內嵌圖片 | 有：原始與解碼位元組、濾鏡、中繼資料（實測） | 有 | 有 | 渲染用 | 有 |
| 圖片遮罩 | 只能拿到「套用遮罩後」的點陣圖（實測為四通道）；拿不到遮罩本身 | 有：直接讀 `/SMask`（實測） | 未查 | 未查 | 有（`has-mask`） |
| MediaBox、CropBox、TrimBox | 有（實測） | 有（實測） | 有 mediabox、cropbox；其餘要讀原始屬性 | `view` 與視埠 | 有 |
| 旋轉 | 有 | 有 | 有 | 有 | 有 |
| 圖層 | 公開介面無列舉函式；但內容標記讀得到物件所屬圖層名稱（實測）；渲染會遵守預設關閉（實測） | 有：`/OCProperties` 全部內容（實測） | 無 | 有：`getOptionalContentConfig` | 有 |
| 文字渲染模式 | 有（實測讀到 INVISIBLE） | 內容串流裡的 `Tr` 運算元（實測） | LTChar 上沒有（實測） | 只在運算元清單 | 有（span 類型 3） |
| 有無 ToUnicode | 逐字回報「Unicode 對照有誤」（實測：拿掉 ToUnicode 後回報 1） | 有：直接看字型字典有無 `/ToUnicode`（實測） | 沒有對照時輸出 `(cid:N)`（實測） | 未查 | 回傳 U+FFFD 並保留字形編號（文件所述） |
| Producer | 有（實測） | 有（實測） | 有 | 有 | 有 |
| 渲染成影像 | 有（實測） | 無 | 無 | 有 | 有 |
| 寫出可編輯文字 | 有，但不做子集（實測） | 只能手寫內容串流 | 無 | 無 | 有 |

結論：沒有任何一個授權可用的函式庫能單獨滿足全部需求。PDFium 加 pikepdf 兩個合起來，表上每一列都有著落。

## 3. 逐條事實與來源

### 3.1 PDFium 與 pypdfium2

1. PDFium 的 LICENSE 檔同時含一份 BSD 三條款授權文（版權行為「Copyright 2014 The PDFium Authors」）與一份完整的 Apache License 2.0。【直讀】[LICENSE](https://github.com/chromium/pdfium/blob/main/LICENSE)。註：讀的是 GitHub 上 chromium 組織的鏡像，官方主站 pdfium.googlesource.com 連不上。
2. `fpdf_text.h` 宣告的逐字函式：`FPDFText_GetUnicode`、`GetCharBox`、`GetCharOrigin`、`GetFontSize`（以上非實驗性）；`GetLooseCharBox`、`GetFontInfo`（UTF-8 字型名稱加字型描述旗標）、`GetFontWeight`、`GetFillColor`、`GetStrokeColor`、`GetCharAngle`、`GetMatrix`、`GetTextObject`、`IsGenerated`、`IsHyphen`、`HasUnicodeMapError`（以上標為 Experimental API）。【直讀】[fpdf_text.h](https://github.com/chromium/pdfium/blob/main/public/fpdf_text.h)
3. `fpdfview.h` 定義文字渲染模式列舉：FILL 0、STROKE 1、FILL_STROKE 2、INVISIBLE 3、FILL_CLIP 4、STROKE_CLIP 5、FILL_STROKE_CLIP 6、CLIP 7。`fpdf_edit.h` 的 `FPDFTextObj_GetTextRenderMode` 回傳這個值（非實驗性）；`SetTextRenderMode` 是實驗性。【直讀】[fpdfview.h](https://github.com/chromium/pdfium/blob/main/public/fpdfview.h)、[fpdf_edit.h](https://github.com/chromium/pdfium/blob/main/public/fpdf_edit.h)
4. 頁面物件分 TEXT、PATH、IMAGE、SHADING、FORM 五類；`FPDFFormObj_CountObjects` 與 `GetObject` 可走進 Form XObject。路徑可讀線段（點、類型、是否閉合）、繪製模式（填色規則與是否描邊）、填色、描邊色、線寬、線端、接合、虛線。【直讀】[fpdf_edit.h](https://github.com/chromium/pdfium/blob/main/public/fpdf_edit.h)
5. 圖片：`FPDFImageObj_GetBitmap` 的註解明講「不考慮關聯的圖片遮罩，也忽略矩陣」；`GetRenderedBitmap`（實驗性）會套用遮罩與矩陣；另有 `GetImageDataRaw`、`GetImageDataDecoded`、`GetImageFilterCount`、`GetImageFilter`、`GetImageMetadata`、`GetImagePixelSize`、`GetIccProfileDataDecoded`。標頭檔裡沒有專門取軟遮罩的函式。【直讀】同上
6. 字型：`FPDFFont_GetBaseFontName`、`GetFamilyName`、`GetFontData`、`GetIsEmbedded`、`GetFlags`、`GetWeight`、`GetItalicAngle`、`GetAscent`、`GetDescent`、`GetGlyphWidth`、`GetGlyphPath`，全部標為實驗性。`GetFontData` 的註解：字型沒有內嵌時，回傳的是替代字型的資料。【直讀】同上
7. 頁框：`fpdf_transformpage.h` 有 MediaBox、CropBox、BleedBox、TrimBox、ArtBox 的讀與寫；另有 `FPDFPageObj_GetClipPath` 與 `FPDFClipPath_*`（實驗性）。旋轉：`FPDFPage_GetRotation` 回傳 0 到 3，代表順時針 0、90、180、270 度。【直讀】[fpdf_transformpage.h](https://github.com/chromium/pdfium/blob/main/public/fpdf_transformpage.h)
8. 中繼資料：`FPDF_GetMetaText` 可取 Title、Author、Subject、Keywords、Creator、Producer、CreationDate、ModDate。【直讀】[fpdf_doc.h](https://github.com/chromium/pdfium/blob/main/public/fpdf_doc.h)
9. 圖層：我把 `public/` 下 21 個標頭檔全部搜過「optional content」「OCG」「OCProperties」，只在 `fpdf_ext.h` 的一行註解（頁面模式常數）出現，沒有任何列舉或切換圖層的函式。【直讀】。2026 年 8 到 9 月 PDFium 郵件論壇有一串討論，貢獻者回覆說公開介面缺 OCG 支援。【搜尋摘要】[討論串](https://groups.google.com/g/pdfium/c/uDsh71Yviks)
10. 但內容標記介面（`FPDFPageObj_CountMarks`、`GetMark`、`FPDFPageObjMark_GetName`、`GetParamKey`、`GetParamStringValue`，皆實驗性）讀得到圖層資訊：實測對一個包在 `/OC /MC0 BDC … EMC` 裡的矩形，回傳標記名稱 `OC`、參數 `Name = LayerA`。【實測】
11. `FPDFPageObj_GetIsActive` 不是圖層可見性：實測把圖層設為預設關閉後，渲染結果該處變白（渲染器有遵守），但該物件仍被列舉、`GetIsActive` 仍回傳 1。【實測】
12. 寫出：`FPDFText_LoadFont`（註解：ToUnicode 等資料結構依輸入自動產生）、`LoadStandardFont`、`LoadCidType2Font`（ToUnicode 與 CIDToGIDMap 由呼叫端提供）、`FPDFPageObj_CreateTextObj`、`FPDFText_SetText`（UTF-16LE）、`SetCharcodes`、`FPDFPage_InsertObject`、`FPDFPage_GenerateContent`（註解：存檔前一定要呼叫，否則變更遺失）、`FPDF_SaveAsCopy`。`fpdf_edit.h` 與 `fpdf_text.h` 搜不到字距、詞距、水平縮放的設定函式。【直讀】[fpdf_edit.h](https://github.com/chromium/pdfium/blob/main/public/fpdf_edit.h)、[fpdf_save.h](https://github.com/chromium/pdfium/blob/main/public/fpdf_save.h)
13. pypdfium2 最新版 5.15.2，2026-11-09 上傳，需要 Python 3.6 以上；PyPI 授權欄位「BSD-3-Clause, Apache-2.0, dependency licenses」。【直讀】[PyPI](https://pypi.org/project/pypdfium2/)
14. pypdfium2 的 README：本身依 Apache-2.0 / BSD-3-Clause 提供；文件與範例是 CC-BY-4.0；PDFium 與其相依套件的授權文必須隨二進位散布；確切授權狀況取決於建置設定，部分建置會連結 libgcc；作者自述這是少數能渲染 PDF 又不受強著佐權授權約束的 Python 函式庫。README 沒有寫明兩個授權之間是「擇一」還是「並用」。【直讀】[README](https://github.com/pypdfium2-team/pypdfium2/blob/main/README.md)
15. pypdfium2 的 README 列出的已知限制：PDFium 不是執行緒安全；Python 可能提早回收 PDFium 還需要的物件；PDFium 公開介面不開放 PDF 字典、串流、名稱樹的原始存取，能力不如 pikepdf；ctypes 綁定有技術缺點。【直讀】同上
16. pypdfium2 高階介面的範圍：`PdfTextPage` 有 `get_text_bounded`、`get_text_range`、`count_chars`、`get_charbox`、`get_rect`、`get_textobj`、`search`；`PdfPage` 有五種頁框的讀寫、`get_rotation`、`get_objects`、`render`；`PdfDocument` 有 `get_metadata_dict`；`PdfImage` 有 `get_bitmap(render=…)`、`get_data`、`get_filters`、`extract`；`PdfFont` 有 `is_embedded`、`get_base_name`、`get_family_name`、`get_weight`。逐字的字型名稱、顏色、矩陣、渲染模式不在高階介面。【直讀】[textpage.py](https://github.com/pypdfium2-team/pypdfium2/blob/main/src/pypdfium2/_helpers/textpage.py)、[page.py](https://github.com/pypdfium2-team/pypdfium2/blob/main/src/pypdfium2/_helpers/page.py)、[pageobjects.py](https://github.com/pypdfium2-team/pypdfium2/blob/main/src/pypdfium2/_helpers/pageobjects.py)
17. pypdfium2 的二進位檔來自 bblanchon/pdfium-binaries（MIT 授權的建置腳本），該專案提供 Windows、macOS、Linux（含 musl）、Android、iOS、WASM 的預建檔，另有含 V8 的版本。【直讀】[pdfium-binaries README](https://github.com/bblanchon/pdfium-binaries/blob/master/README.md)
18. 版本號有一處對不上：pypdfium2 主分支的 `autorelease/record.json` 記錄 5.15.2 對應 PDFium 8138；但我安裝的 5.15.2 輪檔（Linux x86_64）自己回報 PDFium 159.0.8149.0。【直讀】加【實測】。不影響結論。

### 3.2 PyMuPDF 與 MuPDF

1. PyMuPDF 最新版 2.1，2026-11-02 上傳，需要 Python 3.11 以上；PyPI 授權欄位「Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License」。【直讀】[PyPI](https://pypi.org/project/PyMuPDF/)
2. PyMuPDF 的 COPYING 是 GNU AGPL 第 3 版全文；README：「Open source — GNU AGPL v3. Free for open-source projects」「Commercial — separate commercial licences available from Artifex for proprietary applications」。【直讀】[COPYING](https://github.com/pymupdf/PyMuPDF/blob/main/COPYING)、[README](https://github.com/pymupdf/PyMuPDF/blob/main/README.md)
3. MuPDF 官方文件：AGPL「要求公開以它建置的系統的完整原始碼」；不能或不願遵守者可改用商業授權。【直讀】[license.md](https://github.com/ArtifexSoftware/mupdf/blob/master/docs/license.md)
4. Artifex 的說法：沒有商業授權，不能把 MuPDF 用在散布的軟體或軟體即服務；商業授權可針對 SaaS、OEM 或內部使用量身訂做。【搜尋摘要】[artifex.com/licensing](https://artifex.com/licensing)（該站我讀不到）
5. 2.0 版（2026-10-19）起，版面分析程式碼併入主套件；`pymupdf-layout` 2.1 變成空殼套件；主套件相依 onnxruntime、numpy、networkx、pymupdf4llm。【直讀】[changes.txt](https://github.com/pymupdf/PyMuPDF/blob/main/changes.txt)、[pymupdf-layout PyPI](https://pypi.org/project/pymupdf-layout/)
6. `pymupdf-layout` 舊版的授權來源不一致：PyPI 上 1.28.2 的欄位寫 AGPL 或 Artifex 商業授權，一個第三方套件索引卻寫 PolyForm Noncommercial 或商業授權。【直讀】對【第三方】，互相矛盾。不影響結論，因為兩種都不符底線。
7. `Page.get_texttrace()` 每個 span 回傳：字型、字級、顏色、色彩空間、不透明度、上伸與下伸、方向、線寬、書寫模式、`type`（0 填色、1 描邊、3 忽略，即隱藏文字）、`layer`（圖層名稱）、`seqno`（繪製順序），以及逐字的 Unicode、字形編號、原點、外框。文件明講可偵測三種不可見：不透明度為 0、類型大於 1、被順序號較大的物件蓋住。【直讀】[functions.rst](https://github.com/pymupdf/PyMuPDF/blob/main/docs/functions.rst)
8. `Page.get_bboxlog()` 把每個繪圖動作分類為 fill-text、stroke-text、ignore-text（文件註明「例如 OCR 文字層所用」）、fill-path、stroke-path、fill-image、fill-shade。【直讀】同上
9. `get_drawings()` 回傳路徑的線、曲線、矩形、四邊形，以及填色、描邊色、線寬、虛線、不透明度、圖層、順序號；`extended=True` 會加上裁切與群組的階層。`get_image_info()` 回傳圖片外框、尺寸、色彩空間、變換矩陣、`has-mask`。【直讀】[page.rst](https://github.com/pymupdf/PyMuPDF/blob/main/docs/page.rst)
10. 價格：沒有查到 Artifex 公布的價目。唯一的數字來自一個第三方評論站，可信度低，不採用。【第三方】
11. MuPDF 的 JavaScript 版（npm 套件 `mupdf` 1.28.6）授權欄位是 AGPL-3.0-or-later，同樣排除。【直讀】[npm](https://www.npmjs.com/package/mupdf)

### 3.3 pdfminer.six

1. 最新版 20260107，授權 MIT，需要 Python 3.10 以上。【直讀】[PyPI](https://pypi.org/project/pdfminer.six/)、[LICENSE](https://github.com/pdfminer/pdfminer.six/blob/master/LICENSE)
2. `LTChar` 的屬性：`matrix`、`fontname`、`ncs`、`graphicstate`（內含填色 `ncolor` 與描邊色 `scolor`）、`adv`、`upright`、外框、`size`。`size` 的算法是外框的高（直排時是寬），不是 PDF 的字級。【直讀】[layout.py](https://github.com/pdfminer/pdfminer.six/blob/master/pdfminer/layout.py)
3. 直譯器的 `do_Tr` 會把渲染模式存進 `textstate.render`，但 `render_char` 建立 `LTChar` 時沒有傳入這個值。要拿到渲染模式得自己繼承裝置類別。【直讀】[pdfinterp.py](https://github.com/pdfminer/pdfminer.six/blob/master/pdfminer/pdfinterp.py)、[converter.py](https://github.com/pdfminer/pdfminer.six/blob/master/pdfminer/converter.py)
4. 頁面物件有 `mediabox`、`cropbox`、`rotate`；其他頁框要從 `attrs` 自己讀。【直讀】[pdfpage.py](https://github.com/pdfminer/pdfminer.six/blob/master/pdfminer/pdfpage.py)
5. 實測：隱藏文字照樣輸出、`LTChar` 上沒有 `render_mode` 屬性；旋轉 30 度的 10pt 文字，`size` 回報 12.0 與 11.2。【實測】

### 3.4 pdfplumber

1. 最新版 0.11.10（2026-06-15），MIT；相依 `pdfminer.six==20260107`、`Pillow>=12.2.0`、`pypdfium2>=5.9.0`。【直讀】[PyPI](https://pypi.org/project/pdfplumber/)、[LICENSE](https://github.com/jsvine/pdfplumber/blob/stable/LICENSE.txt)
2. 字元屬性含 `fontname`、`size`、`adv`、`upright`、`matrix`、`stroking_color`、`non_stroking_color`、`mcid`、`tag`（後兩者標為實驗性）。文件列出的字元屬性裡沒有渲染模式。【直讀】[README](https://github.com/jsvine/pdfplumber/blob/stable/README.md)
3. `.metadata` 來自 Info 字典，文件舉例含 Producer。【直讀】同上
4. 頁面影像的渲染用的是 pypdfium2（`display.py` 直接匯入並呼叫 `pypdfium2.PdfDocument`）。【直讀】[display.py](https://github.com/jsvine/pdfplumber/blob/stable/pdfplumber/display.py)

### 3.5 pikepdf 與 qpdf

1. pikepdf 最新版 10.18.2（2026-11-16），MPL-2.0，需要 Python 3.11 以上。【直讀】[PyPI](https://pypi.org/project/pikepdf/)
2. README：MPL 2.0「允許與其他作品結合，包含商業與閉源軟體，但要求公開你對 pikepdf 本身所做的原始碼層級修改」。【直讀】[README](https://github.com/pikepdf/pikepdf/blob/main/README.md)
3. README 自己列的不適用情境：從 HTML 或範本產生 PDF、把 PDF 渲染成影像（建議 PyMuPDF 或 pypdfium2）、抽取文字或表格（建議 pdfminer.six 或 pdfplumber）。內容串流文件：「pikepdf 目前不實作文字抽取」「強烈建議不要從內容串流硬刮文字」。【直讀】同上、[content_streams.md](https://github.com/pikepdf/pikepdf/blob/main/docs/topics/content_streams.md)
4. 圖片文件：軟遮罩或明確遮罩會被合成進透明通道並轉成 PNG；`/Separation` 與 `/DeviceN` 色彩空間的圖片不轉檔，會拋錯；一張看起來是單一圖片的東西，實際可能由多張圖、遮罩、透明群組、向量繪圖、Form XObject 合成。【直讀】[images.md](https://github.com/pikepdf/pikepdf/blob/main/docs/topics/images.md)
5. qpdf 授權 Apache-2.0（第 7 版以前是 Artistic License 2.0）；Homebrew 目前的版本是 12.4.2。【直讀】[qpdf README](https://github.com/qpdf/qpdf/blob/main/README.md)、[Homebrew formula](https://github.com/Homebrew/homebrew-core/blob/master/Formula/q/qpdf.rb)
6. 輪檔內含的第三方元件：qpdf（Apache-2.0）、libjpeg-turbo（IJG 加 BSD-3-Clause 加 Zlib）；Windows 輪檔另含靜態連結的 OpenSSL（Apache-2.0）與 zlib；musllinux 輪檔含 GCC 執行期函式庫（GPL-3.0 附 GCC Runtime Library Exception）。【直讀】[第三方元件清單](https://github.com/pikepdf/pikepdf/blob/main/third-party-licenses/README.md)
7. 一處自相矛盾：README 說 macOS 輪檔含「GnuTLS stack」，但第三方元件清單明講 Linux 與 macOS 的建置使用 qpdf 內建的加密後端，「沒有連結或夾帶任何 TLS 或加密函式庫」。【直讀】，兩份官方文件互相矛盾。桌面版出貨前要以實際輪檔內容為準。

### 3.6 Poppler

1. 目前版本 26.11.0。【直讀】（打包者中繼資料）[Homebrew formula](https://github.com/Homebrew/homebrew-core/blob/master/Formula/p/poppler.rb)
2. 授權是 GPL，確切標示來源不一致：Homebrew 標「GPL-2.0-only 或 GPL-3.0-only（見 README-XPDF）」，conda-forge 標「GPL-2.0-or-later」。【直讀】（打包者中繼資料），互相矛盾。
3. Poppler 的 README 寫明：xpdf 以及因此 Poppler 是 GPL 不是 LGPL，任何使用 Poppler 的應用程式也必須以 GPL 授權。【搜尋摘要】（官方站讀不到）
4. 不論哪一種標示，都落在排除條款內。包裝 Poppler 的工具（`pdftoppm`、`pdftocairo`、Python 的 pdf2image、Rust 的 poppler-rs）一併排除。

### 3.7 pdf.js

1. LICENSE 是 Apache License 2.0；npm 上 `pdfjs-dist` 最新版 6.5.438，授權欄位 Apache-2.0。【直讀】[LICENSE](https://github.com/mozilla/pdf.js/blob/master/LICENSE)、[npm](https://www.npmjs.com/package/pdfjs-dist)
2. `TextItem` 的欄位：`str`、`dir`、`transform`、`width`、`height`、`fontName`（註解：pdf.js 為轉換後字型取的名稱）、`hasEOL`。`TextStyle`：`ascent`、`descent`、`vertical`、`fontFamily`（註解：可能的字族）。沒有顏色，沒有渲染模式。【直讀】[api.js](https://github.com/mozilla/pdf.js/blob/master/src/display/api.js)
3. `evaluator.js` 裡 `textRenderingMode` 只出現在產生運算元清單的路徑與狀態定義，沒有寫進文字內容項目。【直讀】[evaluator.js](https://github.com/mozilla/pdf.js/blob/master/src/core/evaluator.js)
4. 另有 `getOperatorList`、`getOptionalContentConfig`、`getMetadata`、`saveDocument`。【直讀】同上

### 3.8 pypdf

1. 最新版 7.1.1（2026-11-18），BSD-3-Clause，需要 Python 3.10 以上。【直讀】[PyPI](https://pypi.org/project/pypdf/)、[LICENSE](https://github.com/py-pdf/pypdf/blob/main/LICENSE)
2. README 的自我介紹：純 Python，能拆分、合併、裁切、變換頁面，加自訂資料、檢視選項與密碼，並能取出文字與中繼資料。沒有提到渲染或繪製文字。【直讀】[README](https://github.com/py-pdf/pypdf/blob/main/README.md)
3. `extract_text` 的 `visitor_text` 收到五個參數：文字、目前變換矩陣、文字矩陣、字型字典、字級。官方文件：「可惜在複雜的 PDF 文件裡，交給 visitor 的座標可能是錯的。」【直讀】[extract-text.md](https://github.com/py-pdf/pypdf/blob/main/docs/user/extract-text.md)
4. 頁面有 `mediabox`、`cropbox`、`bleedbox`、`trimbox`、`artbox`、`rotation`、`user_unit`、`images`。【直讀】[_page.py](https://github.com/py-pdf/pypdf/blob/main/pypdf/_page.py)

### 3.9 Ghostscript

1. LICENSE：以 GNU Affero GPL 第 3 版或更新版散布；官方 Readme：GhostPDL 同時提供 AGPL 與 Artifex 的商業授權。【直讀】[LICENSE](https://github.com/ArtifexSoftware/ghostpdl/blob/master/LICENSE)、[Readme.rst](https://github.com/ArtifexSoftware/ghostpdl/blob/master/doc/src/Readme.rst)
2. Homebrew 目前的版本是 10.08.0，授權標 AGPL-3.0-or-later。【直讀】（打包者中繼資料）[Homebrew formula](https://github.com/Homebrew/homebrew-core/blob/master/Formula/g/ghostscript.rb)
3. 注意相依陷阱：任何在背後呼叫 `gs` 執行檔的工具，都等於用了 Ghostscript。

### 3.10 ReportLab

1. 最新版 5.1.0（2026-10-22）；PyPI 授權欄位「BSD license (see license.txt for details), Copyright (c) 2000-2025, ReportLab Inc.」，分類 BSD License。Python 版本需求有一處對不上：套件中繼資料寫 3.9 以上，但更新紀錄寫 5.1.0a1 起「至少需要 3.10」，分類標籤也從 3.10 起列。以 3.10 為準較安全。【直讀】[PyPI](https://pypi.org/project/reportlab/)（原始碼包 `setup.py`、`CHANGES.md`）
2. 原始碼包裡的 LICENSE 是三條款 BSD 的條文（保留版權聲明、二進位散布須附聲明、不得用公司名背書）。【直讀】（原始碼包 `LICENSE`）
3. `ttfonts.py` 檔頭：TrueType 字型支援子集與內嵌，可表示所有 16 位元 Unicode 字元；`makeToUnicodeCMap` 為每個子集產生 ToUnicode；遇到 PostScript 外框的字型會拋出「postscript outlines are not supported」。【直讀】（原始碼包 `src/reportlab/pdfbase/ttfonts.py`）
4. 文字物件有 `setFont`、`setTextOrigin`、`setTextTransform`、`setCharSpace`、`setWordSpace`、`setHorizScale`、`setRise`、`setTextRenderMode`、`setFillColor`。【直讀】（原始碼包 `src/reportlab/pdfgen/textobject.py`）
5. 字形排版：`TTFont` 有 `shapable` 參數，需要另裝 uharfbuzz，沒裝會拋錯。【直讀】同第 3 點
6. 亞洲字型另一條路是 Adobe 語言包的 CID 字型（繁體中文是 `MSung-Light`、`MHei-Medium`），這種字型不內嵌。【直讀】（原始碼包 `docs/userguide/ch2a_fonts.py`）
7. 實測見 3.13 第 9 點。

### 3.11 Rust 生態

1. pdfium-render 0.9.5（2026-11-01），MIT 或 Apache-2.0；README 說明可在執行期綁定系統或隨附的 PDFium、可靜態連結、可編成 WASM，並提醒 PDFium 不保證執行緒安全，官方建議用多行程而非多執行緒。【直讀】[README](https://github.com/ajrcarey/pdfium-render/blob/master/README.md)、[crates.io](https://crates.io/crates/pdfium-render)
2. pdfium-render 的 `PdfPageTextChar` 有 `unicode_value`、`has_unicode_map_error`、`scaled_font_size`、`unscaled_font_size`、`font_name`、`font_weight`、一組字型旗標、`text_object`、`render_mode`、`fill_color`、`stroke_color`、`angle_degrees`、`tight_bounds`、`loose_bounds`、`origin`、`is_generated`、`is_hyphen`。【直讀】[char.rs](https://github.com/ajrcarey/pdfium-render/blob/master/src/pdf/document/page/text/char.rs)
3. hayro 0.9.0（2026-11-02），Apache-2.0 或 MIT；唯讀；分成 hayro-syntax、hayro-interpret、hayro、hayro-svg、hayro-write 與數個影像解碼子套件；作者自述凡是 Chrome 或 Firefox 能正確顯示的 PDF 幾乎都能正確渲染，穩健性、效能、介面三方面仍在補。【直讀】[README](https://github.com/LaurenzV/hayro/blob/main/README.md)
4. hayro-interpret 的 `Device` 介面：`draw_path`、`push_clip_path`、`push_transparency_group`（含軟遮罩）、`draw_glyph_run`、`draw_image`、`begin_marked_content`、`end_marked_content`。README：「仍在開發，目前幾乎沒有使用文件，現有介面只真正適合渲染成 PNG 或 SVG」。【直讀】[device.rs](https://github.com/LaurenzV/hayro/blob/main/hayro-interpret/src/device.rs)、[README](https://github.com/LaurenzV/hayro/blob/main/hayro-interpret/README.md)
5. krilla 0.8.2（2026-06-04），MIT 或 Apache-2.0；特色與範圍見 1.2。【直讀】[README](https://github.com/LaurenzV/krilla/blob/main/README.md)
6. 授權陷阱三個：`mupdf`（mupdf-rs）是 AGPL-3.0；`pdfium`（newinnovations/pdfium-rs）是 GPL-3.0；`poppler-rs` 綁定是 MIT 但底下是 GPL 的 Poppler。【直讀】[crates.io mupdf](https://crates.io/crates/mupdf)、[crates.io pdfium](https://crates.io/crates/pdfium)、[crates.io poppler-rs](https://crates.io/crates/poppler-rs)
7. 其餘：lopdf 0.47.0（MIT）、pdf 0.10.0（MIT）、pdf-extract 0.12.1（MIT）、printpdf 0.12.13（MIT）、pdf-writer 0.15.0（MIT 或 Apache-2.0）、pdf_oxide 0.3.78（MIT 或 Apache-2.0）、oxidize-pdf 5.6.1（MIT）、qpdf 綁定 0.3.7（MIT 或 Apache-2.0）。【直讀】（crates.io 授權欄位）
8. pdf_oxide 的 README 宣稱平均每份文件 0.8 毫秒、比其他函式庫快 5 倍、3,830 份 PDF 全數通過。這些都是作者自己的數字，沒有找到獨立驗證。【直讀】（主張本身），未驗證。

### 3.12 Illustrator 開啟 PDF 的行為（與寫出格式有關）

1. 社群討論一致的說法：要在 Illustrator 裡編輯 PDF 的文字，電腦上必須裝有該字型；只內嵌子集的字型會被回報為缺字型；開啟後文字常被拆成大量零碎的文字物件。【搜尋摘要】，來源是 Adobe 社群論壇貼文，不是 Adobe 官方文件，例如[這一串](https://community.adobe.com/t5/illustrator-discussions/opening-a-pdf-in-ai-without-outlining-the-texts/m-p/9911431)。
2. 沒有找到 Adobe 官方說明頁。這一點對「以文字可編輯的 PDF 交付 Illustrator」的設計影響很大，需要拿實際的 Illustrator 測。

### 3.13 實測紀錄

環境：Linux x86_64、Python 3.13.18，暫存虛擬環境，安裝 pypdfium2 5.15.2、pikepdf 10.18.2、reportlab 5.1.0、pdfminer.six 20260107、Pillow 13.0.0。

測試檔一：用 ReportLab 產生一頁，含紅色 Helvetica 14pt 文字、旋轉 30 度的藍色 Times-Roman 10pt 文字、渲染模式 3 的 Courier 12pt 文字、一條含貝茲曲線的填色加描邊路徑、一張帶透明通道的圖片；再用 pikepdf 加上 CropBox、TrimBox，以及一個名為 LayerA 的圖層和包在其中的矩形。

1. PDFium 逐字讀回：Unicode、外框、原點、字級（14、10、12 皆正確）、字型名稱、填色、矩陣（旋轉文字為 0.87, 0.50, -0.50, 0.87）皆正確。
2. `FPDFText_GetTextObject` 加 `FPDFTextObj_GetTextRenderMode`：隱藏文字回傳 INVISIBLE，其餘回傳 FILL。
3. 文字頁會在行尾補上 `\r\n`，這些字 `FPDFText_IsGenerated` 回傳 1，字型與顏色為空。
4. `FPDFText_GetCharAngle` 對逆時針旋轉 30 度的文字回傳 5.76 弧度（即 330 度）。角度的方向定義要自己校正，矩陣比較可靠。
5. 路徑：六個線段的類型與座標、填色規則、描邊、填色、描邊色、線寬皆讀到。
6. 圖片：`GetBitmap` 回傳三通道；`GetRenderedBitmap` 回傳四通道（遮罩已套用）；pikepdf 看到該圖片有 `/SMask`。
7. 頁框三種皆正確；以 2 倍渲染得到 760×560 像素，等於 CropBox（380×280）的兩倍，表示渲染範圍是 CropBox。
8. 圖層：見 3.1 第 10、11 點。pikepdf 讀到 `/OCProperties` 的圖層清單與 `/D` 設定；內容串流裡看得到 `Tr 3` 與 `/OC /MC0 BDC`。
9. 寫出繁體中文（字型為系統上的文泉驛正黑 TTC，16.8 MB）：
   - ReportLab：輸出檔 12.7 KB，內嵌子集 13.6 KB，有 ToUnicode，字型類型是簡單 TrueType，BaseFont 為 `AAAAAA+WenQuanYiZenHei-0`。PDFium 讀回文字完全正確、回報已內嵌、無對照錯誤。
   - PDFium（`FPDFText_LoadFont` 以 CID 模式載入，再 `FPDFText_SetText`）：輸出檔 9.5 MB，內嵌字型串流 16.8 MB（未子集），Type0 加 CIDFontType2，有 ToUnicode。讀回文字正確。
10. 把 ReportLab 輸出檔的 `/ToUnicode` 用 pikepdf 刪掉後：PDFium 對四個中文字與後兩個中文字回傳原始字碼（0x1、0x2…）且 `HasUnicodeMapError` 為 1，ASCII 部分為 0；pdfminer.six 輸出 `(cid:1)(cid:2)…`。
11. pdfminer.six：見 3.3 第 5 點。

## 4. 建議

### 首選：PDFium 加 pikepdf 讀，ReportLab 寫

三個元件全部是寬鬆授權或檔案層級著佐權，可商用，可隨桌面版散布。

1. **PDFium 當解析與渲染的主引擎。** 伺服器端經 pypdfium2 的 raw 介面呼叫；桌面版用同一顆 PDFium（Rust 外殼用 pdfium-render，或直接呼叫 C 介面）。理由：這是本次查到唯一一個授權可用、又同時做到逐字屬性齊全（位置、字型、字級、顏色、矩陣、渲染模式）、向量路徑、圖片、頁框、渲染的引擎，而且每一項我都實際叫過。伺服器與桌面用同一顆引擎，兩邊的抽取結果才會一致。
2. **pikepdf 補 PDFium 公開介面的缺口。** 圖層清單與預設開關（`/OCProperties`）、字型字典有無 `/ToUnicode` 與內嵌字型檔、圖片的軟遮罩本身、Info 與 XMP、壞檔修復與解密。PDFium 告訴你「這個物件屬於叫 LayerA 的圖層」，pikepdf 告訴你「LayerA 預設是關的」，兩者要合起來用。
3. **ReportLab 寫出文字可編輯的 PDF。** 理由：BSD 授權；實測會做字型子集並產生 ToUnicode；文字物件能設定文字矩陣、字距、渲染模式，足以照著抽取結果把字放回原位。PDFium 自己的寫出功能不做子集，一行中文就讓檔案脹到 9.5 MB，只適合當後備。

要接受的代價與要先做的事：

- 需要的 PDFium 函式有一大半標為 Experimental API，官方保留更動的權利。要鎖定 PDFium 版本，升級時跑回歸測試。
- PDFium 不是執行緒安全。平行處理要用多行程。
- pypdfium2 的高階介面不夠用，要自己用 ctypes 包一層；它的 README 也提醒有物件生命週期的風險。
- 還原度報告要的「隱藏的 OCR 文字層」判斷，光靠渲染模式 3 不夠。PyMuPDF 文件列的另外兩種不可見（不透明度為 0、被後畫的物件蓋住）在 PDFium 要自己用物件順序與外框重疊去算。
- ReportLab 不吃 CFF 外框的 OpenType 字型。Noto 系列的中日韓字型要挑 TrueType 外框的版本，或先轉檔。
- ReportLab 把字型切成每個最多 256 字形的簡單字型子集，中文多的頁面會出現多個子集字型，文字段落會在子集邊界被切開。這對 Illustrator 的可編輯性有沒有影響，要實測。
- MPL-2.0 不是 GPL 也不是 AGPL，不在排除條款內，但它是著佐權授權。只要不改 pikepdf 自己的原始檔就沒有公開義務；請法務確認一次。

### 備選一：純 Rust 路線，hayro 讀加 krilla 寫

授權是 Apache-2.0 或 MIT，沒有 C++ 相依，桌面版打包最單純，記憶體安全。hayro-interpret 的 `Device` 介面正好是「把每個繪圖動作交給你」的形狀，很適合做反編譯。不列為首選的原因：作者自己說介面仍在變、幾乎沒有文件、穩健性還在補，版本號是 0.9；我也沒有實測。適合在桌面版開工時重新評估，或現在先當第二引擎做交叉比對。krilla 的字形層級介面與 CFF 子集能力，可能比 ReportLab 更適合做 Illustrator 交付，值得做一次對照實驗。

### 備選二：付費，買 Artifex 的 MuPDF 與 PyMuPDF 商業授權

功能上是本次查到最完整的：一個 `get_texttrace` 就給齊逐字資訊、隱藏文字類型、圖層名稱、繪製順序，省下首選方案裡要自己拼湊的工作。代價是價格未公開、要詢價，而且桌面版散布的條款要另談。建議先用首選方案開工，同時向 Artifex 詢價，拿到數字再決定值不值得。

### 輔助工具

- **pdfminer.six 或 PLAYA-PDF**（MIT）：當第二意見，在測試裡跟 PDFium 的抽取結果互相比對。不要當主引擎：pdfminer.six 沒有渲染模式，字級定義也不對。
- **pdfplumber**（MIT）：開發期除錯與目視檢查。
- **pdf.js**（Apache-2.0）：瀏覽器端的預覽顯示。不要當分析引擎：文字是段落層級，沒有顏色與渲染模式。
- **pypdf**：本產品用不到，PDFium 加 pikepdf 已涵蓋。

### 不可使用

- PyMuPDF、MuPDF、mupdf-rs、MuPDF.js 的開源版（AGPL）
- Ghostscript 的開源版（AGPL），以及任何在背後呼叫 `gs` 的工具
- Poppler 與包裝它的一切（GPL）
- Rust 的 `pdfium` 套件（GPL-3.0；不要跟 pdfium-render 搞混）
- borb（AGPL）
- 任何相依 PyMuPDF 的套件，例如 pymupdf4llm

## 5. 查不到的事

1. Artifex 對 MuPDF、PyMuPDF、Ghostscript 商業授權的價格與計價方式。官方未公布；artifex.com 我也讀不到。
2. Poppler 確切的授權標示（GPL-2.0-only 或 GPL-3.0-only，還是 GPL-2.0-or-later）。官方的 COPYING 與 README-XPDF 讀不到，兩個打包者的標示不一致。不影響「排除」的結論。
3. Poppler 有沒有可購買的商業授權。沒有查到。（線索：上游的 Xpdf 似乎另有商業授權，但那不是 Poppler，本次未查證。）
4. Illustrator 實際開啟 ReportLab、krilla、PDFium 寫出的 PDF 時，文字是否可編輯、字型如何比對、段落會不會被拆碎。只有社群論壇的說法，沒有 Adobe 官方文件，也沒有實測。這是「以文字可編輯的 PDF 交付 Illustrator」能不能成立的關鍵，必須拿真的 Illustrator 測。
5. PDFium 對真實世界困難 PDF 的表現：直排文字、Type 3 字型、只靠預定義 CMap 而沒有 ToUnicode 的中日韓字型、合字、右到左文字。實測只用了合成檔。
6. PDFium 標為 Experimental API 的函式的穩定性承諾。沒有查到任何書面保證。
7. PDFium 能不能從公開介面判斷「文字被後畫的圖片或色塊蓋住」。沒有現成函式，理論上可用物件順序加外框自己算，未實測。
8. pdf.js 的 `getTextContent` 會不會輸出渲染模式 3 的不可見文字。未實測。
9. hayro-interpret 的 `GlyphRun` 帶了哪些資訊（Unicode、字型名稱、字級是否齊全）。只讀了 `Device` 介面，沒有讀 `GlyphRun` 的定義，也沒有實測。
10. krilla 寫出的字型結構（是否為 Type0 的 CID 字型）以及中日韓文字的實際輸出品質。未實測。
11. PyMuPDF 2.x 內含的版面分析模型權重，是否另有授權條款。沒有查到。
12. pikepdf 的 macOS 輪檔到底有沒有夾帶 GnuTLS。README 與第三方元件清單說法相反，要打開實際輪檔確認。
13. pypdfium2 5.15.2 輪檔內的 PDFium 版本為何與主分支記錄檔不同（8149 對 8138）。
14. 各函式庫的速度與記憶體用量。沒有做任何效能測試，也沒有找到涵蓋這些函式庫的獨立評測。pdf_oxide 與 PLAYA-PDF 的速度主張都是作者自述。
15. PLAYA-PDF、pdftext、pdf_oxide、oxidize-pdf、lopdf、printpdf 的實際抽取品質。都只讀了 README。

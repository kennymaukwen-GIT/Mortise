# 查證紀錄：輸出格式與寫檔函式庫

- 查證日期：2026-11-20
- 適用產品：Mortise（榫）文件反編譯 SaaS，未來可能有桌面版
- 授權底線：所有元件必須可商用；排除 GPL、AGPL 元件（另有可購買商業授權者列為「需付費」）；排除非商用授權的模型權重
- 範圍：PPTX、Google Slides（由 PPTX 匯入）、DOCX、XLSX、Illustrator（以文字可編輯的 PDF 交付）、InDesign（以 IDML 交付）這幾種輸出的寫檔函式庫、格式規則與已知失真

## 0. 查證方式與限制

本次查證的工具狀況會影響每一條結論的可信度，先講清楚。

| 來源 | 狀況 |
|---|---|
| 網路搜尋 | 可用 |
| github.com、raw.githubusercontent.com | 可直接讀取（LICENSE、README、原始碼、文件原檔），也可用 git 讀取分支與標籤 |
| pypi.org、registry.npmjs.org、api.nuget.org、repo1.maven.org、crates.io | 可直接讀取（版本、上傳日期、授權欄位）。讀的是登錄站的資料介面；下文為了方便點閱，npm 與 NuGet 的連結給的是同一個套件的網頁（www.npmjs.com、www.nuget.org），那些網頁本身我沒有打開 |
| learn.microsoft.com、support.microsoft.com | **無法讀取**（被工作環境的網路規則擋下）。Microsoft 的說明只有搜尋摘要 |
| helpx.adobe.com、community.adobe.com、developer.adobe.com、illustrator.uservoice.com | **無法讀取**。Adobe 的說明與論壇只有搜尋摘要 |
| support.google.com、developers.google.com | **無法讀取**。Google 的說明只有搜尋摘要 |
| python-pptx.readthedocs.io、python-docx.readthedocs.io、xlsxwriter.readthedocs.io、openpyxl.readthedocs.io | **無法讀取**。前三者改讀 GitHub 上的文件原檔；openpyxl 的原始碼庫在 foss.heptapod.net，也讀不到，改讀 PyPI 輪檔內的原始碼與授權檔 |
| ecma-international.org、iso.org | **無法讀取**。ECMA-376 的條文只有間接來源 |

下文每條事實後面標註查證方式：

- **【直讀】** 直接讀到官方來源的原文（LICENSE、官方原始碼、官方文件原檔、套件登錄站的中繼資料）
- **【實測】** 在暫存環境實際安裝套件、跑過程式得到的結果（見 3.14）
- **【搜尋摘要】** 搜尋工具轉述了頁面內容，但我沒有親自打開該頁
- **【第三方】** 只有非官方來源

實測的範圍很小，而且有一個根本限制：**這個環境沒有 PowerPoint、Word、Excel、Illustrator、InDesign，也沒有 Google Slides**。實測只能證明「函式庫把什麼寫進檔案」，不能證明「目標軟體打開後長什麼樣」。凡是牽涉目標軟體行為的結論，都沒有實測。

## 1. 總表

「可否商用」指用在收費的線上服務；「可否隨桌面版散布」指把程式打包進安裝檔交給使用者。這個主題的元件都不是機器學習模型，「權重授權」一欄皆為不適用。

### 1.1 PPTX

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **python-pptx 1.0.2** | 讀寫 PPTX（Python） | MIT | 不適用 | 可 | 可，需附授權文 | 文字方塊、自動圖形、圖片（含裁切）、表格（含合併儲存格）、圖表、連接線、群組、自由曲線、備忘稿、旋轉、純色與圖樣填滿、線性漸層（改既有色標）、線條寬度與虛線。每個物件都能取到底層 XML 節點，可自行補寫 | 不能建立母片與版面配置；字型名稱只寫拉丁欄位，不寫東亞欄位；自動縮放只寫空的設定，不算縮放比例；漸層不能增減色標、不支援放射狀；沒有刪除線、上下標、字距、項目符號、陰影、透明度、箭頭的介面；不支援 SVG 圖片；不能內嵌字型；不能刪除或複製投影片。主分支自 2024-08-06 起沒有新提交 | 是（直讀加實測） | [LICENSE](https://github.com/scanny/python-pptx/blob/master/LICENSE)、[PyPI](https://pypi.org/project/python-pptx/)、[text.py](https://github.com/scanny/python-pptx/blob/master/src/pptx/text/text.py)、[fill.py](https://github.com/scanny/python-pptx/blob/master/src/pptx/dml/fill.py) |
| **PptxGenJS 4.0.1** | 產生 PPTX（JavaScript，瀏覽器與 Node 皆可） | MIT | 不適用 | 可 | 可 | 可用 `defineSlideMaster` 定義母片；設定字型時同時寫拉丁、東亞、複雜文字三個欄位 | 三個字型欄位只能填同一個名稱；圖形填滿只有「無」與「純色」，沒有漸層；自動縮放同樣只寫空設定；不能內嵌字型；只寫不讀。最新版發布於 2025-06-26 | 是（直讀原始碼，未實測） | [LICENSE](https://github.com/gitbrent/PptxGenJS/blob/master/LICENSE)、[npm](https://www.npmjs.com/package/pptxgenjs)、[gen-xml.ts](https://github.com/gitbrent/PptxGenJS/blob/master/src/gen-xml.ts)、[core-interfaces.ts](https://github.com/gitbrent/PptxGenJS/blob/master/src/core-interfaces.ts) |
| **Open XML SDK 3.5.1**（DocumentFormat.OpenXml） | PPTX、DOCX、XLSX 的低階讀寫與驗證（.NET） | MIT | 不適用 | 可 | 可 | 自述為依照 Microsoft Office 對 ISO 29500 的實作而設計的低階介面（強型別類別）；內含 `OpenXmlValidator` 驗證器 | 自述「不提供高階抽象」，每個元素都要自己組；需要 .NET 執行環境 | 部分（授權、版本、README、驗證器原始碼直讀；能力未實測） | [LICENSE](https://github.com/dotnet/Open-XML-SDK/blob/main/LICENSE)、[README](https://github.com/dotnet/Open-XML-SDK/blob/main/README.md)、[OpenXmlValidator.cs](https://github.com/dotnet/Open-XML-SDK/blob/main/src/DocumentFormat.OpenXml.Framework/Validation/OpenXmlValidator.cs)、[NuGet](https://www.nuget.org/packages/DocumentFormat.OpenXml) |
| **ShapeCrawler 0.82.0** | 讀寫 PPTX（.NET，建在 Open XML SDK 上） | MIT | 不適用 | 可 | 可 | 自述在 Open XML SDK 之上提供較直覺的介面 | 能力清單未查；需要 .NET | 部分（只查授權與版本） | [LICENSE](https://github.com/ShapeCrawler/ShapeCrawler/blob/master/LICENSE)、[README](https://github.com/ShapeCrawler/ShapeCrawler/blob/master/README.md) |
| **Apache POI 6.0.0** | PPTX、DOCX、XLSX 讀寫（Java） | Apache-2.0 | 不適用 | 可 | 可，需附 LICENSE 與 NOTICE | 未查 | 能力未查；需要 Java 執行環境 | 部分（只查授權與版本） | [LICENSE](https://github.com/apache/poi/blob/trunk/legal/LICENSE)、[Maven 中繼資料](https://repo1.maven.org/maven2/org/apache/poi/poi-ooxml/maven-metadata.xml) |
| pptx-ea-font 0.0.4 | python-pptx 的小工具，補寫東亞字型欄位 | MIT | 不適用 | 可 | 可 | 套件說明為「正確設定 pptx 的東亞字型」 | 未讀原始碼、未實測；2024-09-25 後無更新。同樣的事自己用幾行 XML 操作即可做到（見 3.14） | 部分（只查 PyPI 中繼資料） | [PyPI](https://pypi.org/project/pptx-ea-font/) |
| python-pptx-ng 0.7.0 | python-pptx 的分支 | MIT | 不適用 | 可 | 可 | 未查 | 最後發布於 2024-01-17，比上游的 1.0.2 還舊，不建議 | 部分（只查 PyPI 中繼資料） | [PyPI](https://pypi.org/project/python-pptx-ng/) |

### 1.2 DOCX

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **python-docx 1.2.0** | 讀寫 DOCX（Python） | MIT | 不適用 | 可 | 可，需附授權文 | 段落、文字樣式（粗斜體、底線、刪除線、上下標、大小寫、醒目提示等）、段落格式（縮排、行距、段前段後、定位點）、表格（含合併）、行內圖片、分節（紙張大小、邊界、方向、頁首頁尾）、樣式、註解（1.2.0 新增）。每個物件都能取到底層 XML 節點 | 沒有文字方塊介面；圖片只能行內，沒有浮動定位介面（官方文件明講）；分節沒有分欄介面；字型名稱只寫 `w:ascii` 與 `w:hAnsi`，不寫 `w:eastAsia`；不能內嵌字型。主分支自 2025-06-16 起沒有新提交 | 是（直讀加實測） | [LICENSE](https://github.com/python-openxml/python-docx/blob/master/LICENSE)、[PyPI](https://pypi.org/project/python-docx/)、[shapes.rst](https://github.com/python-openxml/python-docx/blob/master/docs/user/shapes.rst)、[HISTORY.rst](https://github.com/python-openxml/python-docx/blob/master/HISTORY.rst) |
| **docx 9.16.0**（dolanmiu/docx） | 產生 DOCX（TypeScript，瀏覽器與 Node 皆可） | MIT | 不適用 | 可 | 可 | 文件列有：浮動圖片（含位移與文繞圖）、文字方塊（兩種寫法）、文字框、分欄、分節、形狀、圖表、字型內嵌（含混淆處理）、東亞字型欄位 | 需要 Node 或瀏覽器執行環境；以產生為主。各項功能只讀了文件與原始碼目錄，未實測 | 是（授權、版本、文件直讀；未實測） | [LICENSE](https://github.com/dolanmiu/docx/blob/master/LICENSE)、[npm](https://www.npmjs.com/package/docx)、[images.md](https://github.com/dolanmiu/docx/blob/master/docs/usage/images.md)、[columns.md](https://github.com/dolanmiu/docx/blob/master/docs/usage/columns.md)、[fonts.md](https://github.com/dolanmiu/docx/blob/master/docs/usage/fonts.md) |
| **docx4j 17.3.6** | 讀寫 DOCX、PPTX、XLSX（Java） | Apache-2.0 | 不適用 | 可 | 可 | README 自述為建立、編輯、儲存 OpenXML 封裝的函式庫 | 能力未查；需要 Java | 部分（只查授權與版本） | [LICENSE](https://github.com/plutext/docx4j/blob/master/legals/LICENSE)、[README](https://github.com/plutext/docx4j/blob/master/README.md)、[Maven 中繼資料](https://repo1.maven.org/maven2/org/docx4j/docx4j-core/maven-metadata.xml) |
| Open XML SDK、Apache POI | 同 1.1 | 同 1.1 | 不適用 | 可 | 可 | 同 1.1 | 同 1.1 | 部分 | 同 1.1 |

### 1.3 XLSX

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **XlsxWriter 3.3.3** | 產生 XLSX（Python） | BSD-2-Clause | 不適用 | 可 | 可，需附授權文 | 完整格式、合併儲存格、已定義名稱、圖表、自動篩選、資料驗證、條件式格式、表格、樞紐分析表（3.3.3 新增）、圖片、多格式字串、註解、文字方塊；欄寬可用字元單位或像素；模擬自動調整欄寬；只用標準函式庫；大檔可用省記憶體模式 | **只能建立新檔，不能讀取或修改既有檔**；自動調整欄寬以 Calibri 11 為準，其他字型不準 | 是（直讀加實測） | [LICENSE.txt](https://github.com/jmcnamara/XlsxWriter/blob/main/LICENSE.txt)、[PyPI](https://pypi.org/project/XlsxWriter/)、[README](https://github.com/jmcnamara/XlsxWriter/blob/main/README.rst)、[worksheet.rst](https://github.com/jmcnamara/XlsxWriter/blob/main/dev/docs/source/worksheet.rst) |
| **openpyxl 3.1.5** | 讀寫 XLSX（Python） | MIT | 不適用 | 可 | 可，需附授權文 | 讀寫 xlsx、xlsm、xltx、xltm；樣式、合併儲存格、資料驗證、條件式格式、圖表、圖片、表格、列印設定（依套件內模組判斷） | 欄寬設多少就原樣寫進檔案，不替你加邊距（見 3.6）；最後發布於 2024-06-28；原始碼庫與官方文件本次讀不到 | 是（授權與行為直讀加實測；文件未讀） | [PyPI](https://pypi.org/project/openpyxl/) |
| rust_xlsxwriter 0.103.0 | 產生 XLSX（Rust，與 XlsxWriter 同一作者） | MIT 或 Apache-2.0 | 不適用 | 可 | 可 | 未查 | 只查了授權欄位 | 部分 | [Cargo.toml](https://github.com/jmcnamara/rust_xlsxwriter/blob/main/Cargo.toml) |
| ExcelJS 4.4.0 | 讀寫 XLSX（JavaScript） | MIT | 不適用 | 可 | 可 | 未查 | npm 上最新版發布於 2023-10-19，三年沒有新版 | 部分 | [LICENSE](https://github.com/exceljs/exceljs/blob/master/LICENSE)、[npm](https://www.npmjs.com/package/exceljs) |
| SheetJS 社群版 | 讀寫試算表（JavaScript） | Apache-2.0 | 不適用 | 可 | 可 | 文件說明三種欄寬表示法與換算 | npm 上的 `xlsx` 套件停在 0.18.5（2022-03-24）；較新版本的散布方式本次未查 | 部分 | [LICENSE](https://github.com/SheetJS/sheetjs/blob/master/LICENSE)、[npm](https://www.npmjs.com/package/xlsx) |
| ClosedXML 0.105.1 | 讀寫 XLSX（.NET） | MIT | 不適用 | 可 | 可 | 未查 | 需要 .NET | 部分 | [LICENSE](https://github.com/ClosedXML/ClosedXML/blob/develop/LICENSE)、[NuGet](https://www.nuget.org/packages/ClosedXML) |

### 1.4 IDML

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **自寫 IDML 寫出器** | 從零產生 IDML | 自有 | 不適用 | 可 | 可 | IDML 是 ZIP 封裝的一組 XML，可以不靠 InDesign 產生 | Adobe 現在沒有公開提供完整規格文件（見 3.7）；要靠 2012 年的舊規格、由 InDesign 匯出的範例檔與產生的 RELAX NG 綱要來對照；必須用真的 InDesign 驗證 | 不適用（做法，非元件） | 見 3.7 |
| **SimpleIDML 1.3.1** | 操作既有的 IDML 封裝（Python） | BSD-3-Clause | 不適用 | 可 | 可，需附授權文 | 探索封裝結構、把多個 IDML 組合成一份、匯入與匯出 XML 內容、從目錄建立封裝；2012 年起維護，README 說 Le Figaro 用於正式環境 | 定位是「拿設計師做好的範本來組合與填內容」，不是從版面模型從零產生文件 | 是（授權與 README 直讀；未實測） | [LICENSE](https://github.com/Starou/SimpleIDML/blob/master/LICENSE)、[README](https://github.com/Starou/SimpleIDML/blob/master/README.rst)、[PyPI](https://pypi.org/project/SimpleIDML/) |
| **idml 0.1.73**（npm，ElyaConrad/idml） | 讀寫 IDML、SVG 轉 IDML（TypeScript） | ISC（package.json 的欄位；儲存庫根目錄沒找到 LICENSE 檔） | 不適用 | 可（ISC 為寬鬆授權） | 可 | 可建立文字框、多邊形、矩形、圖片、群組；可設字型、字級、字距、行距、顏色；內含 `svg2idml` 轉換 | 作者自述「只涵蓋 IDML 的一小部分」；遇到遮罩與濾鏡會點陣化；單一維護者 | 是（npm 中繼資料與 README 直讀；未實測） | [npm](https://www.npmjs.com/package/idml)、[README](https://github.com/ElyaConrad/idml/blob/main/README.md) |
| **CoreIdml 1.1.1** | 建立、讀取、填寫、編輯 IDML，並可輸出 PDF（.NET 引擎，另有 Node、WASM、Python 綁定） | MIT | 不適用 | 可 | 可 | README 自述可從零建立文件（頁面、文字、表格、圖片、形狀、樣式、顏色），新文件可被 CS5.5 以後的 InDesign 開啟 | **npm 與 PyPI 上的第一個版本發布於 2026-11-18，也就是查證日的兩天前**，沒有任何使用紀錄可參考；各語言綁定用的是預先編譯的原生函式庫；一切能力都是作者自述，未實測 | 部分（授權與 README 直讀；能力未驗證） | [LICENSE](https://github.com/eduardpaul/CoreIdml/blob/main/LICENSE)、[README](https://github.com/eduardpaul/CoreIdml/blob/main/README.md)、[npm](https://www.npmjs.com/package/@coreidml/node) |
| idmlkit 0.1.0 | 讀取 IDML 摘要、改文字、換圖片連結（Node） | MIT | 不適用 | 可 | 可 | 局部修改，其餘位元組不動 | 只能改既有檔，不能從零產生；只有一個版本（2026-07-18） | 部分（npm 中繼資料與 README） | [npm](https://www.npmjs.com/package/idmlkit) |
| idml2xml（transpect） | IDML 轉成 XML（XSLT 函式庫） | BSD 二條款樣式（LICENSE.md 內文） | 不適用 | 可 | 可 | 把 IDML 轉成 Hub XML 或抽出標記 | 只讀不寫，方向與本產品相反；可當作理解 IDML 結構的參考 | 部分（授權與 README） | [LICENSE.md](https://github.com/transpect/idml2xml/blob/master/LICENSE.md)、[README](https://github.com/transpect/idml2xml/blob/master/README.md) |
| postext-idml 0.16.1 | Postext 排版系統的 IDML 匯出入（TypeScript） | MIT | 不適用 | 可 | 可 | 把 Postext 排好的書寫成 IDML，可選保留斷行或交給 InDesign 重排 | 輸入是 Postext 自己的文件模型，不是通用寫出器；可當實作參考 | 部分（npm 中繼資料與 README） | [npm](https://www.npmjs.com/package/postext-idml) |

### 1.5 需付費，或排除

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| Aspose.Slides、Aspose.Words、Aspose.Cells | PPTX、DOCX、XLSX 讀寫與轉檔（多語言） | 專有授權（PyPI 欄位標示為 Proprietary，指向 Aspose 的使用者授權合約） | 不適用 | **需付費** | **需付費**，散布條款未查 | 未查 | 價格與散布條款未查 | 部分（只查 PyPI 授權欄位） | [aspose-slides](https://pypi.org/project/aspose-slides/)、[aspose-words](https://pypi.org/project/aspose-words/)、[aspose-cells](https://pypi.org/project/aspose-cells/) |
| @imgly/idml-exporter 0.1.1 | 把 CE.SDK 場景匯出成 IDML | 專有授權；README 明講「需要有效的 CE.SDK 授權」 | 不適用 | **需付費** | **需付費** | README 自述產出的檔案通過 Adobe IDML 綱要驗證，並在 InDesign 2026 驗證過 | 輸入必須是 CE.SDK 場景，等於要採用對方的整套編輯引擎 | 部分（npm 中繼資料與 README） | [npm](https://www.npmjs.com/package/@imgly/idml-exporter) |
| @paged-media/paged 0.74.2 | IDML 檢視與編輯引擎 | AGPL-3.0-only，或另購商業授權 | 不適用 | AGPL 版**排除**；商業版需付費 | 同左 | 未查 | 依授權底線，只有購買商業授權才可用 | 部分（只查 npm 授權欄位） | [npm](https://www.npmjs.com/package/@paged-media/paged) |
| Adobe InDesign API（Firefly Services） | 雲端自動化 InDesign（資料合併、產生輸出檔等） | 雲端服務 | 不適用 | **需付費**，且論壇有人回報需要企業帳號 | 不適用（雲端服務，無法打包） | 官方說明為以 REST 介面自動化設計工作 | 是否能把 PDF 或 IDML 轉成 INDD、計價、申請資格，都沒查到 | 否（只有搜尋摘要） | [官方文件](https://developer.adobe.com/firefly-services/docs/indesign-apis)、[論壇](https://community.adobe.com/questions-404/how-can-i-get-access-to-the-indesign-api-firefly-services-for-a-poc-1642371) |

### 1.6 輔助

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| LibreOffice | 把產出的 PPTX、DOCX 轉成 PDF 或影像，供還原度比對（伺服器端） | 儲存庫內同時有 MPL-2.0、LGPL-3.0、GPL-3.0 三份授權文 | 不適用 | 伺服器端使用未見限制 | **未確認**。三份授權如何適用、內含的第三方元件授權，本次沒有查清楚，打包進桌面版前要另查 | 未查 | 排版結果不等於 PowerPoint 或 Word 的排版結果，只能當近似 | 否（只確認授權檔存在） | [COPYING.MPL](https://github.com/LibreOffice/core/blob/master/COPYING.MPL)、[COPYING.LGPL](https://github.com/LibreOffice/core/blob/master/COPYING.LGPL)、[COPYING](https://github.com/LibreOffice/core/blob/master/COPYING) |

## 2. 需求對照

### 2.1 PPTX

| 需求 | python-pptx | PptxGenJS | 說明 |
|---|---|---|---|
| 建立母片與版面配置 | 無介面 | 有（`defineSlideMaster`） | python-pptx 只能沿用範本檔裡既有的母片與版面配置。「模板」輸出需要自訂版面骨架，要自己寫 XML 或事先準備範本檔 |
| 東亞字型欄位 | 不寫，可自行補 XML | 會寫，但與拉丁欄位同名 | 中英文混排要分別指定字型時，兩者都得自己處理 |
| 自動縮放比例 | 不算 | 不算 | 兩者都只寫空的 `normAutofit`。縮放比例要自己算了寫進去，或乾脆直接寫定字級 |
| 漸層 | 線性、改既有色標；增減色標要自行補 XML | 無 | |
| 刪除線、上下標、字距 | 無介面，可自行補屬性 | 未查 | 3.14 實測可寫入 |
| 內嵌字型 | 無 | 無 | 見 3.12 |

### 2.2 DOCX

| 需求 | python-docx | docx（npm） | 說明 |
|---|---|---|---|
| 可重排模式（段落、表格、行內圖片、分節） | 有 | 有 | python-docx 原生就是這個模式 |
| 版面固定模式：文字方塊 | 無介面 | 有 | python-docx 要自己寫整段繪圖 XML |
| 版面固定模式：浮動定位圖片 | 無介面（官方文件明講只支援行內圖片） | 有 | |
| 分欄 | 無介面，可自行補屬性 | 有 | 3.14 實測可寫入 |
| 東亞字型欄位 | 不寫，可自行補屬性 | 有 | 3.14 實測可寫入 |
| 內嵌字型 | 無 | 有 | 見 3.12 |

## 3. 逐條事實與來源

### 3.1 python-pptx

1. 授權為 MIT，著作權人 Steve Canny。【直讀】[LICENSE](https://github.com/scanny/python-pptx/blob/master/LICENSE)、[PyPI](https://pypi.org/project/python-pptx/)
2. 最新版 1.0.2，上傳於 2024-08-07。前幾版為 1.0.1（2024-08-05）、1.0.0（2024-08-03）、0.6.23（2023-11-02）。【直讀】[PyPI](https://pypi.org/project/python-pptx/)、[HISTORY.rst](https://github.com/scanny/python-pptx/blob/master/HISTORY.rst)
3. 主分支與開發分支指向同一個提交（`278b47b`，2024-08-06），之後沒有新標籤。到查證日為止，超過 27 個月沒有新提交。【直讀】用 git 讀取 [儲存庫](https://github.com/scanny/python-pptx) 的分支與標籤
4. 相依套件：Pillow（MIT-CMU）、XlsxWriter（BSD-2-Clause）、lxml（BSD-3-Clause）、typing-extensions（PSF-2.0）。授權皆為寬鬆授權。【直讀】各套件的 PyPI 中繼資料
5. **不能建立母片與版面配置。** 母片集合沒有任何新增方法；版面配置集合只有依名稱查找、取索引、移除三個方法。`Presentation()` 不帶參數時開啟內建範本，內含 1 個母片與 11 個版面配置，尺寸 9144000 × 6858000 EMU（10 × 7.5 英吋，4:3）。【實測】；移除版面配置的說明見 [sld-layout.rst](https://github.com/scanny/python-pptx/blob/master/docs/dev/analysis/sld-layout.rst)【直讀】
6. **字型名稱只寫拉丁欄位。** `Font.name` 的設定函式只建立 `a:latin` 元素。實測把含中文的文字設定字型後，XML 裡只有 `<a:latin typeface="…"/>`，沒有 `a:ea`。開發文件把 PowerPoint 物件模型的 `Font.NameFarEast`、`Font.NameComplexScript` 列為相關項目，但實作的介面只有 `name`。【直讀加實測】[text.py](https://github.com/scanny/python-pptx/blob/master/src/pptx/text/text.py)、[txt-font-typeface.rst](https://github.com/scanny/python-pptx/blob/master/docs/dev/analysis/txt-font-typeface.rst)
7. 字型物件的公開屬性只有：粗體、顏色、填滿、斜體、語言、名稱、字級、底線。沒有刪除線、上下標、字距、大小寫轉換。【實測】
8. 段落物件有：對齊、階層、行距、段前、段後。沒有項目符號、縮排、定位點的介面。【實測】
9. **自動縮放只寫設定，不算比例。** 把文字框設為「縮小文字以符合圖形」，寫出的是 `<a:normAutofit/>`，沒有 `fontScale`。另有 `fit_text()` 方法，做法不同：用字型檔的度量算出最合適的字級，直接寫定字級，並把自動縮放關掉。開發文件明講「穩健的實作很複雜，會超出專案目前預定的範圍」。【直讀加實測】[txt-autofit-text.rst](https://github.com/scanny/python-pptx/blob/master/docs/dev/analysis/txt-autofit-text.rst)
10. PowerPoint 何時重算縮放比例：PptxGenJS 原始碼的註解寫「縮小不會自動生效，PowerPoint 在調整大小時才動態計算 `fontScale`」。【直讀】[gen-xml.ts](https://github.com/gitbrent/PptxGenJS/blob/master/src/gen-xml.ts)。LibreOffice 的一則提交說明寫 Office 需要 `fontScale` 才能正確顯示，且修改後會重算。【搜尋摘要】[提交紀錄](https://lists.freedesktop.org/archives/libreoffice-commits/2018-February/151957.html)。Microsoft 的實作說明寫 PowerPoint 在省略 `normAutofit` 時視為不自動縮放。【搜尋摘要】[MS-OI29500](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-oi29500/d39a4528-75c3-4542-88aa-db874e68085c)。**沒有用真的 PowerPoint 驗證。**
11. **漸層只有部分支援。** `fill.gradient()` 建立預設的兩色標線性漸層；可改角度、改每個色標的顏色與位置；色標集合是唯讀序列，沒有新增或移除的方法；遇到非線性漸層，讀寫角度會丟出例外。【直讀加實測】[fill.py](https://github.com/scanny/python-pptx/blob/master/src/pptx/dml/fill.py)
12. 線條只有顏色、虛線樣式、填滿、寬度，沒有箭頭、端點、接合。陰影只有「是否繼承」一個開關。顏色只有 RGB、佈景主題色、亮度，沒有透明度。【實測】
13. 圖形集合可加入：文字方塊、自動圖形、圖片、表格、圖表、連接線、群組、自由曲線、影片、OLE 物件。表格儲存格可合併與分割。圖片可裁切。【實測】；圖片裁切見 [picture.py](https://github.com/scanny/python-pptx/blob/master/src/pptx/shapes/picture.py)【直讀】
14. 可接受的圖片類型：bmp、emf、gif、jpeg、png、tiff、wdp、wmf。沒有 SVG。【直讀】[spec.py](https://github.com/scanny/python-pptx/blob/master/src/pptx/opc/spec.py)
15. SmartArt「尚未支援，但既有的會保留」。【直讀】[understanding-shapes.rst](https://github.com/scanny/python-pptx/blob/master/docs/user/understanding-shapes.rst)
16. 投影片集合只有新增、依編號取得、取索引，沒有刪除、複製、重新排序的介面。【實測】
17. 原始碼裡沒有任何內嵌字型的實作（搜尋 `embeddedFont` 無結果）。【直讀】
18. **可以繞過介面直接補 XML。** 實測用 lxml 在文字的屬性節點補上 `a:ea`、字距、刪除線、上標，在自動縮放設定補上 `fontScale`，在漸層插入第三個色標，檔案可寫出，再用 python-pptx 讀回無誤。這只證明檔案寫得出來，**不代表 PowerPoint 一定接受**。【實測】

### 3.2 PPTX 的其他函式庫

1. PptxGenJS：授權 MIT。最新版 4.0.1，發布於 2025-06-26；主分支最後提交於 2025-06-25。【直讀】[LICENSE](https://github.com/gitbrent/PptxGenJS/blob/master/LICENSE)、[npm](https://www.npmjs.com/package/pptxgenjs)
2. PptxGenJS 有 `defineSlideMaster` 可定義母片。【直讀】[pptxgen.ts](https://github.com/gitbrent/PptxGenJS/blob/master/src/pptxgen.ts)
3. PptxGenJS 設定 `fontFace` 時，同時寫 `a:latin`、`a:ea`、`a:cs` 三個元素，三者填同一個字型名稱。【直讀】[gen-xml.ts](https://github.com/gitbrent/PptxGenJS/blob/master/src/gen-xml.ts)
4. PptxGenJS 的圖形填滿型別只有 `none` 與 `solid`。原始碼裡出現漸層的地方只有內建佈景主題的固定 XML，沒有讓使用者指定漸層填滿的介面。【直讀】[core-interfaces.ts](https://github.com/gitbrent/PptxGenJS/blob/master/src/core-interfaces.ts)
5. PptxGenJS 原始碼裡沒有內嵌字型的實作。【直讀】
6. PptxGenJS 的相依套件 jszip 授權為「MIT 或 GPL-3.0-or-later」，可選 MIT。【直讀】[npm](https://www.npmjs.com/package/jszip)
7. Open XML SDK：授權 MIT，屬 .NET Foundation 專案。NuGet 上最新穩定版 3.5.1，發布於 2026-03-18。README 自述：依照 Microsoft Office 對 ISO 29500 的實作而設計，但「無意直接提供高階抽象」；使用前提是「詳細了解相關格式與標準」。內含 `OpenXmlValidator` 類別。【直讀】[LICENSE](https://github.com/dotnet/Open-XML-SDK/blob/main/LICENSE)、[README](https://github.com/dotnet/Open-XML-SDK/blob/main/README.md)、[OpenXmlValidator.cs](https://github.com/dotnet/Open-XML-SDK/blob/main/src/DocumentFormat.OpenXml.Framework/Validation/OpenXmlValidator.cs)
8. ShapeCrawler：授權 MIT，NuGet 最新版 0.82.0（2026-10-18），自述建在 Open XML SDK 之上。【直讀】[LICENSE](https://github.com/ShapeCrawler/ShapeCrawler/blob/master/LICENSE)、[README](https://github.com/ShapeCrawler/ShapeCrawler/blob/master/README.md)
9. Apache POI：授權 Apache-2.0。Maven Central 上 poi-ooxml 的最新版為 6.0.0（中繼資料更新於 2026-10-22），儲存庫有對應標籤。搜尋摘要說最新版是 5.5.1（2025-11-30），是過期資訊，以直讀為準。【直讀】[LICENSE](https://github.com/apache/poi/blob/trunk/legal/LICENSE)、[Maven 中繼資料](https://repo1.maven.org/maven2/org/apache/poi/poi-ooxml/maven-metadata.xml)
10. pptx-automizer 0.9.4（MIT，2026-09-28）：以既有 PPTX 為範本做合併與修改的 Node 套件，相依 PptxGenJS。只查了 npm 中繼資料。【直讀】[npm](https://www.npmjs.com/package/pptx-automizer)

### 3.3 python-docx

1. 授權為 MIT，著作權人 Steve Canny。【直讀】[LICENSE](https://github.com/python-openxml/python-docx/blob/master/LICENSE)
2. 最新版 1.2.0，上傳於 2025-06-16，內容是新增註解支援、停止支援 Python 3.8。前一版 1.1.2 在 2024-05-01。主分支最後提交於 2025-06-16，到查證日約 17 個月沒有新提交。【直讀】[PyPI](https://pypi.org/project/python-docx/)、[HISTORY.rst](https://github.com/python-openxml/python-docx/blob/master/HISTORY.rst)
3. 相依套件：lxml（BSD-3-Clause）、typing-extensions（PSF-2.0）。【直讀】PyPI 中繼資料
4. **圖片只支援行內。** 官方文件原文的意思是：撰寫當時 python-docx 只支援行內圖片；浮動圖片可以再加，有實際需求請到議題區提出。【直讀】[shapes.rst](https://github.com/python-openxml/python-docx/blob/master/docs/user/shapes.rst)
5. 原始碼認得 `wp:anchor`（浮動圖形的容器），但只用於「判斷繪圖裡有沒有圖片」與讀出圖片，沒有建立浮動圖形的介面。【直讀】[drawing/\_\_init\_\_.py](https://github.com/python-openxml/python-docx/blob/master/src/docx/drawing/__init__.py)
6. **沒有文字方塊介面。** 文件物件的公開方法只有新增段落、標題、表格、圖片、分頁、分節、註解。原始碼裡沒有產生文字方塊內容的程式。【實測加直讀】
7. **分節沒有分欄介面。** 分節物件的屬性只有邊界、頁首頁尾、裝訂邊、方向、紙張大小、起始類型。內建範本的分節設定裡有 `<w:cols w:space="720"/>`，但沒有對應的介面。【實測】
8. **字型名稱只寫西文欄位。** 設定 `font.name` 後，XML 是 `<w:rFonts w:ascii="…" w:hAnsi="…"/>`，沒有 `w:eastAsia`。【實測】
9. 字型物件的屬性比 python-pptx 完整：有刪除線、雙刪除線、上下標、全大寫、小型大寫、醒目提示色、隱藏等。段落格式有縮排、首行縮排、行距、段前段後、定位點、段落不分頁等。【實測】
10. 內建範本的頁面是 12240 × 15840 twip（Letter），上下邊界 1440、左右邊界 1800。【實測】
11. 可以繞過介面直接補 XML：實測補上 `w:eastAsia` 與 `w:num="2"`（兩欄），檔案可寫出。同樣**不代表 Word 的顯示結果已驗證**。【實測】

### 3.4 DOCX 的其他函式庫

1. docx（dolanmiu/docx）：授權 MIT。最新版 9.16.0，發布於 2026-11-19，主分支當天有提交，共發布過 116 個版本。【直讀】[LICENSE](https://github.com/dolanmiu/docx/blob/master/LICENSE)、[npm](https://www.npmjs.com/package/docx)
2. docx 的文件列有浮動圖片：給圖片 `floating` 選項即可指定位移、錨點鎖定與文繞圖。【直讀】[images.md](https://github.com/dolanmiu/docx/blob/master/docs/usage/images.md)
3. docx 的文件有文字方塊、文字框兩類說明頁（`text-box.md`、`wps-text-box.md`、`text-frames.md`），原始碼有對應目錄。【直讀】[docs/usage](https://github.com/dolanmiu/docx/tree/master/docs/usage)
4. docx 支援分欄：分節屬性可設欄數、欄間距、分隔線、是否等寬，欄間距單位為 twip。【直讀】[columns.md](https://github.com/dolanmiu/docx/blob/master/docs/usage/columns.md)
5. docx 支援內嵌字型，並實作了混淆處理（見 3.12）。【直讀】[fonts.md](https://github.com/dolanmiu/docx/blob/master/docs/usage/fonts.md)、[obfuscate-ttf-to-odttf.ts](https://github.com/dolanmiu/docx/blob/master/src/file/fonts/obfuscate-ttf-to-odttf.ts)
6. docx4j：授權 Apache-2.0，Maven Central 最新版 17.3.6（中繼資料更新於 2026-11-08）。【直讀】[LICENSE](https://github.com/plutext/docx4j/blob/master/legals/LICENSE)、[Maven 中繼資料](https://repo1.maven.org/maven2/org/docx4j/docx4j-core/maven-metadata.xml)
7. docxtpl 0.20.2（Python 的範本填寫工具）授權為 LGPL-2.1-only。不是 GPL 或 AGPL，沒有被授權底線排除，但本產品不需要範本填寫，不列入候選。【直讀】[PyPI](https://pypi.org/project/docxtpl/)

### 3.5 openpyxl 與 XlsxWriter

1. XlsxWriter：授權 BSD-2-Clause，著作權人 John McNamara。【直讀】[LICENSE.txt](https://github.com/jmcnamara/XlsxWriter/blob/main/LICENSE.txt)
2. XlsxWriter 最新版 3.3.3，上傳於 2026-11-20（查證當日），新增樞紐分析表。近一個月內有 3.3.0（10-22）、3.3.1（10-28）、3.3.2（11-19）、3.3.3 四個版本，維護活躍。沒有任何相依套件。【直讀】[PyPI](https://pypi.org/project/XlsxWriter/)、[Changes](https://github.com/jmcnamara/XlsxWriter/blob/main/Changes)
3. XlsxWriter **只能建立新檔，不能讀取或修改既有的 XLSX**。【直讀】[introduction.rst](https://github.com/jmcnamara/XlsxWriter/blob/main/dev/docs/source/introduction.rst)
4. XlsxWriter 的功能清單：完整格式、合併儲存格、已定義名稱、圖表、自動篩選、資料驗證與下拉清單、條件式格式、表格、樞紐分析表、圖片、多格式字串、儲存格註解、文字方塊、巨集、省記憶體模式。【直讀】[README](https://github.com/jmcnamara/XlsxWriter/blob/main/README.rst)
5. XlsxWriter 的 `autofit()` 是模擬：文件說它沒有 Excel 執行時的字型度量函式，改用從 Excel 取得的度量表估算；以 Calibri 11 為準，其他字型或字級不準；不考慮數字與日期格式。【直讀】[worksheet.rst](https://github.com/jmcnamara/XlsxWriter/blob/main/dev/docs/source/worksheet.rst)
6. openpyxl：授權 MIT。最新版 3.1.5，上傳於 2024-06-28，到查證日約 29 個月沒有新版。相依 et-xmlfile（MIT）。【直讀】[PyPI](https://pypi.org/project/openpyxl/)，以及輪檔內的 LICENCE.rst
7. openpyxl 自述為讀寫 Excel 2010 的 xlsx、xlsm、xltx、xltm 檔。套件內有樣式、合併、資料驗證、條件式格式、圖表、圖片、表格、列印設定等模組。【直讀】輪檔內的 METADATA 與模組目錄
8. 兩者寫出的欄寬不同，見 3.6 第 6 點。【實測】

### 3.6 Excel 欄寬單位

1. ECMA-376 對 `col` 元素 `width` 屬性的定義（轉述自 liborcus 專案的筆記，筆記引用規格原文）：欄寬以「一般樣式字型下，數字 0 到 9 之中最寬者的寬度」為單位，量的是字元數。另有 4 像素的邊距（左右各 2）加 1 像素的格線。【直讀間接來源】[liborcus 筆記](https://github.com/kohei-us/orcus/blob/master/misc/notes/column-width-and-row-height.txt)
2. 公式：`width = Truncate([字元數 × 最大數字寬度 + 5 像素] ÷ 最大數字寬度 × 256) ÷ 256`。以 Calibri 11 為例，96 dpi 下最大數字寬度為 7 像素，8 個字元寬的欄，值為 `Truncate([8×7+5]÷7×256)÷256 = 8.7109375`。【直讀間接來源】同上；Microsoft 的實作說明頁有相同條文【搜尋摘要】[MS-OI29500 第 18.3.1.13 節](https://learn.microsoft.com/en-us/openspecs/office_standards/ms-oi29500/727f4736-6cba-40e3-b4d3-ce319885780e)
3. 換回像素的公式：`Truncate(((256 × width + Truncate(128 ÷ 最大數字寬度)) ÷ 256) × 最大數字寬度)`，上例得 61 像素。Microsoft 的實作說明另寫 Office 要求 `width` 介於 0 到 255。【搜尋摘要】同上連結
4. 列高以點為單位，沒有邊距。【直讀間接來源】[liborcus 筆記](https://github.com/kohei-us/orcus/blob/master/misc/notes/column-width-and-row-height.txt)
5. Excel 介面上的預設欄寬是 8.43（Calibri 11）。XlsxWriter 的文件說欄寬單位是「預設字型下的字元數」，並說字串寬度與欄寬的實際關係很複雜。【直讀】[worksheet.rst](https://github.com/jmcnamara/XlsxWriter/blob/main/dev/docs/source/worksheet.rst)
6. **兩個函式庫對「欄寬 20」的處理不同。** XlsxWriter 的 `set_column(…, 20)` 把 20 當作字元數，先換成像素（20×7+5 = 145），再換成檔案裡的值，寫出 `width="20.7109375"`，與規格公式的結果一致。openpyxl 的 `column_dimensions["A"].width = 20` 原樣寫出 `width="20"`，不加邊距。所以同一個數字，openpyxl 產生的欄比 XlsxWriter 窄 5 像素。XlsxWriter 另有 `set_column_pixels()` 可直接給像素。【實測】；換算程式見 [worksheet.py](https://github.com/jmcnamara/XlsxWriter/blob/main/xlsxwriter/worksheet.py)【直讀】
7. 最大數字寬度取決於活頁簿的預設字型。SheetJS 的文件說它是「最大數字的寬度，通常 0 最寬」，並說可以不看字型、用來回換算的誤差反推出這個值。【直讀】[SheetJS 文件](https://github.com/SheetJS/sheetjs/blob/v0.18.5/docbits/62_colrow.md)
8. 設計含意：從影像量到的欄寬是像素或公釐，寫檔時要先定好預設字型（決定最大數字寬度），再換算。用 XlsxWriter 的像素介面最直接。

### 3.7 InDesign IDML 的規格文件

1. IDML 是 InDesign 的 XML 交換格式，是一個 ZIP 封裝，裡面主要是 XML 檔（跨頁、內文、樣式等）。【直讀】[SimpleIDML README](https://github.com/Starou/SimpleIDML/blob/master/README.rst)、[CoreIdml README](https://github.com/eduardpaul/CoreIdml/blob/main/README.md)
2. 封裝根目錄有 `designmap.xml` 作為索引，內文在 `Stories/Story_*.xml`，跨頁在 `Spreads/Spread_*.xml`。【直讀加第三方】[SimpleIDML README](https://github.com/Starou/SimpleIDML/blob/master/README.rst)、[neokapi 文件](https://neokapi.github.io/reference/formats/idml)（後者為搜尋摘要）
3. 封裝的 `mimetype` 項目要放在最前面且不壓縮。這是 CoreIdml 的 README 對「InDesign 會接受的輸出」的描述，不是 Adobe 的原文。【直讀第三方】[CoreIdml README](https://github.com/eduardpaul/CoreIdml/blob/main/README.md)
4. **Adobe 曾公開規格文件，但現在官方網址已失效。** 文件名稱為「IDML File Format Specification」，最後一版是 8.0 版，對應 InDesign CS6（2012 年）。原網址在 wwwimages.adobe.com 的 devnet 路徑下，論壇回報現在會回傳錯誤，8.0 版的 IDML 文件 PDF 已全數下架；2012 年之後沒有再更新。【搜尋摘要】[Adobe 論壇](https://community.adobe.com/questions-671/where-is-the-idml-specification-877459)、[另一串](https://community.adobe.com/questions-671/looking-for-the-latest-adobe-indesign-idml-file-format-specification-877328)
5. 規格文件現在在哪裡，**說法互相矛盾**。一說（2022 年，論壇版主轉述 Adobe 團隊）：IDML 文件已併入 InDesign SDK 的 HTML 文件（`docs/index.html`），約在 CC 早期從獨立 PDF 併入；IDML Cookbook 也在其中。另一說（社群專家）：SDK 裡沒有 IDML 規格；17.3 版的 SDK 還提到那份文件，但連結是壞的。我沒辦法下載 SDK 驗證。【搜尋摘要，互相矛盾】[Adobe 論壇](https://community.adobe.com/questions-671/where-is-the-idml-specification-877459)
6. 綱要檔可以由 InDesign 自己產生：指令碼方法 `app.generateIDMLSchema()` 會把 RELAX NG 綱要寫到指定資料夾，內容取決於當時載入的外掛。SDK 附的 `idml-schema.zip` 在 2019 年與 2024 年（SDK 19.3）都有人回報是空的。【搜尋摘要】[Adobe 論壇](https://community.adobe.com/questions-671/indesign-sdk-19-3-sdk-docs-references-idml-schema-zip-has-no-schema-files-889383)
7. 2025 年 8 月還有論壇使用者貼出在網路上找到的 2012 年 8.0 版規格。坊間流傳的副本是第三方代管，來源未經確認。【搜尋摘要】[Adobe 論壇](https://community.adobe.com/questions-671/where-is-the-idml-specification-877459)
8. 目前的 InDesign 是 21.0 版（2025 年 11 月的論壇貼文提到 InDesign 21.0）。IDML 根元素有 `DOMVersion` 屬性標示版本；第三方專案的範例檔用的是 `20.0`。**InDesign 21 寫出的 `DOMVersion` 值沒有查到。**【搜尋摘要】[Adobe 論壇](https://community.adobe.com/questions-671/xml-tag-colors-not-showing-in-layout-after-mapping-in-indesign-21-0-898218)
9. CS4 以後的任何 InDesign 版本都能開啟 IDML；InDesign 的另存選單裡，這個格式叫「InDesign CS4 or later (IDML)」。舊版開啟時，新版才有的功能會遺失。【搜尋摘要】[Adobe 公告](https://community.adobe.com/announcements-670/save-for-older-versions-with-idml-1629768)
10. **IDML 不能內嵌字型。** 這是 @imgly/idml-exporter 的 README 的說法；它的做法是依照 Adobe 的封裝慣例，在 IDML 旁邊放一個 `Document fonts` 資料夾，InDesign 開檔時會啟用裡面的字型。postext-idml 也用同樣的資料夾慣例。【直讀第三方】[@imgly/idml-exporter](https://www.npmjs.com/package/@imgly/idml-exporter)、[postext-idml](https://www.npmjs.com/package/postext-idml)
11. **InDesign 自己已經有「把 PDF 轉成可編輯 InDesign 文件」的功能。** 用「開啟」或拖曳 PDF 進 InDesign 即可轉成可編輯文件；Adobe 說可還原色票、段落與字元樣式、表格、自動編號；需要連網並登入 Adobe 帳號；建議系統裝有文件用到的字型；Adobe 自己的教學也提醒要比對斷行、文字流與圖片位置。Adobe 的說明頁標題仍標示為測試版（Beta）App 的功能，2025 年 10 月的論壇貼文提到 InDesign 20.5 已可用；是否已正式發布沒有查清楚。2024 年有使用者回報轉出的文字流破碎、有大量未串接的文字框。【搜尋摘要】[Adobe 說明](https://helpx.adobe.com/indesign/using/convert-pdf-to-indesign-file.html)、[Adobe 教學](https://www.adobe.com/learn/indesign/web/convert-pdf-to-indesign-document)

### 3.8 IDML 的開源函式庫

1. SimpleIDML：授權為 BSD 三條款（LICENSE 內文含「不得用作者名義背書」條款），著作權人 Stanislas Guerra。最新正式版 1.3.1（2025-08-15），PyPI 上另有 1.3.2a0 預覽版（2025-10-24）。支援 Python 3.9 以上。【直讀】[LICENSE](https://github.com/Starou/SimpleIDML/blob/master/LICENSE)、[PyPI](https://pypi.org/project/SimpleIDML/)
2. SimpleIDML 的定位：把多個 IDML 組合成複雜文件、把內容與結構分開，用 InDesign 的 XML 結構功能灌入內容。功能章節為：探索封裝、建立封裝（從目錄打包）、組合文件、匯入匯出 XML、匯入 PDF、透過 InDesign Server 的 SOAP 介面轉檔。【直讀】[README](https://github.com/Starou/SimpleIDML/blob/master/README.rst)
3. npm 的 `idml` 套件（ElyaConrad/idml）：package.json 的授權欄位為 ISC；最新版 0.1.73（2026-11-10），2024-11-16 建立，共 72 個版本。作者在 README 說明目標是做出 `svg2idml` 轉換器，並說「從 IDML 的角度看，這個函式庫只涵蓋一小部分」。儲存庫根目錄沒找到獨立的 LICENSE 檔。【直讀】[npm](https://www.npmjs.com/package/idml)、[README](https://github.com/ElyaConrad/idml/blob/main/README.md)
4. CoreIdml：授權 MIT，著作權年份 2026。npm 上 `@coreidml/node` 建立於 2026-11-18，兩天內發布了 8 個版本，最新 1.1.1；PyPI 上的 `coreidml` 1.1.1 發布於 2026-11-19。【直讀】[LICENSE](https://github.com/eduardpaul/CoreIdml/blob/main/LICENSE)、[npm](https://www.npmjs.com/package/@coreidml/node)、[PyPI](https://pypi.org/project/coreidml/)
5. 搜尋結果裡的「IDML/idml」（Java 與 Kotlin 引擎）全名是 Ingestion Data Mapping Language，是一種資料對映語言，**與 InDesign 無關**，不要誤用。【直讀】[readme](https://github.com/IDML/idml/blob/develop/readme.md)
6. npm 上 2026 年出現一批新的 IDML 套件（idmlkit、postext-idml、@coreidml、@imgly、@polotno 等），多數建立不到半年。這代表需求存在，但都還沒有可參考的使用紀錄。【直讀】npm 登錄站搜尋結果

### 3.9 Illustrator 開啟一般 PDF 時對文字的處理

**這一節沒有任何 Adobe 官方文件可引用，也沒有實測。以下全部是論壇、意見回報區與第三方的說法。**

1. 一致的說法：不是由 Illustrator 存出、且沒有保留編輯功能的 PDF，在 Illustrator 開啟後，連續的文字區塊會被拆成多個片段，常見是一行一個點狀文字物件，有時拆到單字或單一字元。2026 年針對 Illustrator 2026（30.3 版）的一則回報把這稱為「預期行為」。【搜尋摘要】[意見回報區](https://illustrator.uservoice.com/forums/601447-illustrator-desktop-bugs/suggestions/51150574-incorrect-text-order-when-copying-text-from-pdfs-w)、[Adobe 論壇](https://community.adobe.com/t5/illustrator-discussions/eps-import-breaks-text-into-characters/m-p/9197265)
2. 拆到逐字的情況與字距調整有關，但**因果關係沒有官方確認**。一則回報說 Illustrator 28 把部分文字拆成逐字的群組，同一份檔案在 Acrobat 裡是完整字串；留言者認為這反映 PDF 儲存文字的方式，並說把字距微調改成整體字距可以讓整行不被拆開。另一則論壇留言說，有字距微調時，文字會被切成小段以維持各段之間的距離。【搜尋摘要】[意見回報區](https://illustrator.uservoice.com/forums/601447-illustrator-desktop-bugs/suggestions/47534339-pdf-live-text-strings-are-broken-into-individual-c)
3. 同一則 2026 年的回報指出另一個問題：拆開後的片段順序不再是視覺上的閱讀順序。【搜尋摘要】[意見回報區](https://illustrator.uservoice.com/forums/601447-illustrator-desktop-bugs/suggestions/51150574-incorrect-text-order-when-copying-text-from-pdfs-w)
4. 字型：論壇說法是 Illustrator 不會用 PDF 內嵌的字型來編輯文字，系統必須裝有該字型，否則文字可能遺失或亂碼；只內嵌子集的字型會造成找不到字型的錯誤。【第三方】[PrintPlanet](https://printplanet.com/threads/illustrator-as-a-pdf-editor.293407/)、[Adobe 論壇](https://community.adobe.com/questions-652/importing-pdf-to-illustrator-fonts-displayed-as-boxes-with-and-x-788009)
5. 能在 Illustrator 裡完整編輯的 PDF，是 Illustrator 自己存出、且勾選「保留 Illustrator 編輯功能」的 PDF。Adobe 的說明頁寫這個選項會在 PDF 裡加入 PGF 資料。PGF 是 Illustrator 的私有資料，等於在 PDF 裡夾帶一份原生檔，第三方寫不出來。【搜尋摘要】[Adobe 說明](https://helpx.adobe.com/illustrator/using/pdf-options.html)
6. 沒有找到 Illustrator 有「開啟 PDF 時把逐行文字合併成段落」的功能。相關但不同的功能：Retype（28 版起的測試功能，把外框文字轉回可編輯文字，早期只支援單字與拉丁文字）、Reflow Viewer（29 版，處理舊文件的東亞文字排版）。第三方外掛 VectorFirstAid 可以把多個點狀文字合併成一個。【搜尋摘要】[Adobe 公告](https://community.adobe.com/announcements-651/convert-outline-text-to-editable-text-with-retype-beta-in-illustrator-805902)、[Adobe 說明](https://helpx.adobe.com/illustrator/using/reflow-viewer.html)、[Astute Graphics](https://astutegraphics.com/learn/tutorial/combine-and-repair-point-text-in-adobe-illustrator-with-vectorfirstaid)
7. **怎樣的 PDF 寫法能讓文字保持成段：查不到任何有根據的答案。** 依上面幾點只能推論出一個待驗證的假設：同一行用同一個文字物件、同一個字型、一次畫出整行字串，不要在字與字之間插入個別的位置調整；字型要是使用者裝得到的。即使假設成立，最好的結果也是「一行一個文字物件」，不是「一段一個文字框」。這個假設沒有任何來源背書，必須拿真的 Illustrator 測。

### 3.10 Illustrator 匯入 SVG 文字的行為

**同樣沒有 Adobe 官方文件，沒有實測。**

1. Adobe 的說明頁查不到 SVG 的 `text`、`tspan`、`font-family` 在匯入時如何處理的說明；找到的頁面講的是 SVG 濾鏡效果與一般文字檔的匯入。【搜尋摘要】[Adobe 說明](https://helpx.adobe.com/illustrator/using/svg.html)
2. 論壇回報：SVG 匯出、重新開啟、再匯出之後，原本的 `tspan` 不見了，全部變成各自獨立的文字。版主的回答是「SVG 不適合在 Illustrator 裡來回轉換」。【搜尋摘要】[Adobe 論壇](https://community.adobe.com/questions-652/svg-text-and-tspans-import-781054)
3. Illustrator 27.0 曾有退步：含 `tspan` 的簡單文字 SVG 置入後再匯出，文字消失；同一個檔案在 26.5 正常。【搜尋摘要】[Adobe 論壇](https://community.adobe.com/t5/illustrator-discussions/regression-in-versoin-27-0-svg-tspan-not-handled-correctly/m-p/13299536)
4. 論壇說 Illustrator 不使用 `text-anchor` 屬性（影響置中與靠右文字的位置）。【搜尋摘要】[Adobe 論壇](https://community.adobe.com/questions-652/specifying-text-alignment-within-svg-exports-808055)
5. 論壇說 Illustrator 沒有機制偵測 SVG 裡缺少的字型；舊版曾有 `font-family` 裡出現不認得的字型就開不了檔的問題。【搜尋摘要】[Adobe 論壇](https://community.adobe.com/questions-652/detect-font-substitution-in-svg-790507)
6. 結論：SVG 作為 Illustrator 的替代交付格式，文字行為比 PDF 更沒有資料，不能當作已知可行。

### 3.11 Google Slides 匯入 PPTX

1. 轉換成 Google 簡報的檔案上限是 100 MB。【搜尋摘要】[Google 雲端硬碟說明](https://support.google.com/drive/answer/37603)
2. 在雲端硬碟裡用 Google 簡報開啟 PowerPoint 檔時，變更會存回原本的 Office 檔（也就是可以不轉檔直接編輯）。【搜尋摘要】[Google 說明](https://support.google.com/docs/answer/9310378)
3. 用程式匯入的做法：上傳時把目標類型指定為 Google 簡報的 MIME 類型（`application/vnd.google-apps.presentation`），雲端硬碟會自動轉換。這是論壇引述官方文件與第三方工具的說法，官方頁面本身沒讀到。反方向，官方的匯出格式表列有 Google 簡報可匯出為 PPTX。【搜尋摘要】[匯出格式表](https://developers.google.com/workspace/drive/api/guides/ref-export-formats)、[第三方範例](https://labnol.org/code/20102-convert-office-files-to-google-docs)
4. **Google 沒有公布轉換的相容性對照表。** 沒有找到官方的「不支援功能清單」。以下失真項目都來自第三方文章，多數是廠商的部落格。【第三方】[pixelpresentation](https://pixelpresentation.com/blog/powerpoint-to-google-slides)
5. 字型：Google 簡報用的是網路字型庫；不在字型庫裡的字型會被替換（常見換成 Arial），斷行與間距跟著變。Google 簡報不能內嵌字型，也不能上傳自訂字型檔，PPTX 內嵌的字型會被丟掉並替換。【第三方】[BrightCarbon](https://www.brightcarbon.com/blog/google-slides-fonts/)、[slidemodel](https://slidemodel.com/fix-compatibility-powerpoint-google-slides/)
6. 開啟時若有不支援的功能，介面會顯示警告，並可查看受影響的項目清單。【第三方】[BrightCarbon](https://www.brightcarbon.com/blog/convert-powerpoint-google-slides/)
7. 常見失真（第三方說法，彼此不完全一致）：SmartArt 變成一般圖形或被壓平成圖片；連結 Excel 的圖表變成靜態圖片（有的來源說所有圖表都會）；轉場「轉化」、移動路徑與多數進階動畫不會保留，動畫時間被簡化；巨集與 3D 物件不保留；文字可能溢出、換行位置改變、項目符號縮排改變。【第三方】[BrightCarbon](https://www.brightcarbon.com/blog/convert-powerpoint-google-slides/)、[slidemodel](https://slidemodel.com/fix-compatibility-powerpoint-google-slides/)
8. 漸層：只有一個來源說漸層填滿會被壓成單色，沒有其他來源佐證。陰影：沒有任何來源提到。自動縮放設定與母片繼承在轉換後如何處理：沒有資料。【第三方，未證實】
9. 設計含意：本產品寫出的 PPTX 沒有 SmartArt、動畫、連結圖表，主要風險集中在字型替換造成的文字重排，以及漸層、陰影、自動縮放這幾項沒有資料的項目，都要實際匯入測試。

### 3.12 OOXML 的字型內嵌

1. Windows 版 PowerPoint 的設定位置：檔案、選項、儲存，勾選「在檔案內嵌字型」。【搜尋摘要】[Microsoft 支援](https://support.microsoft.com/en-us/office/fonts/benefits-of-embedding-custom-fonts)
2. Mac 版：Microsoft 的說明寫 Word for Microsoft 365 for Mac、PowerPoint for Microsoft 365 for Mac、PowerPoint 2024 for Mac、PowerPoint 2021 for Mac 可以內嵌字型。【搜尋摘要】同上
3. Mac 版從哪個版本開始支援，第三方說法不一：一說 16.17，一說 16.11。另有說法是 PowerPoint 2016 for Mac 不能內嵌，但能顯示新版內嵌的字型。【第三方，互相矛盾】[iSpring](https://www.ispringsolutions.com/blog/how-to-embed-fonts-in-powerpoint)、[verdanabold](https://www.verdanabold.com/post/embedding-fonts-in-powerpoint-not-just-for-pcs-any-more)
4. Microsoft 建議：要給別人編輯時，內嵌字型的全部字元，不要只內嵌用到的字元；盡量用 OpenType（.otf）或 TrueType（.ttf），避免 PostScript 字型。【搜尋摘要】[Microsoft 支援](https://support.microsoft.com/en-us/office/fonts/benefits-of-embedding-custom-fonts)
5. 不是所有字型都能內嵌。字型製作者可設定內嵌權限：不可內嵌、預覽與列印、可編輯、可安裝。【搜尋摘要】同上
6. OpenType 字型能不能內嵌，**說法互相矛盾**：Microsoft 的說明建議用 .otf 或 .ttf；一則較舊的 Microsoft 問答說 Office 不能內嵌 Adobe 的 OpenType 字型；一個第三方來源說只有 .ttf 能內嵌。【搜尋摘要，互相矛盾】[Microsoft 問答](https://learn.microsoft.com/en-us/answers/questions/5386781/how-to-embed-font-files-into-powerpoint-for-micros)
7. Office 的雲端字型不需要內嵌，支援的 App 會自動下載。【搜尋摘要】[Microsoft 支援](https://support.microsoft.com/en-us/office/fonts/cloud-fonts-in-office)
8. **網頁版與行動版是否顯示內嵌字型：沒有找到官方說法。** 搜尋結果中有一篇第三方指南轉述 Microsoft 支援團隊，說 PowerPoint 網頁版不支援內嵌或自訂字型（搜尋摘要沒有指明是哪一篇）；另一篇第三方指南說內嵌字型的文件可在 Office 支援的任何平台檢視。兩者相反，都不是官方頁面。Microsoft 的「缺少字型」說明頁把 Word 網頁版與 PowerPoint 網頁版列為會出現缺字型通知的 App。【第三方，互相矛盾】[memory.art 指南](https://www.memory.art/guides/how-to-embed-fonts-in-powerpoint)、[Microsoft 支援](https://support.microsoft.com/en-gb/topic/missing-font-5ebc7afd-ab95-4d96-b675-149d48263d84)
9. Word 的內嵌字型也可能不生效：Microsoft 問答有使用者回報，內嵌的字型在別台電腦的 Word 裡顯示成替代字型，而同樣做法的 PowerPoint 檔正常。【搜尋摘要】[Microsoft 問答](https://learn.microsoft.com/en-us/answers/questions/5196389/microsoft-word-cannot-display-embedded-font)
10. PPTX 的檔案結構：`presentation.xml` 裡的 `p:embeddedFontLst` 列出內嵌字型，每一筆用關聯編號指向字型資料；字型檔的副檔名是 `.fntdata`，內容類型為 `application/x-fontdata`。python-pptx 的內容類型表有這個副檔名。【搜尋摘要加直讀】[Microsoft 規格說明](https://msdn.microsoft.com/en-us/library/ff535081(v=office.12))、[spec.py](https://github.com/scanny/python-pptx/blob/master/src/pptx/opc/spec.py)
11. `.fntdata` 的內容：維基百科說 PowerPoint 2007 與 2010 產生的是副檔名為 `.fntdata` 的 EOT（Embedded OpenType）檔。較新版本是否相同、PowerPoint 是否接受直接放入未轉換的 TrueType 檔，沒有查到。【第三方】[維基百科](https://en.wikipedia.org/wiki/Embedded_OpenType)
12. DOCX 的檔案結構：字型以 `.odttf` 檔存放，內容是經過混淆的字型檔。docx 函式庫的實作是把字型檔的前 32 個位元組，與由字型 GUID 反向排列得到的位元組做互斥或運算；原始碼註解引用 ECMA-376 第 2 部分第 11.1 節。【直讀】[obfuscate-ttf-to-odttf.ts](https://github.com/dolanmiu/docx/blob/master/src/file/fonts/obfuscate-ttf-to-odttf.ts)
13. 函式庫支援：python-pptx、python-docx、PptxGenJS 都沒有內嵌字型的實作；docx（npm）有。【直讀】各自的原始碼
14. LibreOffice：新聞報導說 25.8 版的 Impress 加入了 PPTX 內嵌字型的支援；存成 PPTX 時不內嵌字型的問題回報仍是未解決狀態。【搜尋摘要】[Neowin](https://www.neowin.net/news/libreoffice-kills-32-bit-windows-builds-offers-better-ms-office-compatibility-with-258/)、[問題回報 106771](https://www.libreoffice.org/bugzilla/show_bug.cgi?id=106771)
15. 設計含意：內嵌字型只在桌面版 Office 上有官方說明可依靠，網頁版、行動版、Google 簡報都不能指望。而且內嵌受字型授權的權限位元限制。

### 3.13 OOXML 的單位

1. **EMU**（English Metric Unit）：1 英吋 = 914400 EMU，1 公分 = 360000 EMU，1 公釐 = 36000 EMU，1 點 = 12700 EMU，1 twip = 635 EMU。這樣設計是為了讓英制與公制都能整除。【直讀】[python-pptx util.py](https://github.com/scanny/python-pptx/blob/master/src/pptx/util.py)、[python-docx shared.py](https://github.com/python-openxml/python-docx/blob/master/src/docx/shared.py)；ECMA-376 附錄 L 的定義為搜尋摘要 [中譯](https://hellowac.github.io/ecma-376-zh-cn/ecma-part1/annexL/drawingml/basics)；Google 簡報介面的單位定義相同 [Slides API](https://developers.google.com/workspace/slides/reference/rest/v1/Unit)【搜尋摘要】
2. 1 像素 = 9525 EMU，前提是 96 dpi。【直讀】openpyxl 輪檔內的 `utils/units.py`
3. **twip**（OOXML 裡也寫作 dxa）：二十分之一點，也就是 1/1440 英吋。720 twip = 半英吋 = 36 點。WordprocessingML 的頁面大小、邊界、縮排、欄間距用這個單位。【直讀加搜尋摘要】同上的 `units.py`；[ST_TwipsMeasure](https://webapp.docx4java.org/OnlineDemo/ecma376/WordML/ST_TwipsMeasure.html)
4. **半點**：WordprocessingML 的字級（`w:sz`）以半點為單位，值 24 代表 12 點。【直讀】[python-docx simpletypes.py](https://github.com/python-openxml/python-docx/blob/master/src/docx/oxml/simpletypes.py)
5. 八分之一點：WordprocessingML 的框線寬度用這個單位，值 24 代表 3 點。【搜尋摘要】[ST_EighthPointMeasure](https://webapp.docx4java.org/OnlineDemo/ecma376/WordML/ST_EighthPointMeasure.html)
6. 百分之一點：DrawingML 的字級（`a:rPr` 的 `sz`）以百分之一點為單位，值 1800 代表 18 點；python-pptx 限制範圍為 100 到 400000。【直讀加實測】[python-pptx simpletypes.py](https://github.com/scanny/python-pptx/blob/master/src/pptx/oxml/simpletypes.py)
7. 角度：DrawingML 以六萬分之一度為單位。【直讀】同上
8. 百分比：DrawingML 以千分之一個百分點為單位，100000 代表 100%（例如漸層色標位置、`fontScale`）。【直讀】同上
9. 同一份 DOCX 裡會同時出現兩套單位：文字層用 twip 與半點，繪圖層（圖片、文字方塊的位置與大小）用 EMU。docx 函式庫的文件也提醒，圖片的寬高用像素（96 dpi），浮動位移用 EMU。【直讀】[images.md](https://github.com/dolanmiu/docx/blob/master/docs/usage/images.md)
10. 標準本身：ECMA-376 第 5 版（2021 年 12 月），共四部分，Ecma 網站提供免費下載；對應的 ISO/IEC 29500-1 現行版為 2016 年版，新版在核准階段。【搜尋摘要】[Ecma](https://ecma-international.org/publications-and-standards/standards/ecma-376/)、[ISO](https://www.iso.org/standard/85626.html)
11. Excel 的欄寬與列高單位見 3.6。

### 3.14 實測紀錄

環境：Python 3.13 的暫存虛擬環境，安裝 python-pptx 1.0.2、python-docx 1.2.0、openpyxl 3.1.5、XlsxWriter 3.3.3。測試程式與輸出檔都放在工作階段的暫存目錄，沒有寫進專案。

| 測試 | 結果 |
|---|---|
| 列出 python-pptx 各物件的公開成員 | 母片集合沒有新增方法；字型只有 8 個屬性；填滿有漸層相關成員；陰影只有 `inherit` |
| python-pptx 設定字型名稱後檢視 XML | 只有 `<a:latin typeface="Noto Sans TC"/>` |
| python-pptx 設定「縮小文字以符合圖形」後檢視 XML | `<a:bodyPr wrap="none"><a:normAutofit/></a:bodyPr>` |
| python-pptx 建立漸層 | 預設 2 個色標，色標集合沒有新增方法 |
| python-pptx 用 lxml 補寫 `a:ea`、字距、刪除線、上標、`fontScale`、第三個色標 | 檔案可寫出並讀回；讀回後色標為 3 個，位置 0、0.5、1 |
| 列出 python-docx 各物件的公開成員 | 分節沒有分欄；文件沒有文字方塊；字型有二十多個格式屬性 |
| python-docx 設定字型名稱後檢視 XML | `<w:rFonts w:ascii="Noto Sans TC" w:hAnsi="Noto Sans TC"/>` |
| python-docx 用 lxml 補寫 `w:eastAsia` 與兩欄 | 檔案可寫出，XML 為 `<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Noto Sans TC"/>` 與 `<w:cols w:space="720" w:num="2"/>` |
| XlsxWriter `set_column(0, 0, 20)` | 寫出 `width="20.7109375"` |
| XlsxWriter `set_column_pixels(1, 1, 145)` | 寫出同一個值 |
| openpyxl `column_dimensions["A"].width = 20` | 寫出 `width="20"` |
| 兩者設列高 30 | 都寫出 `ht="30"` |
| 用規格公式計算 | 8 個字元得 8.7109375，換回 61 像素；20 個字元得 20.7109375，換回 145 像素 |

沒有做的事：沒有用任何目標軟體開啟這些檔案；沒有測 PptxGenJS、docx（npm）與任何 IDML 函式庫；沒有測效能。

## 4. 建議

以下是依授權底線與上面的事實提出的建議。主管線假設以 Python 為主（影像、版面、文字辨識的元件多為 Python）；若最後不是，PPTX 與 DOCX 的首選與備選可以對調。

### 4.1 共通原則：寫出器藏在自己的介面後面

每種輸出格式都定義一個自有的「寫出器」介面，上游只交出統一的版面描述。理由有三：python-pptx 與 python-docx 都已長時間沒有更新，要有換掉的餘地；兩者缺的功能要靠自己補 XML，這些補丁應該集中在一處；IDML 這一項還在等使用者確認，要能整塊拿掉。

### 4.2 PPTX

- **首選：python-pptx（MIT）加自寫的 XML 補丁層。** 它處理封裝、關聯、圖片、表格、群組、自由曲線這些繁瑣的部分；缺的東亞字型欄位、字距、刪除線、漸層色標、縮放比例，實測都能用 lxml 直接補進去。母片與版面配置不靠它建立，改用自己事先做好的範本檔，或自己寫母片與版面配置的 XML。版本要鎖定，並準備好自行維護分支，因為上游已超過兩年沒有提交。
- **自動縮放的處理：** 不要依賴 PowerPoint 開檔時重算。版面固定的輸出直接寫定字級並關掉自動縮放；需要讓使用者改字後仍會自動縮的情況，自己算出 `fontScale` 寫進去。這個做法的顯示結果要拿真的 PowerPoint 驗證。
- **備選一：PptxGenJS（MIT）。** 優點是能定義母片、會寫東亞字型欄位；缺點是沒有漸層、三個字型欄位只能同名、要多一個 Node 行程，而且最新版也已是 2025 年 6 月。適合「模板」輸出需要母片而不想自己寫 XML 的情況。
- **備選二：Open XML SDK（MIT）。** 涵蓋整個綱要，由 .NET Foundation 維護，2026 年仍有新版。代價是每個元素都要自己組，且要帶 .NET 執行環境。即使不拿它寫檔，也建議用它的驗證器在測試流程裡檢查產出的檔案。
- **需付費：** Aspose.Slides。只有在自寫補丁層的維護成本失控時才考慮。

### 4.3 DOCX

- **可重排模式首選：python-docx（MIT）。** 這本來就是它的模式（段落、表格、行內圖片、分節），東亞字型欄位與分欄用 lxml 補。
- **版面固定模式：python-docx 沒有文字方塊與浮動定位的介面，兩條路擇一。** 第一條是在 python-docx 的封裝上自己寫繪圖 XML（錨點、文字方塊），維持單一語言。第二條是改用 **docx（npm，MIT）**，它有浮動圖片、文字方塊、分欄與字型內嵌，而且維護非常活躍（查證前一天還在發版）。建議先用第一條做出最小可行版本；如果繪圖 XML 的相容性問題太多，就把 DOCX 的版面固定模式整個交給 docx（npm）。
- **備選：** Open XML SDK（同上，另可當驗證器）、docx4j（Apache-2.0，Java）。
- **需付費：** Aspose.Words。

### 4.4 XLSX

- **首選：XlsxWriter（BSD-2-Clause）。** 本產品只輸出表格區塊，只寫不讀就夠；它沒有相依套件、維護活躍，欄寬可以直接給像素，換算結果與規格公式一致。
- **備選：openpyxl（MIT）。** 需要讀取或修改既有 XLSX 時才用。要記得它的欄寬是原樣寫入，換算得自己做。
- 兩者都是寬鬆授權，可商用，可隨桌面版散布。

### 4.5 InDesign（IDML）

- **首選：自寫 IDML 寫出器。** 沒有一個寬鬆授權、有使用紀錄、能從版面模型從零產生 IDML 的函式庫。SimpleIDML 成熟但用途是組合既有範本；npm 的 `idml` 只涵蓋一小部分；CoreIdml 宣稱能從零建立，但發布才兩天。IDML 本身是 ZIP 加 XML，自己寫是可行的，做法是：以 InDesign 匯出的範例檔為基準，用 InDesign 產生的 RELAX NG 綱要驗證，並且每個版本都用真的 InDesign 開檔測試。
- **備選：** 過三到六個月再評估 CoreIdml（MIT）是否穩定下來；`idml`（npm，ISC）與 postext-idml（MIT）可當實作參考。
- **需付費：** @imgly/idml-exporter（要採用對方的編輯引擎）、@paged-media/paged 的商業授權、Adobe InDesign API。
- **字型：** IDML 不能內嵌字型。交付時要嘛附上 `Document fonts` 資料夾（只限授權允許散布的字型），要嘛在還原度報告列出需要安裝的字型。
- **產品面提醒：** InDesign 自己已經有把 PDF 轉成可編輯文件的功能。使用者確認這項輸出之前，應該先拿同一批檔案比較兩者的結果，確定本產品的 IDML 有明顯優勢再投入。

### 4.6 Illustrator（文字可編輯的 PDF）

- **目前不能對外承諾「在 Illustrator 裡文字成段可編輯」。** 所有來源都指向同一件事：一般 PDF 在 Illustrator 裡，文字最好的情況是一行一個物件，而且系統要裝有該字型。完整可編輯需要 Illustrator 的私有資料，第三方寫不出來。
- **建議的寫法（待驗證）：** 一行一個文字物件、一次畫出整行字串、不逐字調整位置、使用可自由取得的字型。寫 PDF 的函式庫見同目錄的 pdf.md。
- **上線前必做：** 用真的 Illustrator（至少最新版與前一版）測這個寫法，把實際結果寫進還原度報告的說明文字。若結果是逐行物件，對外說法應是「文字可編輯，以行為單位」。
- SVG 不建議當替代方案，資料比 PDF 更少。

### 4.7 Google Slides

- 沿用 PPTX 寫出器，用雲端硬碟的上傳轉換匯入。檔案要控制在 100 MB 以內。
- 給 Google 簡報的版本，字型只用 Google 字型庫裡有的，不要依賴內嵌字型。漸層、陰影、自動縮放在轉換後的結果沒有資料，要建立一組測試投影片實際匯入，把結果記成對照表，再決定哪些效果要在這個版本裡改用較保守的寫法。

### 4.8 字型內嵌

- 不要把內嵌字型當成排版一致的保證。只有桌面版 Office 有官方說明，網頁版、行動版、Google 簡報都靠不住，而且受字型授權限制。
- 主要手段應該是：選用可自由散布的字型、在檔案裡把拉丁與東亞字型欄位都寫對、在還原度報告列出用到的字型與替代關係。DOCX 若走 docx（npm）可以順便內嵌；PPTX 的內嵌要自己寫，且 `.fntdata` 的確切格式還沒查清楚，排在後面做。

## 5. 查不到的事

1. Illustrator 開啟一般 PDF 時，決定文字怎麼拆的確切規則。沒有 Adobe 官方文件，只有論壇與意見回報。
2. 怎樣的 PDF 寫法能讓 Illustrator 把文字保持成段或至少成行。沒有任何有根據的來源，3.9 第 7 點只是推論。
3. Illustrator 匯入 SVG 時對 `text`、`tspan`、字型對應的官方說明。
4. Google 官方的 PPTX 轉換相容性清單。似乎不存在。漸層、陰影、自動縮放、母片繼承、東亞字型欄位在轉換後的實際結果都沒有資料。
5. PowerPoint 網頁版、Word 網頁版、iOS 與 Android 版是否顯示內嵌字型。沒有官方說法，第三方說法相反。
6. Mac 版 PowerPoint 開始支援內嵌字型的確切版本號（16.11 或 16.17）。
7. PowerPoint 目前寫出的 `.fntdata` 是什麼格式，以及它是否接受第三方直接放入未轉換的 TrueType 或 OpenType 檔。
8. OpenType（PostScript 外框）字型在 Office 裡到底能不能內嵌。來源互相矛盾。
9. PowerPoint 開啟只有空白 `normAutofit`（沒有 `fontScale`）的檔案時，會不會在開檔當下縮小文字。兩個函式庫的原始碼與文件都暗示不會，但沒有用 PowerPoint 驗證。
10. 用 lxml 補寫的 XML（東亞字型、漸層色標、字距等）在 PowerPoint、Word、Keynote、LibreOffice、Google 簡報裡的實際顯示。只驗證了檔案寫得出來。
11. Adobe 目前是否在任何地方公開提供 IDML 規格文件，以及 InDesign SDK 裡到底有沒有。論壇說法矛盾，SDK 我下載不了。
12. InDesign 21（2026）寫出的 `DOMVersion` 值，以及 2012 年之後 IDML 新增了哪些元素與屬性。
13. InDesign 開啟「依 2012 年規格寫出」的 IDML 時，有沒有必填而舊規格沒寫的項目。
14. InDesign 的 PDF 轉換功能是否已正式發布（脫離測試版）、需要哪種訂閱、轉換品質如何。
15. Adobe InDesign API（Firefly Services）能否做 PDF 或 IDML 轉 INDD、計價方式、非企業帳號能否申請。
16. CoreIdml、`idml`（npm）、postext-idml 產出的 IDML 在 InDesign 裡的實際表現。全部未實測。
17. PptxGenJS 與 docx（npm）的實際輸出品質。只讀了原始碼與文件。
18. openpyxl 的維護現況（是否還有開發中的新版）。原始碼庫讀不到，只知道 PyPI 上 2024-06 之後沒有新版。
19. Aspose 各產品的價格與散布條款。
20. LibreOffice 打包進桌面版的授權條件（三份授權文如何適用、第三方元件清單）。
21. Apache POI、docx4j、ShapeCrawler、ClosedXML、ExcelJS、rust_xlsxwriter 的能力細節。只查了授權與版本。
22. ECMA-376 與 ISO/IEC 29500 的條文原文。網站讀不到，3.6 與 3.13 的條文都來自間接來源或函式庫原始碼。

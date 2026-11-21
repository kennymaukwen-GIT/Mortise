# 查證紀錄：現有產品基準（市場）

- 查證日期：2026-11-21（工作指派日為 2026-11-20）
- 適用產品：Mortise（榫）文件反編譯 SaaS，未來可能有桌面版
- 授權底線：所有元件必須可商用；排除 GPL、AGPL 元件（另有可購買商業授權者列為「需付費」）；排除非商用授權的模型權重
- 本篇目的：查出現有產品把 PDF 或圖片轉成可編輯檔的能力、公開評價與定價，找出它們具體輸在哪裡，作為 Mortise 的切入點與評測基準
- 本篇性質：查的是**競品與對照組**，不是要內嵌的元件。總表仍沿用其他查證紀錄的欄位，欄位解讀見第 1 節開頭

## 0. 查證方式與限制

本次查證的工具狀況，直接影響每一條結論的可信度，先講清楚。

| 來源 | 狀況 |
|---|---|
| 網路搜尋 | 可用。本篇絕大多數結論來自搜尋工具對官方頁面的轉述 |
| api.github.com | **只有搜尋介面可直接讀取**（開源專案的授權欄位、星數、最後推送日、一行簡介）。個別專案的說明文件與程式碼被擋下，沒有讀到 |
| www.microsoft.com（官方部落格） | **可直接讀取** |
| support.claude.com、claude.com | **可直接讀取** |
| adobe.com、helpx.adobe.com、community.adobe.com、developer.adobe.com | **無法直接讀取**（被工作環境的網路規則擋下） |
| support.microsoft.com、learn.microsoft.com、techcommunity.microsoft.com | **無法直接讀取** |
| pdf.abbyy.com、help.abbyy.com | **無法直接讀取** |
| recosoft.com、markzware.com | **無法直接讀取** |
| canva.com | **無法直接讀取** |
| support.google.com、workspaceupdates.googleblog.com | **無法直接讀取** |
| smallpdf.com、ilovepdf.com | **無法直接讀取** |
| gamma.app、codia.ai、copyslides.com 及其他新創官網 | **無法直接讀取** |
| capterra.com、g2.com、techradar.com、plusai.com、martech.zone 等評測站 | **無法直接讀取** |

下文每條事實後面標註查證方式：

- **【直讀】** 我直接讀到官方來源的原文
- **【搜尋摘要】** 搜尋工具轉述了該頁面的內容，但我沒有親自打開該頁（未直接開啟）
- **【第三方】** 只有非官方來源（評測站、競品部落格、使用者論壇）

三個必須先知道的限制：

1. **沒有任何一項是我親手測的。** 本篇沒有上傳任何檔案到任何競品。所有「弱點」都來自官方文件自己承認的限制、官方論壇的使用者回報、或第三方評測。
2. **沒有找到任何一份獨立、同一批樣本、橫跨這些產品的量化評測**（例如每頁文字框數、字型命中率、表格還原率）。找到的「實測」文章多半是賣轉檔工具或簡報工具的廠商自己寫的，排名互相矛盾。
3. **定價會變動，且多數定價頁我打不開。** 定價數字除非標【直讀】，否則都是搜尋摘要或第三方彙整站的數字，動用前要自己再看一次官方定價頁。

## 1. 總表

欄位解讀（因為本篇查的是競品）：

- **程式碼授權／權重授權**：閉源商業產品一律填「專有」；開源專案填實際授權。
- **可否商用**、**可否隨桌面版散布**：解讀為「能不能當成 Mortise 的元件」。競品成品本身不是元件，填「不適用」；若該廠商另外賣 SDK 或 API，則註明。
- **已查證**：「是」表示主要結論有官方來源（直讀或搜尋摘要）；「部分」表示官方來源只涵蓋一部分，其餘靠第三方；「否」表示只有第三方說法。

### 1.1 傳統 PDF 轉檔與 OCR 工具

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用（當元件） | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **Adobe Acrobat（Standard、Pro、Studio）匯出** | PDF 轉 Word、Excel、PowerPoint；掃描檔先 OCR 再轉 | 專有 | 專有 | 不適用（成品）。Adobe 另有雲端 API（免費額度每月 500 次文件交易，付費價不公開） | 不適用 | 輸出 DOCX、XLSX（或 XML 試算表）、PPTX、RTF、純文字、圖片、HTML；兩種版面模式：「保留流動文字」與「保留頁面版面」；掃描檔可轉（靠 OCR）；轉檔技術自 2010 年起授權自 Solid Documents | 官方自己寫明「保留頁面版面」可能把文字拆成多個文字框群組；需開啟密碼的檔案不能轉。論壇回報：獨立文字框被合併成表格、不同行被併成單一物件、字型全被換掉且文字框變窄、OCR 文字被加上奇怪的字距；轉 Excel 時亂合併儲存格、多個數字擠進同一格、整份資料落在同一欄。掃描檔預設用「合成字型」模仿原字，打新字時會退回系統字型 | 部分（官方文件為搜尋摘要；弱點為官方論壇使用者回報） | [匯出說明](https://www.adobe.com/learn/acrobat/web/convert-pdf-retain-page-layout)、[定價](https://www.adobe.com/acrobat/pricing.html)、[論壇 1](https://community.adobe.com/t5/acrobat-discussions/convert-pdf-to-ppt-with-out-changing-the-text-frame-format/td-p/13979962)、[論壇 2](https://community.adobe.com/t5/acrobat-discussions/how-to-prevent-font-changes-and-narrowing-when-converting-from-pdf-to-word/td-p/13347193) |
| **Microsoft Word 內建 PDF 重排（PDF Reflow）** | 用 Word 直接開 PDF，轉成可編輯文件 | 專有 | 不適用 | 不適用 | 不適用 | Word 2013 起內建；用規則推論標題、清單、表格；轉出的是副本，不動原檔 | 官方明講「重視可編輯勝過版面精準」「以文字為主的文件效果最好」；簡報與型錄類 PDF 容易版面出錯；投影片轉出後文字落在文字框，編輯時不會跨頁重排；表格靠啟發式規則猜；微軟社群回覆稱掃描 PDF 直接用 Word 開通常不會變成可編輯文字 | 是（2012 年官方部落格直讀；現行支援頁為搜尋摘要） | [官方部落格](https://www.microsoft.com/en-us/microsoft-365/blog/?p=836)、[Edit a PDF](https://support.microsoft.com/en-us/word/training/edit-a-pdf)、[Opening PDFs in Word](https://support.microsoft.com/word/opening-pdfs-in-word) |
| **Microsoft Excel「從圖片插入資料」** | 表格照片或截圖轉成儲存格 | 專有 | 專有 | 不適用 | 不適用 | 輸入：剪貼簿截圖、圖片檔、手機相機；平台：Windows、Mac、iPhone、Android；插入前會標出可能辨識錯誤的儲存格讓使用者確認 | 只處理表格；**現行支援頁只列 21 種拉丁字母語言（英、法、德、西等），沒有中文、日文、韓文**；2025 年官方問答確認不支援阿拉伯文；混雜不支援的文字時分析可能失敗 | 是（搜尋摘要） | [支援頁](https://support.microsoft.com/en-us/excel/insert-data-from-picture)、[2019 部落格](https://www.microsoft.com/en-us/microsoft-365/blog/2019/05/29/insert-data-from-picture-take-analog-data-digital/) |
| **ABBYY FineReader PDF 16** | OCR 加 PDF 編輯與轉檔，桌面軟體 | 專有 | 專有 | 不適用（成品）。ABBYY 另有 FineReader Engine 12 SDK：隨應用程式散布需購買 Runtime License，價格不公開 | 不適用 | 輸出 DOCX、XLSX、PPTX、HTML、RTF、TXT、CSV、ODT；198 種辨識語言（官方規格頁）；儲存時有「精確副本」「可編輯副本」等多種版面模式；使用者評價普遍稱讚辨識準確與版面保留 | 只有訂閱制；Mac 版功能少於 Windows 版（使用者評價）；低品質掃描辨識差；沒有找到針對 PPTX 輸出文字框或字型問題的具體評價 | 部分 | [規格頁](https://pdf.abbyy.com/specifications/)、[儲存模式說明](https://help.abbyy.com/en-us/finereader/16/user_guide/savetoedit)、[Capterra 定價](https://www.capterra.com/p/65868/ABBYY-FineReader/pricing/) |
| **Google 雲端硬碟 OCR（用 Google 文件開啟 PDF 或圖片）** | 免費 OCR，轉成 Google 文件 | 專有 | 專有 | 不適用 | 不適用 | 輸入：PDF（多頁）、JPEG、PNG、GIF；粗體、斜體、字級、字型、換行「最可能保留」 | 官方寫明：檔案須 2 MB 以下、文字高度至少 10 像素；**清單、表格、欄、註腳與章節附註很可能偵測不到** | 是（搜尋摘要） | [官方說明](https://support.google.com/drive/answer/176692?hl=en) |
| **Smallpdf** | 線上 PDF 轉 Word、Excel、PPT | 專有 | 專有 | 不適用 | 不適用 | PDF 轉 Word、Excel、PPT；付費版含 OCR（三種輸出都有）；評測普遍認為格式保留不錯 | 免費版每日下載有限制且無 OCR；沒有找到官方對 PPT 輸出限制的說明 | 部分（定價為第三方） | [定價頁](https://smallpdf.com/pricing)、[企業定價頁](https://smallpdf.com/business/pricing)、[Capterra](https://capterra.com/p/172606/Smallpdf/pricing/) |
| **iLovePDF** | 線上 PDF 轉 Word、Excel、PowerPoint | 專有 | 專有 | 不適用 | 不適用 | PDF 轉 Word、PowerPoint、Excel；工具清單中「PDF to Word (OCR)」是獨立項目；PlusAI 2025 年實測認為它與 Adobe 在字型、對齊、版面的選擇略好 | 工具清單中 PowerPoint 沒有 OCR 版本（掃描檔轉 PPT 是否能編輯未查到）；免費版限檔案大小且無 OCR | 部分（定價來源互相矛盾） | [定價頁](https://www.ilovepdf.com/pricing)、[PlusAI 實測](https://plusai.com/blog/best-pdf-to-powerpoint-converters) |

### 1.2 排版軟體方向（InDesign、Illustrator）

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用（當元件） | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **Adobe InDesign 內建「PDF 轉 InDesign 文件」** | 在 InDesign 內直接開 PDF 成為可編輯文件 | 專有 | 專有 | 不適用（另有 Firefly Services 的 PDF-to-InDesign API） | 不適用 | 檔案選單「開啟 PDF」；圖片抽出成連結素材；API 版可把相關段落併入同一文字框、保留表格與超連結 | 需連網並登入 Adobe 帳號（轉換在雲端做）；官方教學承認：斷字改變會造成溢排文字、多欄文字框被拆成個別文字框要自己重新串連；API 版**不支援掃描或 OCR 來源的 PDF**，上限 200 頁與 150 MB，超過 80 種樣式時自動建立樣式會失效 | 部分（官方「支援與限制」頁內文沒讀到） | [使用說明](https://helpx.adobe.com/indesign/using/convert-pdf-to-indesign-file.html)、[API 轉換說明](https://developer.adobe.com/firefly-services/docs/indesign-apis/guides/pdf-to-indesign-conversion-notes)、[限制頁（未讀到內文）](https://helpx.adobe.com/indesign/desktop/create-and-organize-pages/import-and-convert-file-to-indesign/convert-pdf-support-limitations.html) |
| **Recosoft PDF2ID Professional Suite 2026** | PDF 轉可編輯 InDesign 檔的外掛 | 專有 | 不適用 | 不適用 | 不適用 | 2025-11-04 發表；支援 InDesign 2022 至 2026；可還原首字放大、陰影、上下標、段落線；新版預設跨頁串連文字框、自動建立註腳 | 官方自己寫明：設計目標是「編輯彈性」而不是百分之百重建，不保證來回轉換；以 InDesign 外掛形式運作；直接輸出 IDML 只查到 2011 年的 Enterprise 版，現行版本未確認；是否處理掃描檔沒有查到 | 部分 | [2026 版發表](https://www.recosoft.com/?p=10753)、[新功能](https://www.recosoft.com/?p=10629)、[轉換細節](https://www.recosoft.com/?p=133) |
| **Markzware PDFMarkz、OmniMarkz、MarkzPortal** | PDF 轉 IDML 等排版格式 | 專有 | 不適用 | 不適用 | 不適用 | PDF2DTP 已停售，由 PDFMarkz 取代；PDFMarkz 可輸出 IDML，不需安裝 InDesign；MarkzPortal 是雲端版 | 現行 PDFMarkz 與 OmniMarkz 定價沒有查到；沒有找到轉換品質的獨立評價 | 部分 | [產品沿革](https://markzware.com/company/from-legacy-to-leading-edge-markzwares-product-evolution/)、[MarkzPortal 定價](https://markzportal.markzware.com/pricing) |
| **Adobe Illustrator 直接開 PDF；Retype** | 開 PDF 編輯；辨識圖片中的字型 | 專有 | 專有 | 不適用 | 不適用 | 可開 PDF 並保留向量；Retype 可比對 Adobe Fonts 中三萬多款字型 | 論壇專家說法：PDF 文字在 Illustrator 裡「一律」碎成多個輸入點；缺字型時文字被轉外框；**Retype 自 2025 年 2 月起只剩字型辨識，把點陣或外框字轉成可編輯文字的功能已移除**，替代功能「Un-outline」仍在開發 | 部分（論壇與社群專家說法） | [Retype 說明](https://helpx.adobe.com/illustrator/desktop/design-with-text/fonts-and-scripts/identify-fonts-using-retype.html)、[論壇：Retype 不能編輯文字](https://community.adobe.com/t5/illustrator-discussions/retype-not-allowing-to-edit-text/td-p/15153814)、[論壇：文字碎裂](https://community.adobe.com/questions-652/open-pdf-with-text-in-expanded-form-802776) |
| **Affinity（Canva 旗下，軟體免費）匯入 PDF** | 開 PDF 成為可編輯設計檔 | 專有 | 不適用 | 不適用 | 不適用 | 匯入時文字處理可選「偏重可編輯」或「偏重保真」；文字分組可選「併成文字框」或「保留版面」；缺字型時可指定替代字型 | 查到的說明頁屬於第 2 版，現行版本行為未查到；教學稱文字對齊與字型有限制；是否處理掃描檔未查到 | 部分（舊版說明頁） | [匯入 PDF 說明](https://affinity.help/publisher2/en-US.lproj/pages/GetStarted/importPDF.html) |

### 1.3 設計平台（Canva）

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用（當元件） | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **Canva 匯入 PDF** | PDF 轉成可編輯的 Canva 設計 | 專有 | 專有 | 不適用 | 不適用 | 上限 500 頁、300 MB；會把文字與圖片拆成可編輯元素，盡量保留版面 | 官方寫明：**掃描或壓平的 PDF 會匯入成單一張圖，文字與設計元素不能編輯**；不支援的字型會被換成相似字型；超過 1,400 個元素或圖片算超出複雜度，可能匯入不完整；版面可能略為位移 | 是（搜尋摘要） | [匯入 PDF 說明](https://www.canva.com/help/pdf-import/)、[編輯 PDF 說明](https://www.canva.com/help/import-and-edit-pdfs-canva/) |
| **Canva 擷取文字（Grab Text）** | 把圖片上的字換成可編輯文字 | 專有 | 專有 | 不適用 | 不適用 | 付費功能；辨識文字、從背景移除、換成可編輯文字，並嘗試配相似字型與位置 | 官方寫明：字型與位置配對「有時不完美」；粗體、斜體、底線尚未支援；部分文字辨識不到；可能留下文字殘渣，需另用魔法橡皮擦清除 | 是（搜尋摘要） | [擷取文字說明](https://www.canva.com/help/using-grab-text/) |
| **Canva Magic Layers** | 把一張扁平圖拆成可編輯圖層（文字、圖形、物件） | 專有 | 專有（Canva Design Model） | 不適用 | 不適用 | 2026-03-11 公開測試；把圖中的文字、圖形、版面元件拆成可移動可編輯的圖層 | 測試期只收單頁 PNG 與 JPG；初期只在美、英、加、澳；算進每月 AI 用量；官方寫明最適合平面設計與插畫，**寫實照片分離效果較差**；第三方評價：遮擋多或顏色相近時圖層會黏在一起、邊緣不乾淨、字型只是推測 | 部分 | [產品頁](https://canva.com/magic-layers)、[說明頁](https://www.canva.com/en_au/help/editable-magic-layers-variantb/)、[PetaPixel 報導](https://petapixel.com/2026/03/11/canvas-new-ai-tool-turns-flat-images-into-editable-layers) |

### 1.4 AI 助理與簡報生成工具

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用（當元件） | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **Claude（建立與編輯檔案）** | 對話中產生 XLSX、PPTX、DOCX、PDF | 專有 | 專有 | 模型可經 API 付費商用（另見其他查證紀錄） | 否（雲端服務） | 所有方案皆可用（免費版到企業版）；上傳與下載單檔上限 30 MB；可依上傳的檔案產生新檔 | 官方文件沒有任何「照原版面還原」的承諾，只說使用者「可能需要檢視並修改輸出」；本質是**重新生成**，不是版面還原 | 是（直讀） | [官方說明](https://support.claude.com/en/articles/12111783-create-and-edit-files-with-claude) |
| **ChatGPT（簡報與文件產生）** | 對話中產生簡報、文件、試算表並匯出 | 專有 | 專有 | 不適用 | 否 | 官方文件稱它產生的簡報匯出到 PowerPoint 後文字與版面物件皆可編輯；可匯出 PowerPoint、PDF、Google 簡報 | 沒有找到官方宣稱能把任意 PDF 或圖片照原樣轉成可編輯 PPTX；2025 年報導引述 OpenAI 說部分進階 PowerPoint 功能（範本、字型處理）尚未支援 | 部分 | [官方文件](https://learn.chatgpt.com/docs/artifacts-viewer?surface=web)、[TechRepublic](https://www.techrepublic.com/article/news-how-to-use-chatgpt-slides-powerpoint/) |
| **Gemini（Canvas 簡報；Google 簡報內的 Gemini）** | 依上傳檔案或主題產生簡報 | 專有 | 專有 | 不適用 | 否 | Canvas 可匯出到 Google 簡報、PowerPoint、PDF；2026-06-30 起 Google 簡報內的 Gemini 可產生「完全原生、可編輯」的多頁簡報 | 是依內容重新生成加套用主題；沒有找到「把 PDF 或圖片照原版面轉成可編輯簡報」的官方功能 | 部分 | [Canvas 說明](https://support.google.com/gemini/answer/16047321?hl=en)、[2026-06 公告](https://workspaceupdates.googleblog.com/2026/06/create-fully-native-and-editable-presentations-with-Gemini-in-Google-Slides.html) |
| **Microsoft 365 Copilot（PowerPoint）** | 依 Word、PDF、TXT 檔產生簡報 | 專有 | 專有 | 不適用 | 否 | 「從檔案建立簡報」支援 Word、PDF、TXT；代理模式可附 pptx、docx、xlsx、pdf、jpg、jpeg、png 當作脈絡 | 是依內容重新生成；引用檔案建立時不能在同一提示加其他脈絡；需要授權 | 部分（官方頁面新舊說法不一） | [技術社群公告](https://techcommunity.microsoft.com/t5/microsoft-365-insider-blog/reference-pdf-and-txt-files-when-creating-presentations-with/ba-p/4427183)、[代理模式說明](https://support.microsoft.com/en-au/topic/agent-mode-frontier-in-powerpoint-008f17aa-8e5f-4cda-ba6c-0588000bdad7) |
| **Gamma（匯入）** | 匯入 PPTX、DOCX、PDF 成為 Gamma 簡報 | 專有 | 專有 | 不適用 | 否 | 支援 .pptx、.potx、.docx、PDF；官方說明稱新版（Gamma 5）匯入會保留原版面、設計與結構；缺字型時換相似字型並告知 | 舊版產生器只取文字不取版面；超過 100 MB 可能匯入失敗；匯入超過約 35 分鐘會停止；使用者回報 84 頁 PDF 只匯入約 3 頁且版面很差（時間可能早於新版）；**沒有圖片輸入** | 部分 | [匯入說明](https://help.gamma.app/en/articles/11047840-how-can-i-import-slides-or-documents-into-gamma)、[使用者回報](https://ideas.gamma.app/ideas/p/limportazione-da-pptx-non-funziona) |
| **NotebookLM 簡報匯出** | 產生簡報並匯出 PPTX | 專有 | 專有 | 不適用 | 否 | 2026 年初新增 PPTX 匯出（Google 2026-03-20 公告列出） | 多個第三方來源稱匯出的 PPTX 內仍是圖片層而非可編輯物件；另有來源稱文字與版面都保留，**說法互相矛盾**，Google 官方沒有說明可編輯程度 | 部分（可編輯程度矛盾） | [Google 公告](https://workspaceupdates.googleblog.com/2026/03/new-ways-to-customize-and-interact-with-your-content-in-NotebookLM.html)、[Android Authority](https://www.androidauthority.com/notebooklm-slide-deck-editing-3641895) |
| **Genspark、Kimi、Manus 的「PDF 轉 PPT」** | 代理人型產品附帶的 PDF 轉簡報工具 | 專有 | 專有 | 不適用 | 否 | Genspark 自稱保留標題、內文、表格、內嵌圖片，把字型對應到最接近的 PowerPoint 預設字型，用視覺模型讀掃描頁，上限 100 MB；Kimi 自稱版面貼近原件且可編輯 | Genspark 自己說是「依文件結構重建投影片」而非單純轉檔；Manus 強調摘要、圖解與重新設計，屬重新生成；全部是廠商自述，沒有獨立測試 | 否（只有廠商自述） | [Genspark](https://genspark.ai/tools/pdf-to-ppt)、[Kimi](https://www.kimi.com/capabilities/pdf-to-ppt)、[Manus](https://manus.im/tools/pdf-to-ppt) |
| **Microsoft Lens** | 手機拍照轉 Word、PowerPoint | 專有 | 專有 | 不適用 | 不適用 | 曾可把照片經 OCR 存成 Word 或 PowerPoint | **已退役**：Android 自 2026-01-09 起下架，2026-02-09 後不再支援；替代的 Microsoft 365 Copilot 應用程式掃描只能輸出 PDF 或圖片，不能轉 Word 或 PowerPoint | 是（搜尋摘要；退役日期各來源略有出入） | [官方退役說明](https://support.microsoft.com/en-au/topic/f159cdf7-b6ef-4acc-9f69-8b7538d69413)、[TechCrunch](https://techcrunch.com/2025/08/08/rip-microsoft-lens-a-simple-little-app-thats-getting-replaced-by-ai/) |

### 1.5 主打「圖片或 PDF 轉可編輯簡報」的新創與開源專案

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用（當元件） | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **Codia AI NoteSlide** | 圖片、圖片型 PDF、掃描投影片轉可編輯 PPTX | 專有 | 專有 | 不適用（另有按頁計費的開發者 API） | 否 | 用視覺模型解析版面，還原文字、圖片、形狀與層級；輸出 PPTX、PDF、投影片圖檔壓縮包 | 官方文件自己寫明：複雜圖表、照片、截圖、重度合成圖形可能維持圖片；「文字可編輯但換行不同」；品質取決於來源清晰度、解析度、版面複雜度 | 是（搜尋摘要） | [文件](https://codia.ai/docs/noteslide)、[定價](https://codia.ai/noteslide/pricing)、[圖片轉 PPT](https://codia.ai/noteslide/image-to-ppt) |
| **CopySlides** | 圖片、PDF、截圖、影片轉可編輯 PowerPoint 或 Google 簡報 | 專有 | 專有 | 不適用 | 否 | 宣稱把偵測到的文字變成可點選的文字框，重建為 PowerPoint 物件 | 資訊幾乎都來自彙整站，官方限制說明沒有查到 | 否（只有第三方） | [Capterra](https://www.capterra.com/p/10050452/CopySlides/)、[官網工具頁](https://copyslides.com/image-to-pptx) |
| **Image2PPT、SlideForge、2pptx、Reslide（Tosea AI）、Felo 等** | 同類線上工具 | 專有 | 專有 | 不適用 | 否 | 都宣稱輸出原生 PPTX；SlideForge 表示會在「保留圖片比較好」時明說 | 第三方彙整（EduCBA）稱多數同類工具只是把圖片貼成不可編輯的投影片；真正重建的工具對文字密集頁、價目表、圖解類也只是部分重建；**沒有獨立橫向評測** | 否（只有廠商自述與第三方） | [EduCBA 彙整](https://www.educba.com/image-to-ppt-converter-tools/)、[SlideForge](https://slideforge.dev/tools/image-to-pptx)、[Image2PPT](https://image2ppt.com/en/ai-slide-image-to-pptx) |
| **ningzimu/image-to-editable-ppt-skill**（開源） | 讓程式代理人把投影片圖片、PDF、圖片型 PPTX 重建成可編輯簡報的技能包 | MIT | 不含權重（靠呼叫代理人背後的模型） | 程式碼可；背後模型另計 | 程式碼可；背後模型另計 | GitHub 3,460 顆星，最後更新 2026-09-16；同類專案中最受關注 | 是代理人技能，不是獨立引擎；品質取決於所用模型；沒有找到評測 | 是（GitHub 直讀：授權、星數、日期） | [GitHub](https://github.com/ningzimu/image-to-editable-ppt-skill) |
| **laihenyi/NBLM2PPTX**（開源） | NotebookLM 的 PDF 轉可編輯 PPTX | MIT | 未查 | 程式碼可 | 程式碼可（所含模型授權未查） | 352 顆星，最後更新 2026-11-08；專案簡介寫 macOS 離線執行、不需 API 金鑰；第三方收錄頁描述的版本是輸出乾淨底圖加獨立文字框，並用 Gemini 做 OCR（與現行簡介不一致，可能是舊版） | 針對 NotebookLM 這類 AI 生成投影片；所用 OCR 與補圖模型的授權未查 | 是（GitHub 直讀：授權、星數、日期） | [GitHub](https://github.com/laihenyi/NBLM2PPTX) |
| **JuniverseCoder/MinerU2PPT**（開源） | PDF 與圖片轉可編輯 PPTX | **未標示授權**（GitHub 顯示無授權） | 依賴 MinerU | **不可**（無授權等於保留所有權利；且 MinerU 的授權另有限制） | 不可 | 201 顆星，最後更新 2026-05-24 | 沒有授權檔，不能使用 | 是（GitHub 直讀） | [GitHub](https://github.com/JuniverseCoder/MinerU2PPT) |
| **JadeLiu-tech/px-image2pptx**（開源） | 靜態圖片轉可編輯投影片（OCR、補圖、重建） | MIT | 未查 | 程式碼可 | 程式碼可（所含模型授權未查） | 39 顆星，最後更新 2026-11-10；流程是 OCR 加補圖加重建 | 規模小；模型授權未查 | 是（GitHub 直讀） | [GitHub](https://github.com/JadeLiu-tech/px-image2pptx) |

### 1.6 定價一覽

| 產品 | 定價 | 查證方式 |
|---|---|---|
| Adobe Acrobat Standard | 每月 14.99 美元（年約月繳） | 【搜尋摘要】官方定價頁 |
| Adobe Acrobat Pro | 每月 19.99 美元（年約月繳） | 【搜尋摘要】官方定價頁 |
| Adobe Acrobat Studio | 個人頁每月 24.99 美元；企業頁列 34.99 美元（兩頁數字不同，原因未確認） | 【搜尋摘要】官方定價頁 |
| Microsoft Word、Excel 的功能 | 含在 Microsoft 365 訂閱內，本次未查價 | 未查 |
| ABBYY FineReader PDF 16 | 只有訂閱。Windows Standard 每年 99 美元、Corporate 每年 165 美元、Mac 每年 69 美元；彙整站另列月繳 16 與 24 美元；試用 7 天、可轉 100 頁 | 【搜尋摘要】官方產品頁（Standard 99 美元）加【第三方】 |
| Google 雲端硬碟 OCR | 免費（含在 Google 帳號內） | 【搜尋摘要】 |
| Smallpdf Pro | 月繳每月 15 美元；年繳約每月 9 美元；團隊版每人每月 12 美元 | 【第三方】Capterra 等 |
| iLovePDF Premium | 官方定價頁摘要：年繳每月 5 美元、月繳 9 美元；Capterra 列 9 美元、G2 列 7 美元（**互相矛盾**） | 【搜尋摘要】加【第三方】 |
| Recosoft PDF2ID Professional Suite 2026 | 每年 299 美元（Windows 與 macOS 同價） | 【搜尋摘要】官方新聞稿 |
| Markzware MarkzPortal | 單次每份文件 10 美元起（基本 7 美元加每頁 3 美元）；訂閱每月 79 美元 | 【搜尋摘要】官方定價頁 |
| Markzware OmniMarkz | 2021 年上市價：年訂閱 299 美元、買斷 399 美元（官方註明為歷史價格）；現價未查到 | 【第三方】 |
| Canva | Pro 每月 18 美元或每年 144 美元；Business 每人每月 25 美元（最少 3 人），Canva 在地化頁面列每人每年 250 美元；另有 AI Pass 每人每月 100 美元。G2 把 Magic Layers 列為 Pro 功能 | 【第三方】G2、Piktochart 等；官方定價頁未能開啟 |
| Codia NoteSlide | 免費 5 點；Starter 每月 12 美元、Pro 每月 29 美元、Ultra 每月 120 美元（年繳折扣價）；每輸出一頁可編輯投影片 1 點；開發者 API 每頁 13 點 | 【搜尋摘要】官方定價頁 |
| CopySlides | Starter 每月 9 美元（每月 200 頁）、Plus 每月 19 美元 | 【第三方】彙整站 |
| Gamma | 2026-10-19 的第三方擷取：Plus 每席每月 9 美元、Pro 18 美元、Ultra 90 美元；較早的來源列 Pro 20 至 25 美元、Ultra 100 美元（**互相矛盾**） | 【第三方】 |
| Solid Framework SDK（Acrobat 匯出背後的技術，現屬 Apryse） | 官方不公開價格；經銷商頁列「Professional 加 OCR 版、內部使用，每年 400,000 美元」，日期不明 | 【第三方】未證實 |
| Adobe Acrobat Services（PDF Services）API | 免費額度每月 500 次文件交易（定價頁）；企業頁卻寫 1,000 次（**官方兩頁不一致**）；付費需另簽合約，價格不公開 | 【搜尋摘要】官方開發者網站 |
| Apryse SDK（含 PDF 轉 Office） | 模組化計價，入門套件 1,500 美元起，實際需詢價；使用者評價稱「每樣都是加購項目，各有授權條款與合約」 | 【搜尋摘要】官方定價頁加【第三方】 |
| ABBYY FineReader Engine 12 SDK | 不公開，需洽當地辦公室；授權依模組與每期可處理頁數或字元數計 | 【搜尋摘要】官方文件 |
| 其他桌面轉檔軟體（只列價格，未查轉檔品質） | Able2Extract Professional 買斷 199.95 美元；Nitro PDF 每人每月 15 美元起；Foxit PDF Editor 每人每月 10.99 美元起；PDFelement 團隊版每授權每月 9.08 美元（年繳） | 【第三方】Capterra、G2、GetApp |

## 2. 需求對照

把 Mortise 的輸入與輸出對到現有產品，看哪一格目前沒有人做好。「有」表示官方宣稱支援；「弱」表示官方自己承認有明顯限制或使用者普遍回報問題；「無」表示沒有這個功能。

| 需求 | Acrobat | Word 內建 | ABBYY | Canva | InDesign 內建、PDF2ID、Markzware | AI 助理（Claude、ChatGPT、Gemini、Copilot） | 圖片轉簡報新創（Codia 等） |
|---|---|---|---|---|---|---|---|
| 原生 PDF 轉 PPTX | 有，但文字框零碎或被併成表格 | 無 | 有 | 匯入成 Canva 設計後可再匯出（匯出品質未查） | 無 | 重新生成，不保留原版面 | 有（以圖片型 PDF 為主） |
| 掃描 PDF 轉 PPTX | 有（先 OCR） | 無 | 有 | **無**（匯入成單張圖） | 無 | 重新生成 | 有 |
| 照片或截圖轉 PPTX | 需先轉成 PDF 再 OCR | 無 | 有（圖片可當輸入） | Magic Layers（單頁、測試中） | 無 | 重新生成 | 有，主打項目 |
| PDF 轉 DOCX，可重排 | 有（保留流動文字） | 有，這是它的設計目標 | 有（可編輯副本） | 無 | 無 | 重新生成 | 無 |
| PDF 轉 DOCX，版面固定 | 有（保留頁面版面，會拆文字框） | 弱 | 有（精確副本） | 無 | 無 | 無 | 無 |
| 表格轉 XLSX | 有 | 無 | 有 | 無 | 無 | 可抽資料重建 | 無 |
| 表格照片轉 XLSX | 需先 OCR | 無 | 有 | 無 | 無 | 可 | 無（Excel「從圖片插入資料」是另一條路，但只支援 21 種拉丁字母語言，不支援中文） |
| PDF 轉 IDML 或 InDesign | 無 | 無 | 無 | 無 | 有，但**都不吃掃描檔或圖片**（已確認者：InDesign API 版明文不支援） | 無 | 無 |
| 圖片或掃描檔轉 IDML | 無 | 無 | 無 | 無 | 沒有查到任何產品 | 無 | 無 |
| 文字可編輯的 PDF（給 Illustrator） | 可在 Acrobat 內編輯 | 無 | 可輸出可搜尋 PDF | 可匯出 PDF | 無 | 無 | 部分可匯出 PDF |
| 模板（只留版面骨架） | 無 | 無 | 無 | Magic Layers 拆層後可自行清空 | 無 | 無 | 無 |
| 乾淨底圖（把字從圖上移除） | 無 | 無 | 無 | 擷取文字、Magic Layers（會留殘渣） | 無 | 無 | 有，主打項目（品質未有獨立評測） |
| 還原度報告（標示哪些是推論） | 無 | 無 | 有辨識信心標示（OCR 編輯器內） | 無 | 無 | 無 | 沒有查到 |
| 繁體中文 | 有 OCR 語言包（未查細節） | 不適用 | 198 種語言（未逐一確認繁中品質） | 未查到 | 未查到 | 模型本身支援 | 未查到 |

從這張表看得到的空白：

1. **圖片或掃描檔到 IDML**：沒有查到任何現有產品。InDesign 方向的三家都只吃原生 PDF。
2. **還原度報告**：沒有任何一家把「這部分是推論的」當成交付物。
3. **模板輸出**：沒有任何一家把「只留版面骨架」當成正式輸出。
4. **同一份來源、多種輸出、彼此一致**：Acrobat 與 ABBYY 最接近，但都不做簡報底圖清理，也不出排版格式。

## 3. 逐條事實與來源

### 3.1 Adobe Acrobat 匯出

1. 匯出格式包含 Word、Excel、PowerPoint、RTF、純文字、圖片、HTML。【搜尋摘要】<https://www.adobe.com/learn/acrobat/web/convert-pdf-retain-page-layout>、<https://helpx.adobe.com/uk/document-cloud/help/using-exportpdf.html>
2. 匯出有兩個版面選項：「保留流動文字」偏向可重排，「保留頁面版面」維持原設計。官方說明寫到後者**可能把文字拆到不同的文字框群組**以維持原設計。【搜尋摘要】同上
3. 大多數 PDF 都能轉，包含掃描文件（靠 OCR）；需要密碼才能開啟的檔案不能轉。【搜尋摘要】<https://helpx.adobe.com/ie/document-cloud/faq/adobe-exportpdf-faq.html>
4. Acrobat 的 PDF 轉 Office 技術授權自 Solid Documents 的 Solid Framework SDK，始於 2010 年 11 月的 Acrobat X，Acrobat XI 延續。Solid Documents 於 2021 年 8 月被 PDFTron（現名 Apryse）收購，Solid Framework 現在併入 Apryse SDK。【搜尋摘要】<https://www.soliddocuments.com/pdf/_solidframework_adobe_x/300>、<https://pdfa.org/pdftron-acquires-pdf-to-office-innovator-solid-documents/>、<https://apryse.com/brands/soliddocuments>。**現行 Acrobat 是否仍用同一套技術，沒有查到。**
5. 使用者回報（2023 年，官方論壇）：InDesign 匯出的 PDF 轉 PowerPoint 時，獨立的文字框被合併成一個表格；另一位使用者說要先手動把文字框挪開一點才能避免，而且有時還是會發生。沒有官方解法。【第三方：官方論壇使用者】<https://community.adobe.com/t5/acrobat-discussions/convert-pdf-to-ppt-with-out-changing-the-text-frame-format/td-p/13979962>
6. 使用者回報（2016 年，官方論壇）：PDF 中原本分開的各行，轉成 PPTX 後被併成單一物件；有人提出先轉 PostScript 再轉的繞道做法，對某些檔案有效、對另一些無效。【第三方】<https://community.adobe.com/t5/acrobat-sdk-discussions/pdf-to-pptx-problem-objects-elements-merged-into-a-single-object/td-p/8074121>
7. 使用者回報：InDesign 轉 PDF 再用 Acrobat 轉 Word，所有字型都被換掉、文字框變窄、文字擠在一起。【第三方】<https://community.adobe.com/t5/acrobat-discussions/how-to-prevent-font-changes-and-narrowing-when-converting-from-pdf-to-word/td-p/13347193>
8. 使用者回報：掃描文件轉 Word 後字距怪異；一位回覆者判斷是 Acrobat 的 OCR 為了模仿掃描外觀而加上字距調整。這是單一使用者的判斷，不是官方說明。【第三方】<https://community.adobe.com/t5/acrobat-discussions/converting-a-pdf-to-ms-word-font-spacing-issue/td-p/11134482>
9. 字型：競品 WPS 的支援文章稱 Acrobat 不會把內嵌字型抽出放進 DOCX（歸因於字型授權）；官方論壇使用者稱即使 PDF 有內嵌字型，沒裝該字型的電腦仍會被替換。兩者都不是 Adobe 官方說法。【第三方】<https://www.wps.com/office-quick-solution/pdf-troubleshooting/why-acrobat-export-to-word-does-not-embed-pdf-fonts/>
10. 定價（美國、個人、年約月繳）：Standard 每月 14.99 美元、Pro 每月 19.99 美元、Studio 每月 24.99 美元；Studio 含 Pro 全部功能加 AI 助理與 Adobe Express Premium。企業定價頁把 Studio 列為每月 34.99 美元。【搜尋摘要】<https://www.adobe.com/acrobat/pricing.html>、<https://adobe.com/acrobat/business/pricing>
11. 第三方評測：PlusAI（2025 年，自家也賣轉檔工具）測了 10 個工具，認為 Adobe 與 iLovePDF 在字型、對齊、版面的選擇略好。Ranktracker（2026 年）給 Adobe 4.2 分、Smallpdf 3.9 分、iLovePDF 3.5 分。softpicker（2026 年）則說 Smallpdf 的格式準確度最好。**排名互相矛盾。**【第三方】<https://plusai.com/blog/best-pdf-to-powerpoint-converters>、<https://www.ranktracker.com/blog/best-ai-pdf-to-ppt-converters/>、<https://softpicker.com/how-to-convert-pdf-to-powerpoint-free/>
12. 掃描檔的字型處理：官方說明〈Edit scanned PDFs〉與論壇專家說法一致。預設的「可編輯文字與影像」輸出會合成一個貼近原字的自訂字型（名稱像 Times Roman-13009），而且只含已出現過的字元；一打到沒出現過的字元就得改用系統字型，外觀可能對不上。另有「使用可用的系統字型」選項，改用系統上最接近的已安裝字型。沒有查到可以指定替代字型的方法。【搜尋摘要】<https://helpx.adobe.com/acrobat/current/edit-scanned-pdfs.html>、【第三方：官方論壇】<https://community.adobe.com/t5/acrobat-discussions/acrobat-pro-ocr-ends-up-with-different-fonts-to-system-fonts/m-p/14888411>
13. 轉 Excel 的使用者回報（官方論壇，2015 至 2026 年多則）：把不該合併的儲存格合併，乾淨的原生 PDF 也會；掃描對帳單的多個數字擠進同一格；所有欄的資料落在同一欄；版面完全相同的不同頁，有的解析正確有的不正確；有使用者說 2017 版正常、新版反而不同。論壇回覆的解釋是 Acrobat 必須自己推論表格結構。【第三方：官方論壇】<https://community.adobe.com/questions-9/how-do-i-stop-acrobat-from-merging-cells-that-shouldn-t-be-when-converting-from-pdf-to-excel-1551386>、<https://community.adobe.com/questions-9/issue-with-table-conversion-from-scanned-pdf-to-excel-in-adobe-acrobat-pro-1302897>
14. Adobe 另有雲端的 Acrobat Services（PDF Services）API。免費額度：定價頁寫每月 500 次文件交易，企業頁寫 1,000 次，兩頁不一致；免費層每分鐘 25 次請求；付費需另簽書面合約。該 API 的匯出是否涵蓋 DOCX、PPTX、XLSX，本次沒有確認。【搜尋摘要】<https://developer.adobe.com/document-services/pricing/>、<https://developer.adobe.com/document-services/docs/overview/limits>
15. Apryse（Solid Framework 的現任擁有者）官方定價頁稱計價為模組化、只為用到的部分付費，入門套件 1,500 美元起；其 PDF 轉 Office 功能頁稱可把 PDF 表格轉成 XLSX、把簡報轉成 PowerPoint 檔。第三方指南提醒要問清楚正式環境是按伺服器、處理器核心還是容器計價。Solid Framework 在 Apryse SDK 中對應哪個模組、能否隨桌面應用程式散布，本次沒有確認。【搜尋摘要】<https://apryse.com/pricing>、<https://www.pdftron.com/capabilities/conversion/pdf-to-office>、【第三方】<https://verdocs.com/blog/apryse-pricing>

### 3.2 Microsoft Word 內建 PDF 重排

1. 官方部落格〈Unlock PDFs with Word 2013〉（2012-08-09）說明：PDF Reflow 用規則判斷哪些 Word 元素（標題、清單、表格）最能代表原內容；轉出的是副本。【直讀】<https://www.microsoft.com/en-us/microsoft-365/blog/?p=836>
2. 同一篇明講設計取捨：這個流程「重視編輯內容的能力，勝過完美對齊的畫面」。【直讀】同上
3. 同一篇明講適用範圍：「以文字為主的文件效果最好」，例如法律與商業文件；如果 PDF「大多是圖片與圖表」，例如簡報或型錄，版面問題的機率高很多。【直讀】同上
4. 同一篇舉例：轉換後文字換行可能不同，某一行會從一欄頂端跑到前一欄尾端；PowerPoint 投影片轉出後內容還在，但文字落在文字框裡，編輯時不會順利跨頁重排。【直讀】同上
5. 同一篇解釋表格為何難：PDF 的表格通常只是絕對定位的線段與文字，沒有任何資訊說明哪些文字屬於哪一格，Word 必須用啟發式規則推論。【直讀】同上
6. 現行支援頁〈Edit a PDF〉仍寫「最適合以文字為主的 PDF」，並說書籍章節或看起來像影印稿的 PDF 在 Word 裡格式不會好。【搜尋摘要】<https://support.microsoft.com/en-us/word/training/edit-a-pdf>
7. 〈Opening PDFs in Word〉寫 Word 會嘗試配合原 PDF 的版面，轉出的檔案「可能看起來跟原 PDF 不完全一樣」。【搜尋摘要】<https://support.microsoft.com/word/opening-pdfs-in-word>
8. Word 對掃描 PDF 的處理：微軟問答的回覆說，PDF 除非先經過 OCR，否則在 Word 裡開不成可編輯文件；微軟技術社群的回覆說，直接用 Word 開掃描 PDF 通常不會讓頁面文字變成可編輯；第三方也說 Word 沒有內建 OCR。但另有報導說把 JPEG 存成 PDF 後用 Word 開會變成可編輯文件。**多數來源指向「不做 OCR」，但有一則相反說法，而且沒有查到官方文件的明確敘述。**【第三方：微軟社群回覆】<https://learn.microsoft.com/en-us/answers/a/11536346>、<https://techcommunity.microsoft.com/discussions/windows11/how-can-i-convert-pdf-to-editable-word-with-ocr-on-windows-11/4527412/replies/4549789>、<https://blog.flipsnack.com/how-to-edit-a-pdf-in-word/>

### 3.3 Microsoft Excel「從圖片插入資料」與 Microsoft Lens

1. 輸入來源：剪貼簿上的螢幕截圖、電腦上的圖片檔、iPhone 相機。Windows 版需要 Windows 11 或 Windows 10 1903 以上，並安裝 Microsoft Edge WebView2 Runtime。【搜尋摘要】<https://support.microsoft.com/en-us/excel/insert-data-from-picture>
2. 2019 年上市時，Android 與 iPhone 版支援 21 種拉丁語系語言。官方部落格說 OCR 不一定全對，所以功能會特別標出可能的錯誤讓使用者檢查。【搜尋摘要】<https://www.microsoft.com/en-us/microsoft-365/blog/2019/05/29/insert-data-from-picture-take-analog-data-digital/>
3. 2025 年 2 月微軟問答中的回覆：不支援阿拉伯文。另一則回覆：截圖含不支援的文字或混合文字時分析可能失敗。【搜尋摘要】<https://learn.microsoft.com/en-us/answers/a/12776037>
4. 現行支援頁寫「只支援下列字元集」：英文、波士尼亞文、克羅埃西亞文、捷克文、丹麥文、荷蘭文、芬蘭文、法文、德文、匈牙利文、義大利文、挪威文、波蘭文、葡萄牙文、羅馬尼亞文、塞爾維亞文、斯洛伐克文、斯洛維尼亞文、西班牙文、瑞典文、土耳其文。共 21 種，全是拉丁字母語言，**沒有中文、日文、韓文**。這是搜尋工具轉述的頁面內容，頁面可能在被索引之後更新過。【搜尋摘要】<https://support.microsoft.com/en-us/excel/insert-data-from-picture>
5. Microsoft Lens 已退役。官方支援頁（搜尋摘要）：Android 自 2026-01-09 起下架、2026-02-09 後不再支援；2026 年 5 月的微軟問答說掃描功能可用到 2026-03-09。早期報導說 2025 年 9 至 10 月開始退役。**日期各來源不一致。**【搜尋摘要】<https://support.microsoft.com/en-au/topic/f159cdf7-b6ef-4acc-9f69-8b7538d69413>、<https://techcrunch.com/2025/08/08/rip-microsoft-lens-a-simple-little-app-thats-getting-replaced-by-ai/>
6. 微軟指定的替代品 Microsoft 365 Copilot 應用程式可以掃描，但只能輸出 PDF 或圖片，不能直接存成 Word 或 PowerPoint。【第三方報導】<https://www.windowscentral.com/software-apps/microsoft-lens-is-dead-microsoft-wants-you-on-onedrive-but-it-doesnt-let-you-save-scans-locally>

### 3.4 ABBYY FineReader PDF 16

1. 可編輯輸出格式：Corporate 版列 DOCX、XLSX、PPTX、HTML、RTF、TXT、CSV、ODT；Mac 版同樣列 DOCX、XLSX、PPTX。規格頁另一處多列 ODS、ODP，同頁前後不一致。【搜尋摘要】<https://pdf.abbyy.com/specifications/>
2. 辨識語言數：官方 Windows 規格頁寫 198 種、其中 53 種有字典支援；官方行銷頁卻寫 192 種、48 種有字典。**官方兩頁數字不一致。**【搜尋摘要】同上、<https://pdf.abbyy.com/>
3. 儲存為可編輯格式時有多種版面模式，查到名稱的兩種是「精確副本」（格式貼近原件）與「可編輯副本」（格式可能略有不同但較好編輯）。【搜尋摘要】<https://help.abbyy.com/en-us/finereader/16/user_guide/savetoedit>
4. 定價：只有訂閱。官方產品頁列 Standard 每年 99 美元、一個單機授權；第三方列 Corporate 每年 165 美元、Mac 每年 69 美元、三年方案 267、446、186 美元；Capterra 列月繳 16 與 24 美元。試用 7 天、可轉 100 頁。【搜尋摘要】<https://pdf.abbyy.com/finereader-pdf>、【第三方】<https://www.capterra.com/p/65868/ABBYY-FineReader/pricing/>、<https://www.pdnob.com/pdf-editor/abbyy-finereader-perpetual-license.html>
5. 使用者評價（Capterra、Gartner）：普遍稱讚辨識準確與版面保留；抱怨集中在低品質掃描辨識差、介面不直覺、Mac 版功能不如舊版、Mac 匯出錯誤訊息不明、不喜歡訂閱制。【第三方】<https://www.capterra.com/p/65868/ABBYY-FineReader/reviews/>
6. **沒有找到針對它 PPTX 輸出（文字框零碎、字型）的具體評價。**
7. ABBYY 另有給開發者的 FineReader Engine 12 SDK。官方文件：要把使用該函式庫的應用程式散布出去，需要 Runtime License（單機用 Standalone Runtime License，網路部署用 Network Runtime License）；Developer License 不允許把引擎功能放進應用程式出貨；所有授權都需要執行 Licensing Service；授權限定每期可處理的頁數或字元數；功能分模組授權。文件沒有價格，要洽當地 ABBYY 辦公室。匯出 DOCX、XLSX、PPTX 屬於哪個模組，本次沒有確認。【搜尋摘要】<https://docs.abbyy.com/fine-reader/engine/licensing>、<https://help.abbyy.com/en-us/finereaderengine/12/user_guide/distribution_redistribution>

### 3.5 Google 雲端硬碟 OCR

1. 可轉的檔案：PDF（多頁文件）或圖片檔（.jpeg、.png、.gif）；檔案應在 2 MB 以下；文字高度至少 10 像素。【搜尋摘要】<https://support.google.com/drive/answer/176692?hl=en>
2. 官方寫明：粗體、斜體、字級、字型、換行最可能保留；清單、表格、欄、註腳與章節附註很可能偵測不到。【搜尋摘要】同上
3. 輸出是 Google 文件（可再下載成 DOCX）；沒有簡報或試算表輸出。【依功能性質推論，未另查】

### 3.6 Smallpdf 與 iLovePDF

1. Smallpdf：企業定價頁把 OCR（PDF 轉 Word、PDF 轉 Excel、PDF 轉 PPT）列為團隊功能；定價頁寫免費使用者的 PDF OCR 有每日下載限制。【搜尋摘要】<https://smallpdf.com/business/pricing>、<https://smallpdf.com/pricing>
2. Smallpdf 定價：Pro 月繳每月 15 美元、年繳約每月 9 美元；團隊版每人每月 12 美元。【第三方】<https://capterra.com/p/172606/Smallpdf/pricing/>、<https://frontdeskreview.com/software/pdf-tools/smallpdf/>
3. 第三方（softpicker，2026 年）列免費限制：Smallpdf 5 MB、每天 2 次、無 OCR；iLovePDF 15 MB、無 OCR。【第三方】<https://softpicker.com/how-to-convert-pdf-to-powerpoint-free/>
4. iLovePDF：官方部落格說轉檔器把 PDF 頁面轉成可在 PowerPoint 編輯的投影片。定價頁的工具清單把「PDF to Word」與「PDF to Word (OCR)」分開列，PowerPoint 只有一項、沒有 OCR 版本。【搜尋摘要】<https://www.ilovepdf.com/pricing>、<https://www.ilovepdf.com/blog/how-to-convert-pdf-to-powerpoint-presentation-online-free>
5. iLovePDF 定價三個來源三個數字：官方定價頁摘要為年繳每月 5 美元（另有月繳 9 美元）、Capterra 9 美元、G2 7 美元。【搜尋摘要】加【第三方】
6. 第三方（MarTech Zone，2026 年）把 Smallpdf 與 Adobe 這類稱為「版面轉換器」，形容它們像數位影印機，把文字框變成可編輯形狀來複製外觀；與之相對的是讀懂內容後重新產生投影片的「AI 轉換器」。【第三方】<https://martech.zone/best-pdf-to-ppt-converters-in-2026-i-tested-8-tools-with-a-real-document/>

### 3.7 InDesign 方向：Adobe 內建、Recosoft、Markzware

1. **Adobe InDesign 現在內建 PDF 轉 InDesign 文件。** 使用說明：用檔案選單的「開啟 PDF」；需要連上網路並登入 Adobe 帳號；用「置入 PDF」則只會當成圖片放進來。【搜尋摘要】<https://helpx.adobe.com/indesign/using/convert-pdf-to-indesign-file.html>
2. Adobe 教學承認輸出不完美：圖片匯出到名為「InDesign PDF Assets」的資料夾並以連結方式存在；斷字改變可能造成溢排文字；**多欄文字框會被拆成個別文字框，可能需要自己重新串連**。【搜尋摘要】<https://www.adobe.com/lu_en/learn/indesign/web/convert-pdf-to-indesign-document>
3. Adobe Firefly Services 的 PDF-to-InDesign API（與桌面功能不同的產品）轉換說明：不支援掃描、OCR 來源、密碼保護、損毀或格式不良的 PDF；只支援 200 頁以內、150 MB 以內；偵測到超過 80 種樣式時自動建立樣式失效；不支援進階項目符號、編號或複雜表格功能；可把相關段落併入同一文字框。【搜尋摘要】<https://developer.adobe.com/firefly-services/docs/indesign-apis/guides/pdf-to-indesign-conversion-notes>
4. Recosoft PDF2ID Professional Suite 2026 於 2025-11-04 發表，新增 InDesign 2026 支援，相容 InDesign 2022 至 2026，Windows 與 macOS 皆為年訂閱 299 美元。【搜尋摘要】<https://www.recosoft.com/?p=10753>
5. PDF2ID 2026 新功能：自動建立註腳（預設開啟）、跨頁串連文字框（預設開啟）、macOS 版可同時安裝到多個 InDesign 版本。【搜尋摘要】<https://www.recosoft.com/?p=10629>
6. Recosoft 自己的轉換細節頁寫明：PDF2ID 的設計是提供編輯彈性，而不是把 PDF 百分之百純粹重建成 InDesign 格式；不提供 PDF 到可編輯 InDesign 的來回轉換。【搜尋摘要】<https://www.recosoft.com/?p=133>
7. Markzware：PDF2DTP（InDesign 外掛）已停售，由 PDFMarkz 取代；PDF2DTP 的限制是只能在 InDesign 內執行。PDFMarkz 可輸出 IDML，不需安裝 InDesign。【搜尋摘要】<https://markzware.com/company/from-legacy-to-leading-edge-markzwares-product-evolution/>
8. Markzware MarkzPortal（雲端）定價：單次使用每份文件 10 美元起（基本 7 美元加每頁 3 美元）；訂閱每月 79 美元。【搜尋摘要】<https://markzportal.markzware.com/pricing>
9. OmniMarkz 2021 年上市價為年訂閱 299 美元、買斷 399 美元；Windows 版 OmniMarkz SE 舊價為年訂閱 389、買斷 599（美元或歐元）。Markzware 自己註明這些是歷史價格。【第三方】<https://www.dpsmagazine.com/markzware-announces-omnimarkz>
10. PDF2ID 的標準版是 InDesign 外掛，安裝後從 InDesign 選單選 PDF 轉換；轉換時可選只取文字，或只還原圖片與註解。2011 年推出的 PDF2ID Enterprise 可直接把 PDF 轉成 IDML（當時需要 InDesign CS4 至 CS5.5）。現行產品線是否仍有直接輸出 IDML 的版本，沒有查到。【第三方：新聞稿與評測】<https://creativepro.com/pr-recosoft-ships-enterprise-level-pdf-to-indesign-and-pdf-to-idml-conversion-solution>、<https://creativepro.com/inreview-pdf2id>
11. **三家都沒有查到處理圖片或掃描檔的能力；Adobe 的 API 版明文排除。**

### 3.8 Adobe Illustrator

1. 官方論壇社群專家說法：PDF 的文字在 Illustrator 裡一律碎成多個輸入點；另一位使用者說文字被切成一段一段，因為兩邊的文字引擎不相符。【第三方：官方論壇】<https://community.adobe.com/questions-652/open-pdf-with-text-in-expanded-form-802776>
2. 缺字型或字型特殊時，開啟 PDF 會出現「字型已轉外框」的警告，文字變成不可編輯的路徑。【第三方：官方論壇】<https://community.adobe.com/questions-652/pdf-objects-reinterpreted-fonts-outlined-why-824095>
3. Retype：Adobe 社群專家在 2025 年 2 月表示 Retype 改為只專注字型比對；之後的使用者回報說「把點陣或外框圖稿轉成即時文字」的功能已不存在。同一則貼文說專門的「Un-outline」功能正在開發。沒有找到它已上線的證據。【第三方：官方論壇】<https://community.adobe.com/t5/illustrator-discussions/retype-not-allowing-to-edit-text/td-p/15153814>
4. Retype 測試期的限制（針對已被移除的轉換功能）：只支援拉丁文字、一次只處理一個詞。【第三方：官方論壇】<https://community.adobe.com/t5/illustrator-discussions/convert-outline-text-to-editable-text-with-retype-beta-in-illustrator/m-p/14230769>
5. Adobe 產品頁稱 Retype 可比對 Adobe Fonts 字型庫中三萬多款字型或本機字型。【搜尋摘要】<https://www.adobe.com/ca/products/illustrator/find-font.html>
6. 對照：Affinity（現屬 Canva，軟體本身免費，部分生成式 AI 功能需 Canva 付費方案）匯入 PDF 時提供兩組選項。文字處理可選「偏重可編輯」（犧牲設計還原的準確度）或「偏重保真」；文字分組可選「併成文字框」（把多行視為同一個文字框）或「保留版面」（維持原文字位置）。缺字型時可選替代字型。這些出自第 2 版的說明頁，現行版本是否相同沒有查到。【搜尋摘要】<https://affinity.help/publisher2/en-US.lproj/pages/GetStarted/importPDF.html>、【第三方】<https://www.techspot.com/downloads/7804-canva-affinity.html>

### 3.9 Canva

1. 匯入 PDF 上限：最多 500 頁、最大 300 MB。【搜尋摘要】<https://www.canva.com/help/pdf-import/>
2. 複雜度上限：PDF 含超過 1,400 個元素或圖片，或檔案太大，即超出支援的複雜度；較大的檔案可能匯入不完整或有效能問題。【搜尋摘要】<https://www.canva.com/help/import-and-edit-pdfs-canva/>
3. **掃描的 PDF 會匯入成單一張壓平的圖，文字與設計元素不能編輯**；官方建議的做法是自己用 Canva 的文字元素重做。【搜尋摘要】同上
4. 不支援的字型可能被換成相似字型；官方的修正方式是自己選別的字型。【搜尋摘要】同上
5. Canva 說會分析 PDF 並盡量保留原版面，但部分版面匯入後可能略為位移，需要小幅調整；文字與圖片可能被拆成個別可編輯元素。【搜尋摘要】同上
6. 擷取文字（Grab Text）：付費功能，內建於圖片編輯器；會嘗試用相似字型配對並放到正確位置，官方承認「有時不完美」；粗體、斜體、底線尚未支援（此說法出自英國版說明頁，可能已過時）；部分文字可能辨識不到；留下的文字殘渣要用魔法橡皮擦清。在 Canva 文件、影片等設計類型不可用。【搜尋摘要】<https://www.canva.com/help/using-grab-text/>
7. Magic Layers：2026-03-11 以公開測試推出，把扁平圖片轉成 Canva 編輯器中的可編輯設計，圖形、文字、版面元件各自成為可移動可編輯的圖層；由 Canva 自有的 Canva Design Model 驅動。【搜尋摘要】<https://petapixel.com/2026/03/11/canvas-new-ai-tool-turns-flat-images-into-editable-layers>、<https://siliconangle.com/2026/03/11/canvas-magic-layers-breathes-new-life-flat-ai-generated-images/>
8. Magic Layers 測試期限制：只支援單頁 .png 與 .jpg；初期限美國、英國、加拿大、澳洲；每次使用計入每月 AI 用量。【搜尋摘要】同上、<https://canva.com/magic-layers>
9. Canva 說明頁寫 Magic Layers 最適合平面設計、插畫與風格化視覺；寫實照片可能分離得不乾淨。【搜尋摘要】<https://www.canva.com/en_au/help/editable-magic-layers-variantb/>
10. 第三方評價：遮擋嚴重或顏色相近的區域會產生黏在一起的圖層，需手動清理；邊緣品質不一定乾淨；字型樣式、大小、位置是模型「盡量推測」。【第三方】<https://computertech.co/canva-magic-layers-review/>、<https://www.mindstudio.ai/blog/what-is-canva-magic-layers>
11. **沒有找到針對海報或傳單類的實測。**
12. 定價（第三方彙整）：Canva Pro 每月 18 美元或每年 144 美元；Canva Business 每人每月 25 美元、最少 3 人，Canva 在地化定價頁列每人每年 250 美元；另有 AI Pass 每人每月 100 美元。G2 把 Magic Layers 列在 Pro 的功能中；Business 是否包含沒有查到。【第三方】<https://www.g2.com/products/canva/pricing>、<https://piktochart.com/blog/canva-pricing-2026-costs-raised/>

### 3.10 AI 助理與簡報生成工具

1. Claude：官方說明寫可建立 Excel（.xlsx）、PowerPoint（.pptx）、Word（.docx）與 PDF；所有方案（免費、Pro、Max、Ultra、Team、Enterprise）皆可用；上傳與下載單檔上限 30 MB；使用者「可能需要檢視並修改 Claude 的輸出」。文章沒有提到把上傳的 PDF 或圖片照原版面轉成投影片，也沒有任何版面保真度的承諾。【直讀】<https://support.claude.com/en/articles/12111783-create-and-edit-files-with-claude>
2. ChatGPT：官方文件說明可在 ChatGPT 中建立、預覽、修改文件、簡報、試算表與 PDF，並稱匯出到 PowerPoint 後有意義的文字與版面物件都可編輯。這是指它自己產生的簡報，不是匯入的檔案。【搜尋摘要】<https://learn.chatgpt.com/docs/artifacts-viewer?surface=web>
3. Gemini：Canvas 中的簡報可匯出到 Google 簡報、PowerPoint 或 PDF；可上傳文件、試算表或研究檔讓它產生整份簡報。2026-06-30 Google 公告在 Google 簡報內用 Gemini 建立「完全原生且可編輯」的簡報。【搜尋摘要】<https://support.google.com/gemini/answer/16047321?hl=en>、<https://workspaceupdates.googleblog.com/2026/06/create-fully-native-and-editable-presentations-with-Gemini-in-Google-Slides.html>
4. Microsoft 365 Copilot：PowerPoint 的「從檔案建立簡報」可引用 OneDrive 或 SharePoint 上的 Word、PDF、TXT；代理模式可連結或上傳 pptx、docx、xlsx、pdf、jpg、jpeg、png。較舊的官方教學頁仍寫只支援 Word。【搜尋摘要】<https://techcommunity.microsoft.com/t5/microsoft-365-insider-blog/reference-pdf-and-txt-files-when-creating-presentations-with/ba-p/4427183>、<https://support.microsoft.com/en-au/topic/agent-mode-frontier-in-powerpoint-008f17aa-8e5f-4cda-ba6c-0588000bdad7>
5. Gamma：支援 .pptx、.potx、.docx、PDF；官方說明稱新版匯入保留原版面、設計與結構，舊版產生器只匯入文字；單則訊息附件上限 250 MB，但 PDF 與 PowerPoint 超過 100 MB 可能上傳後匯入失敗；匯入超過約 35 分鐘會停止；缺字型時換相似字型並告知。【搜尋摘要】<https://help.gamma.app/en/articles/11047840-how-can-i-import-slides-or-documents-into-gamma>
6. Gamma 使用者回報：一份 84 頁簡報的 PDF 只匯入約 3 頁、而且格式很糟。此回報可能早於新版。【第三方】<https://ideas.gamma.app/ideas/p/limportazione-da-pptx-non-funziona>
7. NotebookLM：Google 2026-03-20 的 Workspace 更新列出可把產生的簡報匯出為 PPTX（另有 PDF）。Codia 的部落格說原生「匯出到 PowerPoint」按鈕出現於 2026-02-18。【搜尋摘要】<https://workspaceupdates.googleblog.com/2026/03/new-ways-to-customize-and-interact-with-your-content-in-NotebookLM.html>、【第三方】<https://codia.ai/blog/notebooklm-native-pptx-export-vs-noteslide>
8. NotebookLM 匯出的 PPTX 能否編輯，說法矛盾：多個賣轉檔工具的廠商與部落格說裡面仍是圖片層；Geeky Gadgets 說保留了所有文字、視覺、版面與設計元素。Google 沒有說明。【第三方】<https://www.androidauthority.com/notebooklm-slide-deck-editing-3641895>、<https://www.geeky-gadgets.com/edit-notebooklm-slides/>
9. Genspark、Kimi、Manus 都有「PDF 轉 PPT」工具頁。Genspark 自稱保留標題、內文、表格與內嵌圖片，把字型對應到最接近的 PowerPoint 預設字型，並說它是「依文件結構重建投影片」而不是單純轉檔；自稱用視覺模型讀掃描與純圖片 PDF，上限 100 MB。Kimi 自稱輸出貼近原版面且可編輯。Manus 強調摘要、圖解與重新設計。全部是廠商自述。【搜尋摘要：廠商頁面】<https://genspark.ai/tools/pdf-to-ppt>、<https://www.kimi.com/capabilities/pdf-to-ppt>、<https://manus.im/tools/pdf-to-ppt>
10. Gamma 定價（第三方，互相矛盾）：2026-10-19 的擷取為 Plus 每席每月 9 美元、Pro 18 美元、Ultra 90 美元；2026 年 1 月的來源列 Pro 20 美元、Ultra 100 美元；2026 年 3 月的來源列 Plus 12 美元、Pro 25 美元。【第三方】<https://www.eesel.ai/en/blog/gamma-pricing>、<https://www.withorb.com/pricing-atlas/gamma>
11. **這一整類的共同點：多數是「讀懂內容後重新產生一份簡報」，不是「把這一頁照原樣變成可編輯」。Gamma 新版與 Genspark、Kimi 自稱保留版面，但沒有任何一家的官方文件承諾版面一致，也沒有獨立測試。**

### 3.11 主打「圖片或 PDF 轉可編輯簡報」的新創

1. 這個類別在 2026 年明顯成形，直接起因是 AI 產生的投影片（NotebookLM、圖像模型產生的整頁投影片）本質是圖片、不能編輯。多數產品的行銷頁都以「NotebookLM 轉 PowerPoint」為主打。【搜尋摘要】<https://codia.ai/noteslide/notebooklm-to-powerpoint>、<https://copyslides.com/image-to-pptx>
2. Codia NoteSlide 官方文件：用 Codia AI Vision 把每張圖解析成版面，還原文字、圖片、形狀與層級；複雜圖表、照片、截圖與重度合成的圖形可能維持圖片以保留視覺；文字可編輯但換行會不同；可上傳 NotebookLM 的 PDF、圖片型簡報或掃描投影片；可匯出 PPTX（主要格式）、PDF、投影片圖片壓縮包。【搜尋摘要】<https://codia.ai/docs/noteslide>
3. Codia 定價：免費 5 點；Starter 每月 12 美元、Pro 每月 29 美元、Ultra 每月 120 美元（年繳折扣價）；轉成可編輯 PowerPoint 每輸出一頁 1 點；開發者 API 另計，每轉一頁 13 點。【搜尋摘要】<https://codia.ai/noteslide/pricing>、<https://codia.ai/image-to-ppt-api>
4. Codia 自家頁面有一處寫圖片轉 PPTX 流程中不能選取或改寫標題，與其他頁「文字可編輯」的說法矛盾，無法判斷是特定限制還是舊頁面。【搜尋摘要】
5. CopySlides：彙整站列 Starter 每月 9 美元（每月最多 200 頁）、Plus 每月 19 美元；宣稱重建為可編輯的 PowerPoint 物件並保留版面、字型、顏色、圖表。【第三方】<https://www.capterra.com/p/10050452/CopySlides/>、<https://dynamicbusiness.com/ai-tools/copyslides-ai-powered-pdf-to-powerpoint.html>
6. 第三方彙整（EduCBA，2026 年）：多數圖片轉簡報工具只是把圖片轉成不可編輯的投影片，每個文字元素仍是鎖死的圖；作者找到一個會重建的工具，但文字密集頁、價目表、圖解式版面通常只是部分重建。【第三方】<https://www.educba.com/image-to-ppt-converter-tools/>
7. 另一份第三方說明：轉成可編輯 PPTX 時，非文字元素通常仍留在背景圖裡。【第三方】<https://2pptx.com/blog/screenshot-to-editable-powerpoint>
8. Reslide（Tosea AI）的自述文章特別註明：工具不能更正圖片中原本就存在的事實錯誤。【第三方：廠商自述】<https://medium.com/@2315610426/we-built-a-website-that-turns-slide-images-into-fully-editable-powerpoint-files-0c2f8e1bad35>
9. **沒有找到這類工具的獨立橫向評測。所有品質宣稱都來自廠商自己。**

### 3.12 開源專案（GitHub 直讀，2026-11-21）

以下數字直接取自 GitHub 公開資料介面的搜尋結果。只確認了授權欄位、星數、最後推送日與一行簡介。個別專案的說明文件與程式碼在這個工作環境讀不到，所以**沒有讀程式碼、沒有讀說明文件，也沒有確認它們依賴的模型與套件授權**。

| 專案 | 星數 | 授權 | 最後推送 | 簡介 |
|---|---|---|---|---|
| ningzimu/image-to-editable-ppt-skill | 3,460 | MIT | 2026-09-16 | 把投影片圖片、PDF、圖片型 PPTX 轉成可編輯簡報的代理人技能 |
| atharva9167j/dom-to-pptx | 392 | MIT | 2026-10-18 | 把 HTML 元素轉成可編輯的 PowerPoint 投影片（前端函式庫；屬輸出端工具，不是還原工具） |
| laihenyi/NBLM2PPTX | 352 | MIT | 2026-11-08 | macOS 上把 NotebookLM 的 PDF 轉成可編輯 PowerPoint，離線、不需 API 金鑰 |
| JuniverseCoder/MinerU2PPT | 201 | 無授權 | 2026-05-24 | 把 PDF 與圖片轉成可編輯 PPTX |
| w1163222589-coder/slide-image-to-editable-pptx | 199 | MIT | 2026-06-17 | 投影片截圖轉可編輯簡報的代理人技能 |
| knight6669/knight-imagetopptx-skill | 141 | MIT | 2026-08-24 | 語意式投影片圖片轉可編輯 PPTX 的代理人技能 |
| JadeLiu-tech/px-image2pptx | 39 | MIT | 2026-11-10 | 靜態圖片轉可編輯投影片：OCR、補圖、重建 |
| nathanfhh/nbp-web-gen | 16 | Apache-2.0 | 2026-06-10 | 含投影片轉換功能，用 OpenCV.js 或 Gemini API 補圖移除原文字 |

觀察：2026 年出現大量「代理人技能」形式的專案，做法是讓通用程式代理人看圖後寫出簡報，而不是一條固定的還原管線。這代表**門檻很低、人人都能做出能動的版本**，差異只會出現在品質的穩定度與可驗證性。來源：<https://api.github.com/search/repositories?q=image+to+editable+pptx>、<https://api.github.com/search/repositories?q=notebooklm+pptx+editable>

## 4. 建議

### 4.1 現有產品具體輸在哪裡

依查到的證據強度排列。前四項有官方文件自己承認，後幾項是使用者回報或推論。

| 弱點 | 誰有這個問題 | 證據 |
|---|---|---|
| **掃描檔或圖片直接放棄** | Canva 匯入 PDF（變成單張圖）；InDesign 的 API 版（明文不支援）；Recosoft 與 Markzware（沒有查到支援） | 官方文件（3.9 第 3 條、3.7 第 3、11 條） |
| **文字框零碎、或反過來被亂併** | Acrobat「保留頁面版面」會拆文字框群組；Acrobat 轉 PPT 把獨立文字框併成表格、把多行併成單一物件；InDesign 內建把多欄拆成個別文字框；Illustrator 開 PDF 文字碎成多個輸入點 | 官方文件加官方論壇（3.1 第 2、5、6 條；3.7 第 2 條；3.8 第 1 條） |
| **字型被換掉，而且不告訴你換了什麼、差多少** | Acrobat（論壇；掃描檔用合成字型，打新字就退回系統字型）；Canva（官方承認，只叫你自己再選）；Gamma（會告知換了哪個）；Canva 擷取文字（不支援粗斜體） | 3.1 第 7、9、12 條；3.9 第 4、6 條；3.10 第 5 條 |
| **表格靠猜** | Word（官方解釋了為什麼難）；Google OCR（官方說很可能偵測不到）；Acrobat（文字框被誤判成表格；轉 Excel 亂合併儲存格、數字擠進同一格） | 3.2 第 5 條；3.5 第 2 條；3.1 第 5、13 條 |
| **簡報、型錄、海報類版面是公認的弱項** | Word（官方明講圖多的 PDF 容易出錯）；Canva Magic Layers（寫實照片分離差、圖層黏在一起） | 3.2 第 3 條；3.9 第 9、10 條 |
| **底圖殘影** | Canva 擷取文字（官方承認會留文字殘渣，要另外清）；新創工具（非文字元素多半留在背景圖） | 3.9 第 6 條；3.11 第 7 條 |
| **重新生成而不是還原** | Claude、ChatGPT、Gemini、Copilot、Manus、Gamma 舊版 | 3.10 第 11 條 |
| **不告訴你哪裡是推論的** | 全部。只有 Excel「從圖片插入資料」會在插入前標出可疑儲存格 | 3.3 第 2 條；第 2 節 |
| **排版格式輸出只吃原生 PDF** | InDesign 內建、PDF2ID、PDFMarkz | 3.7 第 11 條 |

### 4.2 切入點

1. **首要切入點：把「圖片與掃描檔」做到跟原生 PDF 同一個等級，並且一路通到 IDML。** 這是查到最明確的空白。Canva 直接放棄掃描 PDF，InDesign 方向三家都不吃圖片。
2. **第二切入點：文字框的「分段正確」。** 現有產品不是太碎就是亂併，而且沒有任何一家把它當成可衡量的品質指標。Mortise 應該把「一個段落一個文字框、同一欄的段落可串連」當成第一級指標。
3. **第三切入點：還原度報告。** 沒有任何競品交付這個。這同時是誠實的對外承諾，也是差異化。最接近的先例是 Excel 在插入前標出可疑儲存格。
4. **第四切入點：模板輸出。** 靈感模板市場目前只有 Canva Magic Layers 沾到邊，而且它是單頁、測試中、限四個國家。
5. **第五切入點：繁體中文的表格照片轉 Excel。** Excel 內建的「從圖片插入資料」只列 21 種拉丁字母語言，沒有中文。這是一個明確、可驗證、使用者會直接撞到的缺口。
6. **不要正面對打的地方：** 以文字為主的原生 PDF 轉 Word。Word 免費內建而且就是為此設計，Acrobat 與 ABBYY 也成熟。這一塊做到「不輸」即可。
7. **要當心的地方：** 「圖片轉可編輯簡報」這個賽道在 2026 年已經擠滿了人，開源技能包拿到三千多顆星，進入門檻很低。只做投影片、只做 AI 生成的乾淨圖片，沒有差異。Mortise 的差異必須來自困難輸入（照片、歪斜、掃描）、多種輸出一致、以及可驗證的品質。
8. **可借鏡的既有設計：** Acrobat（保留流動文字或保留頁面版面）、ABBYY（精確副本或可編輯副本）、Affinity（偏重可編輯或偏重保真）都把「好編輯」與「長得像」做成使用者自己選的兩種模式。Mortise 的 Word「版面固定模式」與「可重排模式」符合使用者已有的認知，不需要另外教育市場。

### 4.3 評測基準建議

對照組（每一類挑一到兩個，全部都要親手跑同一批樣本）：

| 類別 | 對照組 | 理由 |
|---|---|---|
| 傳統轉檔 | Adobe Acrobat Pro、ABBYY FineReader PDF 16 | 業界標準；三種 Office 輸出都有；都吃掃描檔 |
| 免費基準 | Word 內建 PDF 重排、Google 雲端硬碟 OCR、iLovePDF 免費版 | 使用者的零成本替代方案；輸不得 |
| 設計平台 | Canva 匯入 PDF、Canva Magic Layers | 靈感模板市場的主要對手 |
| 排版格式 | InDesign 內建 PDF 轉換、Recosoft PDF2ID | IDML 輸出的對照 |
| 圖片轉簡報 | Codia NoteSlide、ningzimu/image-to-editable-ppt-skill | 新創代表與開源代表 |
| 重新生成型 | Claude 或 ChatGPT 直接「請把這份 PDF 轉成 PPTX」 | 使用者最可能先試的做法 |

建議量測的指標（都對應到 4.1 查到的弱點）：

1. **文字框數與段落數的比值**：每頁輸出的文字框數除以人工標註的段落數。等於 1 最好；遠大於 1 是零碎；小於 1 是亂併。
2. **文字正確率**：字元錯誤率，繁中與英文分開算。
3. **字型命中**：字型家族是否正確；不正確時替代字型的字寬差距；是否有告知使用者。
4. **表格結構**：儲存格的行列與合併是否正確；被誤判成表格的非表格區塊數。
5. **底圖殘影**：移除文字後，原文字區域與周圍背景的差異；是否有殘留筆畫。
6. **版面位置誤差**：每個元素外框與原圖的重疊比例。
7. **輸入類型的失敗率**：照片、截圖、掃描 PDF、原生 PDF、混合 PDF 各自「完全不能編輯」的頁面比例。
8. **海報類專項**：大字、疊在照片上的字、斜排字、藝術字，單獨成一組樣本。這一類沒有任何公開評測資料。

### 4.4 依授權限制的元件建議

本主題查的是競品，沒有任何一個競品成品可以當元件內嵌。就「要不要向這些廠商買技術」而言：

- **首選：不採購任何競品的引擎，自建管線。** 理由：（一）查到的競品弱點多半出在「版面重建的決策」而不是底層辨識，買引擎解決不了；（二）Acrobat 匯出背後的 Solid Framework 唯一查到的報價是每年 40 萬美元等級（第三方、未證實），不適合早期產品；（三）自建才能產出還原度報告，這是差異化所在。
- **備選（需付費）：Apryse SDK（內含 Solid Framework 的 PDF 轉 Office）或 ABBYY FineReader Engine 12 SDK**，只在自建的 DOCX 或 XLSX 重排品質長期追不上時才評估。兩者都是專有商業授權、可商用。ABBYY 的官方文件明寫隨應用程式散布需要另購 Runtime License，並且要一併安裝它的授權服務程式；Apryse 入門套件 1,500 美元起、模組化加購，散布條款要看合約。兩者的實際報價本次都沒有查到。
- **可參考但不可直接依賴：MIT 授權的開源專案**（ningzimu/image-to-editable-ppt-skill、laihenyi/NBLM2PPTX、JadeLiu-tech/px-image2pptx）。程式碼授權合格，可以讀它們的做法、拿來當評測對照組。但它們依賴的模型與套件授權沒有查，不能直接打包。
- **排除：JuniverseCoder/MinerU2PPT。** 沒有授權檔，等同保留所有權利。
- **雲端 API 型（Adobe PDF Services、Adobe Firefly Services 的 PDF-to-InDesign、Codia 開發者 API）**：可商用於線上服務，但不能隨桌面版散布，且使用者檔案會送到第三方。只適合當評測對照，不適合當核心。

## 5. 查不到的事

1. **沒有任何獨立、同樣本、橫跨這些產品的量化評測。** 文字框數、字型命中率、表格還原率、底圖殘影，沒有任何公開數字。這些必須自己測。
2. **海報、傳單類的失敗情形**：沒有找到任何產品在這一類的實測或使用者回報。只有 Word 官方說圖多的 PDF 容易出錯，以及 Canva 說寫實照片分離較差。
3. **繁體中文表現**：沒有找到任何一個競品在繁體中文文件上的轉換評價。
4. **Excel「從圖片插入資料」的語言清單是否在近期更新過**：搜尋工具轉述的現行支援頁只列 21 種拉丁字母語言、沒有中文，但我沒有親自打開該頁。
5. **Acrobat 現行版本是否仍使用 Solid Documents 的技術**：只查到 2010 至 2013 年的授權紀錄。
6. **Acrobat 把掃描檔匯出成 Word 或 PowerPoint 之後，合成字型在輸出檔裡變成什麼**：只查到在 PDF 內編輯時會合成自訂字型或改用系統字型，沒有查到匯出後的字型行為。
7. **Adobe InDesign「PDF 轉換的支援與限制」官方頁內文**：搜尋只回傳選單文字，內文沒讀到。
8. **Recosoft PDF2ID**：是否處理掃描檔、現行版本是否直接輸出 IDML（只查到 2011 年的 Enterprise 版）、缺字型時怎麼處理，都沒有查到。
9. **Markzware PDFMarkz 與 OmniMarkz 的現行定價**：只查到 2021 年歷史價格與雲端版 MarkzPortal 的價格。
10. **iLovePDF 的 PDF 轉 PowerPoint 是否對掃描檔做 OCR**：定價頁工具清單沒有 OCR 版本，但沒有明確說「不支援」。定價三個來源三個數字。
11. **Smallpdf、Canva、Gamma 的現行官方定價**：定價頁打不開，只有第三方數字，而且 Gamma 的數字互相矛盾。
12. **Word 對掃描 PDF 是否做 OCR**：微軟社群回覆多數說不會，但有一則相反報導，官方文件的明確敘述沒查到。
13. **NotebookLM 匯出的 PPTX 到底能不能編輯**：第三方說法矛盾，Google 沒有說明。
14. **Microsoft Lens 確切退役日期**：各來源不一致（2025 年秋到 2026 年 3 月之間）。
15. **圖片轉簡報新創（Codia、CopySlides、Image2PPT、SlideForge、Reslide 等）的實際品質**：只有廠商自述。Codia 自家頁面對「文字能否編輯」還有前後矛盾。
16. **Apryse SDK、ABBYY FineReader Engine SDK 的實際報價**：Apryse 只查到「1,500 美元起、模組化」；ABBYY 不公開。兩者匯出 Office 格式各屬哪個模組也沒確認。Adobe PDF Services API 只查到免費額度（官方兩頁數字不一致），付費價格不公開。
17. **開源專案依賴的模型與套件授權，以及它們的實際做法**：只從 GitHub 搜尋結果查到專案本身的授權欄位；說明文件與程式碼讀不到。
18. **ChatGPT、Gemini、Copilot 把上傳的 PDF 或圖片「照原版面」轉成可編輯簡報的實際表現**：官方文件都沒有這種承諾，也沒有找到實測。
19. **Affinity 現行版本匯入 PDF 的行為**：只查到第 2 版的說明頁。
20. **Recosoft PDF2Office（PDF 轉 Word、Excel、PowerPoint）的現況與定價**：只查到多年前的版本與價格。
21. **PlusAI、MarTech Zone 等實測文章的逐項結果**：文章打不開，搜尋摘要只給了結論句，沒有每個工具的具體失敗情形。
22. **Nitro、Foxit、PDFelement、Able2Extract 等其他桌面轉檔軟體的 PowerPoint 輸出品質**：只查到價格，沒有查到品質資料。

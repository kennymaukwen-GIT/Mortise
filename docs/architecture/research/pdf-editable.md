# 查證紀錄：輸出「最好編輯」的 PDF，與現有產品的程度

- 查證日期：2026-11-21
- 適用產品：Mortise（榫）文件反編譯 SaaS，未來可能有桌面版
- 授權底線：所有元件必須可商用；排除 GPL、AGPL 元件；排除非商用授權的模型權重
- 範圍：一般 PDF 編輯軟體怎麼處理文字、字型、向量圖；「掃描檔或影像 → 可編輯 PDF」的現有產品；讓 PDF 好編輯的寫法；ReportLab、pikepdf、pypdfium2（PDFium）做得到哪裡
- 舊研究已有的事實不重查，直接引用：pdf.md（函式庫授權與能力）、fonts.md 3.7（內嵌字型的授權）、formats.md 3.9（Illustrator）、market.md 3.1、3.4、3.7 至 3.9（競品）

## 0. 查證方式與限制

| 來源 | 狀況 |
|---|---|
| 網路搜尋 | 可用 |
| github.com、raw.githubusercontent.com、gitlab.com、pypi.org | 可直接讀取 |
| helpx.adobe.com、www.adobe.com | **無法讀取**（被工作環境的網路規則擋下）。任務要求直讀 helpx 的說明頁，**做不到**；Adobe 的說明與論壇全部只有搜尋摘要 |
| www.foxit.com、help.pdf-xchange.com、pdf.wondershare.com、pdf.abbyy.com、support.apple.com、help.libreoffice.org、wiki.inkscape.org、pdfa.org、verapdf.org、opensource.adobe.com（ISO 32000-1 的免費副本） | **無法讀取**。這些廠商的說明只有搜尋摘要 |

- 標註：**【直讀】** 親自讀到官方原文（原始碼、LICENSE、套件中繼資料）；**【實測】** 在本機跑程式得到的結果（見 5.6）；**【搜尋摘要】** 搜尋工具轉述官方頁面，我沒有打開該頁；**【第三方】** 論壇、部落格、廠商宣傳（多數也只看到搜尋摘要）；**【推論】** 我自己的推論，沒有來源背書。
- ISO 32000-1 的原檔讀不到。規格條文有兩個替代來源：pdf-raku 專案逐字轉錄的規格表格（只有字典表格，沒有運算元表格），標為【第三方：規格轉錄】；veraPDF 的 PDF/UA-1 規則檔，標為【直讀】。
- **這個環境沒有任何 PDF 編輯軟體。** 實測只能證明「檔案裡寫了什麼、PDFium 與 Poppler 怎麼讀」，凡是「某個編輯器打開後會怎樣」都沒有實測。
- 本機套件：reportlab 5.0.1（BSD）、pikepdf 10.18.2（MPL-2.0）、pypdfium2 5.13.0（BSD-3-Clause、Apache-2.0；內含 PDFium 153.0.7999.0）、fontTools 4.68.0（MIT），授權皆讀自套件中繼資料【直讀】。reportlab 與 pypdfium2 比 pdf.md 記錄的最新版（5.1.0、5.15.2）舊，與版本有關的結論另外讀了最新版原始碼。

## 1. Acrobat「編輯 PDF」對文字的行為

**本節沒有任何一條直讀。**

1. **字型條件。** 官方說明頁〈Edit text in PDFs〉：系統上裝有該字型才能編輯那段文字；字型沒裝但有內嵌，只能改顏色或字級；沒裝也沒內嵌，完全不能編輯。Distiller 的說明頁同一說法：內嵌字型一定可供檢視與列印，編輯仍須安裝。【搜尋摘要】[Edit text in PDFs](https://helpx.adobe.com/acrobat/using/edit-text-pdfs1.html)、[Distiller 字型處理](https://helpx.adobe.com/acrobat/kb/font-handling-in-acrobat-distiller.html)
2. **子集與完整內嵌。** 官方頁面沒有區分兩者，條件都是「系統有裝」。「子集只含原文件用到的字，不能拿來打新字」是第三方的說法。【第三方】[WPS](https://www.wps.com/office-quick-solution/pdf-troubleshooting/fix-missing-system-fonts-when-editing-a-pdf-in-acrobat/)。結論：對 Acrobat，完整內嵌換不到可編輯性。
3. **字型不在時。** 官方的解法是把文字改成系統上有的字型。偏好設定「內容編輯 > 字型選項」有「編輯用的後備字型」與「新增文字的預設字型」兩個選單；選「讓 Acrobat 選擇」時依文字的書寫系統自動挑後備字型。【搜尋摘要】[No Available System Font](https://helpx.adobe.com/acrobat/kb/error-no-available-system-font.html)、[後備字型設定](https://helpx.adobe.com/acrobat/current/default-font-pdf-editing-add-text.html)。論壇：這組選項只有訂閱版有；日文會挑到哪個後備字型，Adobe 沒有文件也不保證。【第三方】[論壇](https://community.adobe.com/questions-9/font-options-missing-in-content-editing-1301002)、[論壇（日文）](https://community.adobe.com/questions-9/acrobat-日本語代替フォントのデフォルト仕様-1550809)
4. **嵌入許可。** 論壇說法：Acrobat 遵守字型的嵌入旗標；「預覽與列印」等級的字型可以內嵌，但內嵌的字型不得用來繼續編輯文件。【第三方】[論壇](https://community.adobe.com/t5/type-typography-discussions/preview-and-print-restrictions-in-pdf/m-p/6704135)
5. **文字框怎麼來。** 官方頁面只說：每個文字框各自獨立，在一個框裡加字不會推動相鄰的框，也不會流到下一頁；段落與清單會被偵測出來。【搜尋摘要】（同第 1 點）。**分組規則沒有官方文件。** 社群專家的說法一致：PDF 裡沒有文字框，是進入編輯模式的當下由演算法猜的；使用者不能控制，也不能手動合併或拆開；演算法各版本不同（有人回報 DC 18.9 又變回一行一框）；大的空白會把一個框切成兩個；OCR 產生的檔案可能一個字一個框。【第三方】[論壇 1](https://community.adobe.com/t5/acrobat-discussions/text-boxes-when-editing-a-pdf/td-p/15486877)、[論壇 2](https://community.adobe.com/questions-9/how-to-stop-acrobat-from-breaking-up-text-into-two-or-more-bounding-boxes-1245992)、[論壇 3](https://community.adobe.com/t5/acrobat-discussions/quot-edit-pdf-quot-bounding-boxes/m-p/9230338)
6. **分組是否參考標記結構：查不到。** 沒有任何來源說編輯模式會讀結構樹。官方只說標籤決定閱讀順序、螢幕報讀與小螢幕重排。【搜尋摘要】[無障礙功能](https://helpx.adobe.com/acrobat/using/accessibility-features-pdfs.html)
7. **Tagged PDF 對「匯出成 Word」：查不到 Adobe 官方說法。** 找到的只有：InDesign 說明頁寫，在 Acrobat 把 PDF 匯出成 HTML 或 XML 時，標成 H1、H2、P 的段落會正確呈現【搜尋摘要】[InDesign 說明](https://helpx.adobe.com/ae_en/indesign/using/structuring-pdfs.html)；論壇的社群專家說要得到最好的匯出結果應該用結構良好的 Tagged PDF【第三方】[論壇](https://community.adobe.com/t5/acrobat-discussions/tagged-pdf/m-p/9897513)。market.md 3.1 記錄的匯出問題都沒有提到來源檔有沒有標籤。
8. **中日韓。** 官方語言支援頁：裝了亞洲語言字型套件後，幾乎所有功能都支援繁中、簡中、日、韓文字。【搜尋摘要】[語言支援](https://helpx.adobe.com/acrobat/using/asian-cyrillic-right-to-left.html)。沒有查到針對中日韓文字編輯的限制清單；**直排文字的編輯行為查不到**。論壇個案：編輯 Noto Sans 的文字時被換成預設字型。【第三方】[論壇](https://community.adobe.com/questions-9/noto-sans-fonts-unavailable-for-editing-in-adobe-acrobat-after-update-1301332)

## 2. 其他編輯器

| 軟體 | 文字怎麼分組 | 字型 | 等級與來源 |
|---|---|---|---|
| Foxit PDF Editor | 「編輯文字」像文書處理器一樣編輯段落，到邊界自動換行。另有「連結與合併文字」：手動把多個文字區塊連成一段（可跨頁重排）、合併或拆開；合併與拆開文字物件是 Pro 功能 | 使用手冊：系統裝有該字型才能新增、貼上或取代文字 | 【搜尋摘要】[編輯文字物件](https://www.foxit.com/resource-hub/user-guide/foxit-pdf-editor-for-windows/edit-text-objects/)、[段落編輯](https://resource-cms.foxit.com/foxit-pdf-editor-for-windows/paragraph-editing)、[連結文字](https://resource-cms.foxit.com/foxit-pdf-editor-for-windows/link-and-unlink-text-objects) |
| PDF-XChange Editor | 論壇版主：「編輯文字」工具以區塊模式編輯；「編輯物件」工具才照檔案裡原本的內容項目選取與編輯文字物件。區塊怎麼劃分查不到 | 客服：要用到字型的全部功能須在 Windows 安裝該字型，內嵌字型常缺資訊 | 【第三方：官方論壇】[區塊模式](https://forum.pdf-xchange.com/viewtopic.php?p=152531)；字型那句的搜尋摘要沒有指明出自哪一串，候選是[這一串](https://forum.tracker-software.com/viewtopic.php?p=127180) |
| Wondershare PDFelement | 兩種模式可切換：段落模式（整段一個區塊）、行模式（每行一個區塊） | 加字型的方式是裝進系統字型資料夾；缺字型時的行為查不到 | 【搜尋摘要】[文字模式](https://support.wondershare.com/how-tos/pdfelement/edit-text-with-different-modes-pdfelement-windows.html)、[加字型](https://support.wondershare.com/how-tos/pdfelement/how-to-add-particular-font-in-pdfelement.html) |
| macOS 預覽程式 | **不能編輯既有文字**，只能加註解與新的文字方塊 | 不適用 | 【搜尋摘要】[Apple 使用手冊](https://support.apple.com/guide/preview/prvw11580/mac) |
| Affinity | 匯入時可選「併成文字框」或「保留版面」 | 缺字型可選替代字型 | 見 market.md 3.8 第 6 點 |
| Canva | 文字與圖片拆成個別元素，版面可能位移；掃描檔匯成一張圖 | 不支援的字型換成相似字型 | 見 market.md 3.9 |
| Illustrator | 最好一行一個點狀文字；有字距微調時拆得更碎 | 要裝字型；子集內嵌會被當成缺字型 | 見 formats.md 3.9 |
| Inkscape | 內建匯入器：每個 PDF 文字物件（BT 到 ET）一個 `<text>`；物件內只要 Tm 改變，或改了顏色、線寬、渲染模式、座標矩陣，就結束目前的 `<text>`；Td、T*、TL、Tc 不會。同一個 `<text>` 內，字型樣式改變或基線位置差超過 0.1 就開新的 `<tspan>`；渲染模式 3 的不可見文字直接丟掉 | 六個選項：缺的字型畫成外框、替換缺的字型（預設）、保留缺字型的名稱、刪除缺字型的文字、全部畫成外框、全部刪除 | 【直讀】[svg-builder.cpp](https://gitlab.com/inkscape/inkscape/-/blob/master/src/extension/internal/pdfinput/svg-builder.cpp)、[pdf-parser.cpp](https://gitlab.com/inkscape/inkscape/-/blob/master/src/extension/internal/pdfinput/pdf-parser.cpp)、[pdf-input.h](https://gitlab.com/inkscape/inkscape/-/blob/master/src/extension/internal/pdfinput/pdf-input.h) |
| LibreOffice Draw | 每一行、甚至每一段屬性不同的文字各成一個文字方塊，段落間距遺失（缺陷 32249，未修）。6.4 起有「合併文字」可手動併成一個可重排的方塊，但常要重調格式，不能一次處理所有頁 | 查不到 | 【搜尋摘要】[缺陷 32249](https://www.libreoffice.org/bugzilla/show_bug.cgi?id=32249)、[合併文字](https://help.libreoffice.org/7.6/en-US/text/sdraw/01/consolidatetext.html)、[問答](https://ask.libreoffice.org/t/pdf-text-editing-issue-all-sentences-imported-as-separate-text-boxes/129870) |

三個共同點：

1. 編輯既有文字都靠系統字型（Acrobat、Foxit 官方明講，PDF-XChange 是客服說法）。內嵌字型只保證顯示。
2. 段落是各家演算法在開檔時猜的。Acrobat、Foxit、PDFelement、Affinity 會併段，PDF-XChange 以「區塊」為單位；Illustrator、LibreOffice Draw 不併；Canva 沒有說明。
3. 唯一讀得到原始碼的 Inkscape，分組直接取決於 PDF 的文字物件邊界與 Tm。向量圖方面，Inkscape 可依 XObject 或圖層分組【直讀】（同上）；其他軟體怎麼處理向量圖，查不到比舊研究更多的資料。

## 3. 「掃描檔或影像 → 可編輯 PDF」的現有產品

| 產品 | 輸出什麼 | 字型怎麼來 | 已知弱點 | 價格 | 等級與來源 |
|---|---|---|---|---|---|
| Acrobat Pro「掃描與 OCR」 | 三種輸出。「可搜尋的影像」與「可搜尋的影像（精確）」是原圖加不可見文字層。**「可編輯的文字與影像」（舊名 ClearScan）把辨識出的字元換成真的文字**，沒辨識出的字元影像原樣保留，背景留一份低解析度副本 | 合成一個貼近原字的自訂字型，只含出現過的字元（market.md 3.1 第 12 點）；或改用系統上最接近的字型 | 官方有一篇〈編輯掃描 PDF 時文字變亂碼〉；論壇回報文字扭曲（圖文混排頁最嚴重）、部分區塊只被當成影像、含少量文字的圖片被處理到模糊、不能一次改多個文字框的字型。Standard 版不支援此模式 | Pro 每月 19.99 美元（market.md 3.1） | 【第三方】[輸出選項](https://community.adobe.com/t5/acrobat-discussions/recognize-text-output-description-of-options/m-p/12986551)、[扭曲](https://community.adobe.com/questions-9/when-i-run-recognize-text-in-a-pdf-the-document-text-becomes-distorted-1275902)；【搜尋摘要】[亂碼](https://helpx.adobe.com/acrobat/kb/acrobat-text-garbled-while-using-edit-in-scanned-pdfs.html)、[Standard 版](https://helpx.adobe.com/acrobat/kb/is-ocr-or-text-recognition-available-in-acrobat-std-dc.html) |
| ABBYY FineReader PDF 16 | 三種。「僅文字與圖片」：存辨識出的文字與圖片，丟掉頁面背景。「文字在頁面影像之上」：保留原背景與圖片，辨識文字蓋在上面。「文字在頁面影像之下」：不可見文字層 | 查不到 | 官方自己寫「僅文字與圖片」的外觀可能與原件略有不同 | Standard 每年 99 美元（market.md 3.4） | 【搜尋摘要】[PDF 類型](https://help.abbyy.com/en-us/finereader/16/user_guide/pdf_types)、[兩種模式的差別](https://support.abbyy.com/hc/en-us/articles/19098939370515-The-difference-between-the-Text-over-the-page-image-and-Text-under-the-page-image-options-in-Searchable-PDF-format-settings) |
| Foxit PDF Editor | 「可編輯文字」：把影像文字換成可編輯文字。「可搜尋的文字影像」：不可見文字層 | 把影像文字的形狀與系統上已安裝的字型比對，取最接近的 | 官方線上版頁面自己提醒可能改變 PDF 的外觀；沒把握的字標成待確認 | 每年 129.99 美元，買斷 209.99 美元，含 OCR | 【搜尋摘要】[辨識文字](https://resource-cms.foxit.com/foxit-pdf-editor-for-windows/recognize-text)、[線上版](https://www.foxit.com/ocr-pdf/)、[定價](https://www.foxit.com/pdf-editor/pricing/) |
| PDFelement | 「可編輯文字」：用標準數位字型整個取代文字影像。「可搜尋的文字影像」 | 「標準數位字型」，細節查不到 | 查不到 | 來源不一：每年 79.99 至 129 美元 | 【第三方：廠商文章】[OCR 教學](https://pdf.wondershare.com/how-to/ocr-pdf.html)、[G2](https://www.g2.com/products/wondershare-wondershare-pdfelement/pricing) |
| Readiris PDF（Canon／IRIS） | 官方只說轉成可編輯、可搜尋的檔案，138 種語言；PDF 有哪些輸出模式查不到 | 查不到 | 查不到 | 來源互相矛盾（買斷約 69 美元、每年 99 美元） | 【搜尋摘要】[Canon](https://www.canon-europe.com/business/products/software/readiris/)、【第三方】[Capterra](https://www.capterra.com/p/275865/Readiris-PDF/pricing/) |
| OCRmyPDF 17.14.2 | **只在影像下方加 OCR 文字層**，供搜尋與複製；功能清單沒有任何可編輯輸出 | 不適用 | 不是可編輯 PDF | 免費，MPL-2.0 | 【直讀】[README](https://github.com/ocrmypdf/OCRmyPDF/blob/main/README.md)、[PyPI](https://pypi.org/project/ocrmypdf/) |
| 線上工具 | Adobe 線上 OCR、Soda PDF：可搜尋或匯出成其他格式。Smallpdf 自稱產生「帶可編輯文字的可搜尋 PDF」，實際寫了什麼查不到 | 查不到 | 查不到 | 見 market.md 3.6 | 【搜尋摘要】[Adobe](https://www.adobe.com/acrobat/online/ocr-pdf.html)、[Smallpdf](https://smallpdf.com/lp/pdf-ocr) |

### 3.1 創辦人的判斷哪裡對、哪裡不對

判斷原文：「轉 Word、PPT、Excel 很多軟體都能做，可編輯 PDF、SVG、Illustrator、InDesign 沒人做好。」

1. **轉 Office 很多人做：對。** 見 market.md 3.1 至 3.6。
2. **可編輯 PDF「沒人做」：不對。** Acrobat Pro、ABBYY、Foxit、PDFelement 四個桌面產品都有「把掃描影像換成真文字」的輸出模式，年費 80 至 240 美元；這不是新功能，Acrobat 的這個模式前身叫 ClearScan。
3. **可編輯 PDF「沒人做好」：有依據，但證據薄。** 查得到的弱點是：字型是合成的或從系統字型硬配；外觀會變（ABBYY、Foxit 官方自己寫）；文字框交給編輯器開檔時去猜，OCR 檔可能碎到一字一框（第 1 節第 5 點）。**這些全是官方的但書與論壇個案。沒有找到任何獨立評測，也沒有任何針對海報、簡報這類設計稿的資料**，所以「它們在設計稿上做不好」目前是推測。
4. **原生 PDF 進來時，「可編輯 PDF」幾乎不是賣點。** 原檔已經是各編輯器能編輯的最好狀態，整頁重寫只會持平或更差。有價值的只剩「頁面局部被壓平、或文字被轉成外框」的部分（第 5.4 節）。
5. **SVG：大致對。** 影像轉向量的工具（Vectorizer.ai、Vector Magic、Illustrator 影像描圖、Figma 向量化）輸出都是路徑，文字不是文字；一篇 2026 年的教學與一篇評測都這樣歸納。唯一宣稱輸出可編輯文字的是 SciDraw（科學圖表用），只看到鏡像站，未確認。【第三方】[教學](https://dev.to/localfaceswap/4-ways-to-convert-scientific-images-to-editable-svg-2026-guide-2ili)、[評測](https://automateed.com/ai-vector-review)
6. **Illustrator：對，但原因是格式的天花板。** 第三方寫不出 Illustrator 的私有資料，誰來做都只能交一般 PDF，文字最好一行一個物件（formats.md 3.9）。我們也一樣。
7. **InDesign：部分不對。** 原生 PDF 轉 InDesign 已有 Adobe 內建功能、Recosoft、Markzware 三個產品；三者都不處理圖片或掃描檔（market.md 3.7）。空缺在「影像或掃描 → InDesign」。

## 4. 讓 PDF 最好編輯的寫法

### 4.1 文字物件與運算元

1. 【實測】PDFium 把每一個 Tj（或 TJ）當成一個獨立的文字物件：一個 BT 區塊裡用 T* 換行的兩行，列舉出來是兩個 TEXT 物件。對用這種模型讀檔的軟體，「物件」的單位是一次顯示運算，不是 BT 區塊。
2. 【直讀】Inkscape 以 BT 區塊、Tm 與顏色變更為界（第 2 節）。同一段要在 Inkscape 裡成為一個多行文字，必須是：一個 BT 區塊、只在開頭下一次 Tm、之後用 TL 加 T*（或 Td）換行、段內不改顏色與渲染模式。每行各下一次 Tm 就會變成一行一個物件。
3. Illustrator：TJ 陣列裡有逐字位移（字距微調）時拆得更碎，改用整體字距（Tc）可維持整行。這是意見回報區的留言，沒有官方確認（formats.md 3.9 第 2 點）。【搜尋摘要】
4. Acrobat：大的空白會把文字框切開（第 1 節第 5 點）。【第三方】
5. **【推論，未驗證】對各家都不吃虧的寫法：** 一段一個 BT 區塊；顏色與渲染模式在第一個字之前設好，段內不改；每行一個 Tj、整行一個字串、同一個字型；行與行用 TL 加 T*；要對齊原圖寬度時用整段一致的 Tc（必要時 Tw、Tz），不用 TJ 逐字位移；字與字之間用真的空白字元，不用位移跳過去。這樣寫，PDFium 系看到「一行一物件」，Inkscape 看到「一段一物件」，Acrobat、Foxit 交給它們自己的演算法併段。**沒有任何來源能保證 Acrobat 會因此併成一段。**
6. Tc 有代價：PDFium 重新產生內容時不寫 Tc、Tw（5.4 第 2 點）【直讀】【實測】。檔案之後若被以 PDFium 為核心的工具編輯過，字距會跑掉。

### 4.2 字型、ToUnicode、ActualText

1. **ToUnicode 一定要有。** 沒有它，PDFium 與 pdfminer.six 都抽不出中文（pdf.md 3.13 第 10 點）【實測】。PDF/UA-1 也要求每個字型把用到的字碼對應到 Unicode（規則 7.21.7-1）【直讀】（來源見 4.4）。
2. **ActualText** 的定義是「結構元素及其子項的精確替代文字」，套用範圍越小越好。【第三方：規格轉錄】[表 323](https://github.com/pdf-raku/PDF-ISO_32000-raku)。PDF Association 的技巧頁：需要 ActualText 時一律掛在 Span 上，因為它會蓋掉標籤的語意。【搜尋摘要】[技巧頁](https://pdfa.org/techniques-for-accessible-pdf/actualtext-provides-correct-extractable-characters-in-place-of-ocr-errors/UA1_Tpdf-G2_06)。用途是字形與文字對不上的地方（合字、以外框或影像保留的字），不是正常文字的替代品。
3. **內嵌方式不影響可編輯性，字型選擇才影響。** 對 Acrobat 與 Foxit，能不能改字取決於使用者系統有沒有裝該字型（第 1、2 節），子集或完整內嵌沒有差別。所以要用使用者裝得到的開放字型。【推論，未驗證】字型字典裡的名稱（去掉子集前綴後）應與字型檔的 PostScript 名稱完全相同，編輯器才比對得到已安裝的字型；比對規則本身查不到。
4. **嵌入旗標（fsType）。** 定義與「旗標不等於授權」見 fonts.md 3.7。ReportLab 遇到 0x0002 或含 0x0300 的字型，字形數超過 255 時直接拋錯，不內嵌。【直讀】[PyPI](https://pypi.org/project/reportlab/)（`reportlab/pdfbase/ttfonts.py`）
5. **中日韓要用 CID 字型。** 【實測】ReportLab 把 TrueType 寫成 8 位元的簡單字型，一個子集最多 256 個字形。一頁用到 300 個不同的中文字就拆成兩個子集字型（`AAAAAA+…`、`AAAAAB+…`）；同一行混到兩個子集的字時，該行被切成 3 個 Tj、中間換 2 次字型，直接違反 4.1 第 5 點。正確結構是 Type0 加 Identity-H 的 CID 字型：一個字型物件容納全部字形。規格：CIDFontType2 的 CIDToGIDMap 可為 Identity；Type0 的 ToUnicode 是把字碼對應到 Unicode 的 CMap。【第三方：規格轉錄】[表 117、表 121](https://github.com/pdf-raku/PDF-ISO_32000-raku)。PDF/UA-1：內嵌的 CIDFontType2 必須有 CIDToGIDMap（7.21.3.2-1）【直讀】。

### 4.3 Tagged PDF 的實際效果

1. **最小結構**（【實測】寫得出來，PDFium 與 Poppler 都讀得回來，見 5.2）：內容串流裡每段包在 `/P <</MCID n>> BDC … EMC`；裝飾與背景包在 `/Artifact … BDC … EMC`；Catalog 加 `/StructTreeRoot`、`/MarkInfo <</Marked true>>`、`/Lang`；結構樹為 Document → H1、P、Figure（`/Alt`，版面屬性 `/BBox`）等，元素的 `/K` 放 MCID、`/Pg` 指向頁面；頁面加 `/StructParents`，StructTreeRoot 的 `/ParentTree` 把每個 MCID 對回它的父元素。
2. 規格定義：`/K` 的子項可以是結構元素、整數 MCID、標記內容參照字典或物件參照；元素沒寫 `/Lang` 就沿用 Catalog 的語言；`/Alt` 是替代描述；Artifact 的 `/Type` 為 Pagination、Layout、Page 或 Background（Background 必須有 BBox）。【第三方：規格轉錄】[表 323、表 330](https://github.com/pdf-raku/PDF-ISO_32000-raku)
3. **對編輯器：查不到任何證據說 Acrobat、Foxit、PDF-XChange、PDFelement 的編輯分組會讀結構樹**（第 1 節第 6 點）。不要把標籤當成改善編輯體驗的手段。
4. **對匯出：** 只有一條 HTML／XML 匯出的官方敘述與論壇專家的說法（第 1 節第 7 點），要實測。
5. **對無障礙與重排：** 官方明講標籤決定閱讀順序、螢幕報讀與小螢幕重排。這是標籤確定有的價值。【搜尋摘要】（第 1 節第 6 點）
6. **對我們自己：** 結構樹是把「這幾行是一段」「這張圖是裝飾」存進檔案的唯一標準位置，之後任何工具都讀得到（5.3）。

### 4.4 PDF/UA-1 的最低要求

來源是 veraPDF 的 PDF/UA-1 規則檔，共 106 條機器可檢查的規則。【直讀】[PDFUA-1.xml](https://github.com/veraPDF/veraPDF-validation-profiles/blob/integration/PDF_UA/PDFUA-1.xml)。與我們的輸出有關的：

- 識別：XMP 有 `pdfuaid:part`（5-1、5-2）；檔頭版本 1.0 至 1.7（6.1-1）；Catalog 的 `MarkInfo` 裡 `Marked` 為 true（6.2-1）；`Suspects` 不得為 true（7.1-4）。
- 內容：每一塊內容不是標成 Artifact 就是掛在結構樹上（7.1-3），兩者不得互相包住（7.1-1、7.1-2）；非標準標籤要用 RoleMap 對到標準標籤（7.1-5）。
- 中繼資料：XMP 有 `dc:title`（7.1-9）；`ViewerPreferences` 的 `DisplayDocTitle` 為 true（7.1-10）。
- 語言：頁面文字、Alt、ActualText 都要能判定自然語言（7.2-21、7.2-22、7.2-34）；`Lang` 的值要是合法語言代碼（7.2-29）。
- 結構：Figure 要有替代文字（7.3-1）；標題用了就從 H1 起、不跳級（7.4.2-1）；Table 只能含 TR、THead、TBody、TFoot、Caption，TR 只能含 TH、TD（7.2-3、7.2-10）；TH 要有 Scope 或用 Headers 與 ID（7.5-1）；清單是 L → LI → Lbl、LBody（7.2-19、7.2-20）。
- 字型：全部內嵌（7.21.4.1-1）；字型字典的字寬與字型程式一致（7.21.5-1）；字碼有 Unicode 對應（7.21.7-1）；不得引用 .notdef 字形（7.21.8-1）。
- 有註解的頁面要有 `/Tabs /S`（7.18.3-1）。

## 5. 工具能力

### 5.1 ReportLab（5.0.1 實測；另讀 5.1.0 原始碼包）

1. **Tagged PDF：開源版沒有。** 兩個版本的全套件都搜不到寫出 MCID 或結構元素的程式碼（唯一的 EMC 在表單欄位的外觀串流裡），只有 Catalog 允許掛 `StructTreeRoot`、`MarkInfo` 兩個鍵。更新紀錄寫 4.0.0「合併了 accessibility 分支」、4.0.0a3 有「rml ul ol dl tagging」，但 RML 不在開源套件裡。商業版 ReportLab PLUS 的標籤能力查不到。【直讀】[PyPI](https://pypi.org/project/reportlab/)（`pdfbase/pdfdoc.py`、`CHANGES.md`）
2. **繞道可行**【實測】：`canvas._code.append('/P <</MCID 0>> BDC')` 可直接插入原始運算元，結構樹再用 pikepdf 補（5.2）。`_code` 是私有屬性，升版可能失效。
3. **CFF 外框的 OpenType：不支援。** 5.0.1 註冊 Inter-Regular.otf 時拋出「postscript outlines are not supported」【實測】；5.1.0 的 `ttfonts.py` 同一行還在【直讀】。
4. **完整內嵌：沒有選項。** `TTFont` 一律動態子集；只有字型禁止子集且字形數不超過 255 時才整個內嵌。【直讀】
5. **字型結構：** 簡單 TrueType 字型、8 位元字碼（4.2 第 5 點）。從 TTC 載入時 BaseFont 多出子字型序號（`WenQuanYiZenHei-0`），與系統上的字型名稱不同。【實測】
6. **漸層：有。** `linearGradient` 寫出軸向（Type 2）shading 與 `sh` 運算元，`radialGradient` 寫出放射（Type 3），色標用拼接函數；另有函數式（Type 1）的類別。沒有網格漸層（Type 4 至 7）的類別。【直讀】（`pdfbase/pdfdoc.py`、`pdfgen/canvas.py`）加【實測】
7. **文字運算元**【實測】：`beginText` 的文字物件，多行用 TL 加 T*，每行一個 Tj；`setCharSpace` 寫 Tc；另有 `setWordSpace`、`setHorizScale`、`setTextTransform`。正好是 4.1 第 5 點要的形式。
8. 預設每頁開頭多寫一個空的 `BT /F1 12 Tf 14.4 TL ET`，引用未內嵌的 Helvetica（PDF/UA-1 要求用於顯示的字型全部內嵌）。建立 Canvas 時把 `initialFontName` 設成已註冊的內嵌字型，這個空文字物件與 Helvetica 都不再出現。【實測】
9. `Canvas(lang=…)` 會寫 Catalog 的 `/Lang`；檔頭預設 %PDF-1.3，可用 `pdfVersion` 指定。【實測】

### 5.2 用 pikepdf 事後加結構樹

【實測】ReportLab 寫出含 BDC／EMC 的頁面後，用 pikepdf 建立 StructTreeRoot、Document、各結構元素、ParentTree（`/Nums [0 [元素…]]`）、頁面的 `/StructParents` 與 `/Tabs`、Catalog 的 `/MarkInfo` 與 `/ViewerPreferences`，並用 `open_metadata()` 寫 XMP 的 `dc:title` 與 `pdfuaid:part`。讀回結果：PDFium 的 `FPDFCatalog_IsTagged` 回傳 1，結構樹為 Document（Lang zh-TW）→ H1、P、P（Lang en-US）、Figure（Alt 正確）；Poppler 的 `pdfinfo -struct-text` 印出同一棵樹與每個元素的文字。**沒有用 veraPDF 或 PAC 驗證是否符合 PDF/UA-1**（環境裝不了）。

### 5.3 用 pypdfium2 讀結構樹

`fpdf_structtree.h` 宣告 31 個函式，其中 19 個標為 Experimental。【直讀】[fpdf_structtree.h](https://github.com/chromium/pdfium/blob/main/public/fpdf_structtree.h)。pypdfium2 5.13.0 的 raw 介面全部都有；高階介面沒有包裝。【實測】實際呼叫過的：`FPDFCatalog_IsTagged`、`FPDF_StructTree_GetForPage`、`CountChildren`、`GetChildAtIndex`、`FPDF_StructElement_GetType`、`GetAltText`、`GetLang`、`GetMarkedContentIdCount` 與 `GetMarkedContentIdAtIndex`、`GetAttributeCount`；`GetActualText`、`GetParent`、屬性值的讀取函式有綁定但沒有呼叫。頁面物件這一側用 `FPDFPageObj_GetMarkedContentID` 與內容標記函式對回去，實測每個物件都回報正確的 MCID 與標記名稱（H1、P、Figure、Artifact）。

### 5.4 PDFium 編輯介面用在「保留原檔、只替換扁平化部分」

1. 函式：`FPDFPage_RemoveObject`（Experimental，移除後要自己 `FPDFPageObj_Destroy`）、`FPDFPage_InsertObject`、`FPDFText_LoadFont`、`FPDFPageObj_CreateTextObj`、`FPDFText_SetText`、`FPDFPage_GenerateContent`、`FPDF_SaveAsCopy`。【直讀】[fpdf_edit.h](https://github.com/chromium/pdfium/blob/main/public/fpdf_edit.h)
2. **移除物件：不可行。** 【實測】移除一張圖片後呼叫 `GenerateContent`，PDFium 把該物件所在的整條內容串流重寫；一頁只有一條串流時（常見），等於整頁重寫。損失如下，前兩項在最新版原始碼裡確認仍是如此【直讀】[cpdf_pagecontentgenerator.cpp](https://github.com/chromium/pdfium/blob/main/core/fpdfapi/edit/cpdf_pagecontentgenerator.cpp)：
   - **漸層消失。** `ProcessPageObject` 只處理圖片、表單、路徑、文字四類，shading 物件沒有分支，重寫後直接不見。
   - **字距消失。** `ProcessText` 只寫 Tm、Tf、Tr 與一個 Tj，沒有 Tc、Tw、Tz，也不保留 TJ 的位移。實測原本 `0.3 Tc` 的兩行中文，每個字的位置都變了。
   - 文字物件被打散：原本一個 BT 區塊兩行，變成每行各一個 `q … BT … Tm … ET Q`。
   - 字型資源被改名，未使用的字型被丟掉。
   - 留下來的：BDC／EMC 與 MCID、結構樹、文字內容與 ToUnicode。被移除圖片的結構元素變成指向不存在的 MCID。
3. **只新增、不移除：可行但有限。** 【實測】只插入新文字物件時，原串流的位元組完全不變，新內容寫成第二條串流。但 `FPDFText_LoadFont` 整個字型內嵌（38 KB 的檔案變成 458 KB），也沒有字距的設定函式（pdf.md 3.1 第 12 點）。
4. **改用 pikepdf 做記號層級的修改：可行。** 【實測】`pikepdf.parse_content_stream` 把頁面內容拆成指令，刪掉目標圖片的 `Do`（連同包住它的 BDC／EMC 與 q、cm、Q），`unparse_content_stream` 寫回；新文字由 ReportLab 另外產生一頁，把它的內容串流接在原頁面之後（原內容包進 q／Q），字型物件用 `copy_foreign` 複製並改名；結構樹把原本的 Figure 元素改成 P、指向新的 MCID。結果：原有 104 個字的 Unicode 與座標完全不變，渲染差異只出現在被替換的區域，檔案 38 KB 變 59 KB，Poppler 讀到的結構樹正確。
5. 這個做法沒測到的風險：兩邊各自產生的子集字型可能同名（實測出現兩個 `AAAAAA+DejaVuSans`），要自己給唯一的子集前綴；圖片若在 Form XObject 裡、被多處引用、是內嵌影像（BI／ID／EI）、或被裁切路徑包住，刪除範圍要另外判斷。

### 5.5 ReportLab 以外的寫出路線

1. **pikepdf 加 fontTools 自組字型物件**【實測】：Type0 加 Identity-H；TrueType 用 CIDFontType2 加 FontFile2，CFF 用 CIDFontType0 加 FontFile3（Subtype 為 OpenType）；fontTools 做子集並保留原字形編號；自寫 ToUnicode。302 個中文字形只用一個字型物件，PDFium 讀回文字正確、渲染正常，Poppler 的 pdffonts 辨識為「CID TrueType」與「CID Type 0C (OT)」。約 60 行程式。這代表 ReportLab 的兩個限制（CFF、中文拆子集）可以不靠它解決。
2. **krilla（Rust，MIT 或 Apache-2.0，版本見 pdf.md）：** README 列出 CFF 與 TrueType 兩種字型的子集、線性、放射、掃描漸層、Tagged PDF、PDF/UA-1 驗證模式。【直讀】[README](https://github.com/LaurenzV/krilla/blob/main/README.md)。沒有實測，沒有查有無 Python 綁定。
3. **fpdf2 2.9.0：LGPL-3.0-only。** 更新紀錄寫 2.8.6 起支援 CID-keyed 的 CFF 字型；README 的功能清單有 TrueType 子集內嵌、圖片與連結的替代描述。【直讀】[PyPI](https://pypi.org/project/fpdf2/)、[CHANGELOG](https://github.com/py-pdf/fpdf2/blob/master/CHANGELOG.md)、[README](https://github.com/py-pdf/fpdf2/blob/master/README.md)。LGPL 不在授權底線的排除清單裡，但不是寬鬆授權，要用得先問法務。未實測。

### 5.6 實測紀錄

環境：Linux x86_64、Python 3.13.18，套件版本見第 0 節；Poppler 的命令列工具只當第二個讀取器，不是產品元件。腳本放在暫存目錄 `/tmp/claude-0/-home-user-Mortise/23cac33b-f54e-5b7f-a414-9498035590bc/scratchpad/research/`（會被清掉，結果以本文為準）：`exp1_reportlab.py`（5.1）、`exp2_tagged.py`（5.2、5.3、4.1 第 1 點）、`exp3_pdfium_edit.py`（5.4 第 2 點）、`exp4_pikepdf_surgery.py`（5.4 第 3、4 點）、`exp5_cid_fonts.py`（4.2 第 5 點、5.5 第 1 點）。測試檔全是自己產生的單頁合成 PDF，字型用文泉驛正黑、DejaVu Sans、Inter。

## 6. 原檔內嵌的子集字型能不能、該不該沿用

1. **頁面保留時，字型自然留著。** 【實測】用 5.4 第 4 點的做法，原頁面的字型物件原封不動，原文字照常顯示，不需要「取出」字型。這是把原文件轉存，沒有對字型程式做任何處理。
2. **拿來寫新字：不可行。** 子集只含原文件出現過的字形【第三方】（第 1 節第 2 點）；我們要補寫的正是原本沒有文字的區域，要用的字多半不在子集裡。ReportLab 也不能從 PDF 載入字型【直讀】（pdf.md 1.2）。
3. **沿用換不到可編輯性。** 字型只有內嵌、系統沒裝時，Acrobat 只能改顏色與字級（第 1 節第 1 點）。沿用的價值是顯示與原檔一致。
4. **授權面**（fonts.md 3.7 已查）：嵌入旗標沒有「可取出另作他用」的等級，旗標也不等於授權；論壇說法是「預覽與列印」等級的內嵌字型不得用來繼續編輯文件（第 1 節第 4 點）。「原頁面保留、原文字不動」屬於 fonts.md 3.7 第 10 點的第三級，仍需法務判斷，但比「整頁重寫並把字型搬過去」更接近單純轉存。
5. **結論：** 原生文字區域保留原內容串流與原字型，不動；新寫的文字一律用我們有授權的開放字型，不碰原字型程式。

## 7. 查不到的事

1. Acrobat 編輯模式把文字分成文字框的規則。沒有官方文件。
2. Acrobat、Foxit、PDF-XChange、PDFelement 的編輯分組會不會參考結構樹。
3. Acrobat「匯出成 Word」會不會用標籤，Adobe 沒有官方說法。
4. Acrobat 對直排文字、中日韓文字編輯的限制清單。
5. 各編輯器用什麼規則把 PDF 裡的字型名稱比對到系統字型。
6. ABBYY、PDFelement、Readiris 的可編輯 PDF 輸出字型怎麼來；線上工具實際寫了什麼。
7. 任何一份針對「掃描 → 可編輯 PDF」的獨立評測；任何針對設計稿（海報、簡報）的資料。
8. ReportLab PLUS（商業版）的 Tagged PDF 能力與價格。
9. Foxit、PDF-XChange、PDFelement、Affinity、Canva 怎麼處理向量圖、Form XObject、裁切路徑。
10. ISO 32000-1 文字運算元的條文原文（9.3、9.4）。本次只有實測結果，沒有讀到條文。

## 8. 給實作的建議

### 8.1 寫出器

- **拉丁文字、TrueType 字型的頁面：ReportLab 夠用。** 文字用 `beginText` 的文字物件（它寫出的正是 TL、T*、Tj、Tc 的形式），漸層用 `linearGradient` 與 `radialGradient`，標記內容用原始運算元插入。
- **中日韓文字、或 CFF 外框的字型：不要用 ReportLab 的 `TTFont`。** 用 pikepdf 加 fontTools 自組 Type0／Identity-H 字型（5.5 第 1 點，已有可跑的原型）。兩條路擇一：只把字型物件與文字內容串流自己寫，其餘仍交給 ReportLab；或整個寫出器都建在 pikepdf 上。寫出器本來就要藏在自有介面後面（formats.md 4.1），先做前者。
- 桌面版若走 Rust，krilla 同時有 CFF 子集與 Tagged PDF，是本次查到唯一兩者都有的寬鬆授權函式庫；要先實測。
- 結構樹一律在最後用 pikepdf 補。XMP、`MarkInfo`、`Lang`、`ViewerPreferences` 也在這一步寫。

### 8.2 寫法規則

1. 一段一個 BT 區塊；開頭一次 Tm；行與行用 TL 加 T*。顏色與渲染模式在第一個字之前設好；段內有變色的字時，接受它被拆開。
2. 每行一個 Tj，整行一個字串、同一個字型。不用 TJ 逐字位移。要配合原圖的行寬，用整段一致的 Tc，或微調字級。
3. 字與字之間寫真的空白字元。
4. 字型用使用者裝得到的開放字型（清單見 fonts.md 4.1），子集內嵌；BaseFont 用字型檔的 PostScript 名稱加唯一的六碼子集前綴，不要出現 `-0` 這類後綴。
5. 一律寫 ToUnicode。ActualText 只在字形與文字對不上時用，掛在 Span 上。
6. 每段包一個 MCID，對到 P 或 H1 至 H6；圖片對到 Figure 並寫 Alt 與 BBox；背景、裝飾、頁碼標成 Artifact；Catalog 與有外語的元素寫 Lang。照 4.4 的清單寫到 PDF/UA-1 的機器可檢查項目都通過為止。
7. 圖片各自是獨立的影像 XObject，不用內嵌影像；向量圖寫成路徑，漸層寫成 shading；不要把整頁包進一個 Form XObject。這幾點是為了讓各編輯器能逐一選取，屬於推論。

### 8.3 原生 PDF 的混合頁面

- **保留原內容串流，只改被壓平的部分**，用 pikepdf 的記號層級修改加附加新串流（5.4 第 4 點）。不要整頁重寫。
- **不要用 PDFium 的 `FPDFPage_RemoveObject` 加 `GenerateContent` 處理原有內容**：會丟掉漸層與字距。PDFium 只用來讀。
- 原字型不動；新文字用自己的字型，子集前綴要唯一。
- 上線前要補測：圖片在 Form XObject 裡、被重複引用、被裁切路徑包住、頁面有多條內容串流的情形。

### 8.4 產品定位

- **不要對外說「可編輯 PDF 沒人做」。** 四個既有產品都做。能主張的差異只有四個，而且每一個都要先量出來才能講：字型比對到使用者裝得到的真字型，而不是合成字型；以段落為單位寫出並帶結構樹；文字壓在圖上的設計稿，把背景補回、圖與向量各自獨立；原生 PDF 只修被壓平的部分。
- **先做基準測試再定案。** 同一批樣本（文字文件、海報、簡報、中文各若干）跑 Acrobat Pro、ABBYY、Foxit 的可編輯輸出，量每頁文字框數、字型是否為真字型、與原圖的外觀差異（方法見 market.md 4.3）。目前沒有任何數據支持「它們在設計稿上做不好」。
- 四種輸出裡，「影像或掃描 → InDesign」與「→ 帶真文字的 SVG」是查得到的空缺；Illustrator 受格式限制，對外只能說「以行為單位可編輯」（formats.md 4.6）；可編輯 PDF 是競爭最多的一項。

### 8.5 待驗證的假設

要用真的 Acrobat Pro、Foxit、PDF-XChange、PDFelement、Illustrator、Affinity、Inkscape、LibreOffice Draw 開檔測。測試檔照 5.1 至 5.5 的做法產生（5.6 的暫存腳本若還在，可直接改）。

1. 「一段一個 BT 加 T*」比「一行一個 BT 加 Tm」更容易被併成一個段落文字框。
2. TJ 逐字位移會讓 Illustrator 拆得更碎、讓 Acrobat 多切文字框；改用 Tc 不會。
3. Type0／Identity-H 的中文字型，比多個 8 位元子集字型更容易被當成同一個字型，編輯時比對得到已安裝的字型。
4. BaseFont 與 PostScript 名稱一致時，Acrobat 與 Foxit 比對得到系統字型；帶 `-0` 後綴時比對不到。
5. 結構樹不影響 Acrobat 的編輯分組，但會改善「匯出成 Word」的標題、段落與閱讀順序。
6. 5.4 第 4 點產生的檔案（多條內容串流、q／Q 包裝、新舊字型並存）在 Acrobat 能正常編輯與另存，原有文字的分組不變差；新文字與相鄰的原文字會不會被併成同一段。
7. 我們的最小結構樹加上 5.1 第 8、9 點的設定，通過 veraPDF 的 PDF/UA-1 檢查。
8. CFF 字型以 FontFile3（OpenType）內嵌，在上述編輯器都能正常顯示與編輯。

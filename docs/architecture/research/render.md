# 查證紀錄：把輸出檔重新渲染成影像的引擎（供驗證用）

- 查證日期：2026-11-20
- 適用產品：Mortise（榫）文件反編譯 SaaS，未來可能有桌面版
- 授權底線：所有元件必須可商用；排除 GPL、AGPL 元件（另有可購買商業授權者列為「需付費」）；排除非商用授權的模型權重
- 需求：把 Mortise 產出的 PPTX、DOCX、XLSX、文字可編輯的 PDF（給 Illustrator）、IDML（給 InDesign）重新渲染成影像，拿來和原始影像比對，算出還原度。理想的渲染器是「使用者最後會用來開檔的那個程式」：PowerPoint、Word、Excel、Google Slides、Illustrator、InDesign

## 0. 查證方式與限制

### 0.1 這份紀錄沒有做的事

**沒有做任何實測。** 沒有安裝 LibreOffice，沒有呼叫 Microsoft Graph、Google API 或 Adobe API，沒有比較過任何兩個引擎的渲染結果。下文關於「保真度」的內容全部來自文件、原始碼與錯誤回報，沒有任何像素差異的數字。LibreOffice 和 PowerPoint、Word 的實際差距有多大，要另外建立對照測試集才知道。

### 0.2 來源能不能讀到

| 來源 | 狀況 |
|---|---|
| 網路搜尋 | 可用 |
| raw.githubusercontent.com、github.com | 可直接讀取。Microsoft Graph、Office.js、SharePoint 的官方文件原檔，LibreOffice、ONLYOFFICE、Collabora、Apache POI、Scribus 的原始碼與授權檔，Adobe 的部分開發者文件原檔，Google API 的官方描述檔，都是從這裡讀的 |
| www.microsoft.com | 可直接讀取（Product Terms 產品條款、Unattended License 授權指引） |
| pypi.org、api.nuget.org、hub.docker.com | 可直接讀取（版本、授權欄位、發布日期） |
| learn.microsoft.com、support.microsoft.com | **無法讀取**。Graph 與 Office.js 改讀 GitHub 上的文件原檔；KB 257757 與「無人值守 RPA 注意事項」兩篇只有搜尋摘要 |
| developers.google.com、support.google.com | **無法讀取**。API 的方法說明改讀 Google 官方用戶端程式庫裡的描述檔；配額數字與匯入失真只有搜尋摘要 |
| adobe.com、helpx.adobe.com、developer.adobe.com | **無法讀取**。InDesign 的 UXP 文件與 InDesign API 文件讀到了 GitHub 上的原檔；價格、授權條款、Illustrator 的部分只有搜尋摘要 |
| libreoffice.org、wiki.documentfoundation.org、bugs.documentfoundation.org、ask.libreoffice.org | **無法讀取**。授權與功能改讀 GitHub 上的原始碼鏡像；版本說明與錯誤回報只有搜尋摘要 |
| aspose.com、onlyoffice.com、collaboraonline.com、syncfusion.com、componentsource.com | **無法讀取**。價格全部只有搜尋摘要 |

### 0.3 查證方式的標註

- **【直讀】** 直接讀到官方來源的原文：授權檔、官方文件原檔、原始碼、官方條款頁、套件登錄站的中繼資料
- **【搜尋摘要】** 搜尋工具轉述了官方頁面的內容，但我沒有親自打開該頁
- **【第三方】** 只有非官方來源（經銷商、部落格、論壇貼文）

總表的「已查證」欄：授權與主要能力都是【直讀】才寫「是」；只有一部分是【直讀】寫「部分」；全部靠搜尋摘要或第三方寫「否」。

## 1. 總表

「可否商用」指用在收費的線上服務。「可否隨桌面版散布」指把程式打包進安裝檔交給使用者。這個主題的候選都不是機器學習模型，「權重授權」一欄全部是不適用。

### 1.1 可以自己架設，授權可用

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **LibreOffice 26.8（無介面模式）** | 把 PPTX、DOCX、XLSX 轉成 PDF，再由 PDF 轉影像 | MPL-2.0 為主（原始檔標頭）；程式庫同時附 GPLv3、LGPLv3 授權文；部分檔案帶 Apache-2.0 來源聲明 | 不適用 | 可 | 可。MPL-2.0 是檔案層級的著佐權，若修改 LibreOffice 的原始檔須公開那些檔案；安裝包內第三方元件的授權要另外盤點 | `soffice --headless --convert-to pdf`；各文件類型有專用的 PDF 匯出濾鏡，可帶頁碼範圍等參數；Writer 內建數十個「與 Word 相容」的版面旗標 | 不是 Office 的排版引擎。預設字型替代表沒有 Aptos、微軟正黑體、標楷體的條目；文字自動縮放與斷行和 Office 有已知出入；`--convert-to png` 據回報只輸出第一頁 | 是（授權、指令、字型表、相容旗標皆直讀；保真度未實測） | [COPYING.MPL](https://github.com/LibreOffice/core/blob/master/COPYING.MPL)、[啟動參數說明](https://github.com/LibreOffice/help/blob/master/source/text/shared/guide/start_parameters.xhp)、[VCL.xcu](https://github.com/LibreOffice/core/blob/master/officecfg/registry/data/org/openoffice/VCL.xcu)、[IDocumentSettingAccess.hxx](https://github.com/LibreOffice/core/blob/master/sw/inc/IDocumentSettingAccess.hxx) |
| **LibreOfficeKit** | 在自己的行程內載入 LibreOffice，直接把文件畫成點陣圖 | MPL-2.0（標頭檔原文） | 不適用 | 可 | 可，條件同 LibreOffice | C 介面有 `paintTile`、`paintPartTile`、`setPart`、`getDocumentSize`、`saveAs`，可以逐頁、逐張投影片取點陣圖，不必經過 PDF | 排版引擎與 LibreOffice 相同，限制相同；我只讀了標頭檔，沒有實測 | 部分（只直讀標頭檔） | [LibreOfficeKit.h](https://github.com/LibreOffice/core/blob/master/include/LibreOfficeKit/LibreOfficeKit.h) |
| **unoserver 3.7** | 讓 LibreOffice 常駐，用用戶端送件轉檔，省掉每次啟動的時間 | MIT | 不適用 | 可 | 可 | 常駐一個 LibreOffice，經 UNO 介面轉檔 | 只是外殼，渲染結果等於 LibreOffice；沒有實測 | 是（授權與版本直讀） | [LICENSE](https://github.com/unoconv/unoserver/blob/master/LICENSE)、[PyPI](https://pypi.org/project/unoserver/) |
| **Gotenberg 9.9** | 把 LibreOffice 與 Chromium 包成 HTTP 轉檔服務的容器 | MIT | 不適用 | 可 | 不適合。它是伺服器容器，不是給桌面程式內嵌的元件 | README 自述：Office 文件經 LibreOffice 轉 PDF，支援一百多種格式 | 只是外殼，渲染結果等於 LibreOffice；容器內的 LibreOffice 版本要自己確認 | 是（授權與 README 直讀） | [LICENSE](https://github.com/gotenberg/gotenberg/blob/main/LICENSE)、[README](https://github.com/gotenberg/gotenberg/blob/main/README.md)、[Docker 標籤](https://hub.docker.com/r/gotenberg/gotenberg/tags) |
| **Apache POI（PPTX2PNG 工具）** | 用 Java 直接把投影片畫成影像 | Apache-2.0 | 不適用 | 可 | 可 | 內附 `PPTX2PNG`，輸出 png、gif、jpg、svg、pdf | 自己的繪圖實作，不是 Office 也不是 LibreOffice 的引擎；保真度沒有查到資料，也沒有實測；只處理投影片，不處理 DOCX 版面 | 部分（授權與工具存在直讀） | [LICENSE](https://github.com/apache/poi/blob/trunk/legal/LICENSE)、[PPTX2PNG.java](https://github.com/apache/poi/blob/trunk/poi-ooxml/src/main/java/org/apache/poi/xslf/util/PPTX2PNG.java) |
| **PDFium**（見 [pdf.md](pdf.md)） | 把 PDF 畫成影像。LibreOffice 轉出的 PDF、給 Illustrator 的 PDF 交付物，都由它轉成影像 | BSD-3-Clause 加 Apache-2.0 | 不適用 | 可 | 可 | 見 pdf.md | 見 pdf.md。它畫出來的是「PDF 檢視器看到的樣子」，不是「Illustrator 開啟後的樣子」 | 是（在 pdf.md 查證） | [pdf.md](pdf.md) |

### 1.2 真正的 Office 與 Google 引擎：雲端服務，或在使用者自己的程式裡執行

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **Microsoft Graph 格式轉換**（`content?format=pdf`） | 由 Microsoft 的雲端把 DOCX、PPTX、XLSX 轉成 PDF | 專有雲端服務，受 Microsoft 365 訂閱條款與 Microsoft APIs 使用條款約束 | 不適用 | 技術上可呼叫。拿自家租戶替外部客戶轉檔是否合乎條款，**沒有查到明文，需法務判斷** | 不適用（雲端服務）。桌面版可改用使用者自己的 Microsoft 帳號呼叫 | 來源格式含 doc、docx、pps、ppsx、ppt、pptx、xls、xlsm、xlsx、odp、ods、odt、rtf；回傳短效下載網址；不在計量收費清單內；另有 `format=jpg` 與縮圖端點 | 檔案要先放進 OneDrive 或 SharePoint；v1.0 文件註記轉換上限 2 MB（實際是軟上限，見 3.3）；有節流；用的是不是桌面版 Office 的排版引擎，沒有查到 | 部分（端點、格式、權限、計費、節流直讀；資料條款只有搜尋摘要） | [v1.0 文件原檔](https://github.com/microsoftgraph/microsoft-graph-docs-contrib/blob/main/api-reference/v1.0/api/driveitem-get-content-format.md)、[計量 API 清單](https://github.com/microsoftgraph/microsoft-graph-docs-contrib/blob/main/concepts/metered-api-list.md)、[節流上限](https://github.com/SharePoint/sp-dev-docs/blob/main/docs/general-development/how-to-avoid-getting-throttled-or-blocked-in-sharepoint-online.md) |
| **Office 增益集（Office.js）** | 在使用者自己開著的 PowerPoint、Word、Excel 裡取得真引擎的渲染結果 | Office.js 由 Microsoft 的 CDN 提供；增益集本身是我們自己的網頁程式 | 不適用 | 可（增益集可自行散布或上架） | 可。增益集是網頁加一份資訊清單，不內含 Office | PowerPoint：`Slide.getImageAsBase64` 回傳投影片的 PNG（需求集 1.8，正式版）。Word、PowerPoint 桌面版：`getFileAsync(Pdf)` 取整份 PDF。Excel：`Range.getImage` 回傳範圍的 PNG，`getFileAsync(Pdf)` 連網頁版都支援 | 只能在使用者開著文件時執行，不能在伺服器無人值守執行。Word 沒有「頁面轉影像」的介面，網頁版 Word、PowerPoint 也拿不到 PDF。大量授權永久版與 LTSC 不支援 1.8 | 是（介面、需求集、平台表、資源限制皆直讀；未實測） | [PowerPoint 1.8 介面表](https://github.com/OfficeDev/office-js-docs-reference/blob/main/docs/includes/powerpoint-1-8.md)、[需求集平台表](https://github.com/OfficeDev/office-js-docs-reference/blob/main/docs/requirement-sets/powerpoint/powerpoint-api-requirement-sets.md)、[Office.Document](https://github.com/OfficeDev/office-js-docs-reference/blob/main/docs/docs-ref-autogen/office/office/office.document.yml) |
| **Google Slides API 縮圖**（`presentations.pages.getThumbnail`） | 取得 Google Slides 對某一頁的實際渲染結果 | 專有雲端服務，受 Google API 條款約束 | 不適用 | 可呼叫。用使用者自己的 Google 授權操作他自己的檔案最單純 | 不適用（雲端服務） | 回傳 PNG 的短效網址（預設 30 分鐘）；寬度可選 200、800、1600、2000 像素 | 算「昂貴讀取」，配額每專案每分鐘 300 次、每使用者每分鐘 60 次（搜尋摘要）；PPTX 要先匯入成 Google Slides 才有縮圖；匯入本身會失真 | 部分（方法說明直讀；配額只有搜尋摘要） | [slides.v1.json](https://github.com/googleapis/google-api-python-client/blob/main/googleapiclient/discovery_cache/documents/slides.v1.json)、[用量上限頁](https://developers.google.com/workspace/slides/api/limits) |
| **Google Drive API 匯出**（`files.export`） | 把 Google Slides 匯出成 PDF | 同上 | 不適用 | 同上 | 不適用 | 把 Google Workspace 文件匯出成指定的 MIME 類型 | 匯出內容上限 10 MB；`thumbnailLink` 是短效連結，有跨來源限制 | 是（方法說明直讀） | [drive.v3.json](https://github.com/googleapis/google-api-python-client/blob/main/googleapiclient/discovery_cache/documents/drive.v3.json) |

### 1.3 Adobe

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **InDesign 腳本與 UXP 外掛**（在使用者的 InDesign 裡執行） | 在使用者自己的 InDesign 裡開啟 IDML、匯出影像或 PDF 回傳 | 腳本與外掛是我們自己的程式；InDesign 由使用者自備授權 | 不適用 | 可 | 可。外掛（`.ccx`）可經 Adobe Marketplace 或自有通路散布；腳本（`.idjs`）不能上 Marketplace | UXP 腳本自 v18.0 起支援 InDesign 與 InDesign Server；UXP 外掛自 v18.5 起只支援桌面版 InDesign | 只能在使用者開著 InDesign 時執行；我沒有查證「開啟 IDML 再匯出影像」這條具體路徑的介面 | 部分（UXP 的支援範圍直讀） | [UXP 簡介](https://github.com/AdobeDocs/uxp-indesign/blob/main/src/pages/introduction/index.md)、[腳本與外掛比較](https://github.com/AdobeDocs/uxp-indesign/blob/main/src/pages/introduction/next-steps/script-and-plugin/index.md) |
| **InDesign Server** | 無介面的 InDesign，在伺服器上跑腳本 | 專有，按伺服器訂閱 | 不適用 | 需付費。對外服務要 Premium 授權；能不能用在代管或 SaaS，Adobe 購買指南與經銷商的說法**互相矛盾** | 否 | 真正的 InDesign 引擎；可跑 ExtendScript 與 UXP 腳本 | 每個虛擬機各要一份授權；價格說法不一（每年 2,100、5,000、13,500 美元三個數字都出現過，對應哪一級不清楚）；Adobe 社群人員說沒有公開的統一價目 | 否（授權與價格只有搜尋摘要） | [購買指南](https://www.adobe.com/products/indesignserver/buying-guide.html)、[2021 授權更新](https://blog.developer.adobe.com/en/publish/2021/06/licensing-updates-for-developers-using-indesign-server)、[Datalogics](https://datalogics.com/adobe-indesign-server) |
| **InDesign API（Firefly Services）** | Adobe 雲端代跑 InDesign Server，把文件轉成 PNG、JPEG、PDF | 專有雲端服務，企業合約 | 不適用 | 需付費，按用量計 | 不適用（雲端服務） | `create-rendition` 端點輸出 PNG、JPEG、PDF；伺服器對伺服器的 OAuth；軟上限每分鐘 250 次、硬上限 350 次 | 文件範例的輸入是 `.indd`，**沒有查到是否接受 IDML**；輸入輸出要放在 S3、Dropbox、Azure 的預簽網址；計價單位兩份 Adobe 文件說法不同；個人帳號據回報申請不到 | 部分（端點與速率上限直讀自 GitHub 文件，該文件可能是測試期的舊版；定價與輸入格式未查證） | [README](https://github.com/AdobeDocs/indesign-api-docs/blob/main/README.md)、[FAQ](https://github.com/AdobeDocs/indesign-api-docs/blob/main/FAQs.md)、[費率表](https://helpx.adobe.com/business/enterprise/products-entitlements/manage-entitlements/fireflyservices-ratecard-ver-a.html) |
| **Illustrator 腳本**（在使用者的 Illustrator 裡執行） | 在使用者自己的 Illustrator 裡開啟 PDF、匯出影像 | 腳本是我們自己的程式；Illustrator 由使用者自備授權 | 不適用 | 可 | 可（腳本檔） | 腳本語言：JavaScript（ExtendScript）、AppleScript（macOS）、VBScript（Windows） | 沒有伺服器版的 Illustrator；UXP 在 Illustrator 據社群貼文尚未正式發布；只能在使用者開著程式時執行 | 否（只有搜尋摘要與非官方文件） | [非官方腳本指南](https://ai-scripting.docsforadobe.dev/introduction/scriptingLanguageSupport/)、[社群討論](https://community.adobe.com/questions-652/clarification-needed-is-uxp-publicly-available-for-illustrator-in-2026-1548811) |
| **Illustrator API（Firefly Services）** | Adobe 雲端把 `.ai` 轉成其他格式 | 專有雲端服務，企業合約 | 不適用 | 需付費 | 不適用 | 搜尋摘要：Rendition 服務把 `.ai` 轉成多種格式 | 沒有查到是否接受 PDF 當輸入、輸出格式、定價 | 否 | [Illustrator API](https://developer.adobe.com/firefly-services/docs/illustrator) |

### 1.4 排除，或需付費才能用

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **在伺服器上自動化執行 Microsoft Office** | 用真正的 PowerPoint、Word 在伺服器上轉檔 | 專有 | 不適用 | **不可**。產品條款明文：不得用產品向第三方提供商業代管服務 | 否 | 真引擎，保真度最高 | Microsoft 不建議也不支援；Unattended License 只能用在客戶專用硬體、Azure 虛擬機、Windows 365 Enterprise；條款另禁止把機器人產出的輸出用來開發、改進、複製產品功能或訓練機器學習模型 | 部分（產品條款與授權指引直讀；兩篇技術支援文章只有搜尋摘要） | [通用授權條款：所有軟體](https://www.microsoft.com/licensing/terms/en-US/product/ForallSoftware/MCA)、[Microsoft 365 產品條款](https://www.microsoft.com/licensing/terms/en-US/productoffering/Microsoft365/MCA)、[授權指引](https://www.microsoft.com/licensing/guidance/Microsoft-365-Unattended-License) |
| **ONLYOFFICE Docs（社群版）** | 另一套辦公文件引擎，含轉檔 | AGPL-3.0（DocumentServer、core、DocumentBuilder 三個程式庫的授權檔） | 不適用 | **排除**（AGPL） | **排除** | 自有排版引擎與轉檔 | AGPL | 是（授權檔直讀） | [DocumentServer](https://github.com/ONLYOFFICE/DocumentServer/blob/master/LICENSE)、[core](https://github.com/ONLYOFFICE/core/blob/master/LICENSE)、[DocumentBuilder](https://github.com/ONLYOFFICE/DocumentBuilder/blob/master/LICENSE) |
| **ONLYOFFICE Docs Developer Edition** | 同上，商業授權版 | 專有商業授權 | 不適用 | 需付費 | 未查證 | 同上，含白牌與 Document Builder | 經銷商列價：單一伺服器、250 個同時連線，一年 8,918 美元；開發用 1,911 美元。官方價目沒有讀到 | 否（只有搜尋摘要與經銷商） | [官方問答](https://helpcenter.onlyoffice.com/docs/faq/developer.aspx)、[ComponentSource](https://www.componentsource.com/product/onlyoffice-docs-developer/prices) |
| **Collabora Online** | 以 LibreOffice 為核心的線上辦公套件，含轉檔端點 | 原始碼以 MPL-2.0 為主（COPYING 與 README） | 不適用 | 原始碼授權允許。官方發布的 CODE 二進位檔另有條件，官方也不建議用於生產環境 | 未查證 | `POST /cool/convert-to/<格式>` 轉檔 | 渲染核心就是 LibreOffice，保真度不會比較好；轉 png 據論壇回報只輸出第一頁；CODE 二進位檔的限制條款沒有讀到 | 部分（原始碼授權直讀） | [COPYING](https://github.com/CollaboraOnline/online/blob/master/COPYING)、[鏡像 README](https://github.com/CollaboraOnline/online.mirror/blob/main/README.md)、[轉檔 API](https://sdk.collaboraonline.com/docs/conversion_api.html) |
| **Aspose.Slides、Aspose.Words、Aspose.Cells 26.11** | 不依賴 Office 的商用函式庫，把 PPTX、DOCX、XLSX 轉成 PDF 或影像 | 專有 EULA | 不適用 | 需付費。SaaS 與對外網站要 OEM 級或計量授權 | 需付費（OEM 級才涵蓋散布） | 自有排版引擎；.NET、Java、Python 等版本 | 不是 Office 的引擎，保真度未測。價位級距（美元，多為經銷商數字）：Slides for .NET 開發者小型企業 999、開發者 OEM 2,997、站台小型企業 4,995、站台 OEM 13,986；Total for .NET 開發者小型企業 3,999、站台 OEM 55,986 | 部分（版本與專有授權直讀；價格與授權類型只有搜尋摘要與經銷商） | [NuGet](https://www.nuget.org/packages/Aspose.Slides.NET)、[PyPI](https://pypi.org/project/aspose-slides/)、[官方價格頁](https://purchase.aspose.com/pricing/slides/net/)、[授權類型](https://purchase.aspose.com/policies/license-types)、[ComponentSource](https://www.componentsource.com/product/aspose-slides-net/prices) |
| **Syncfusion 文件處理函式庫** | 同類商用函式庫 | 專有；另有免費的社群授權 | 不適用 | 需付費，或符合社群授權資格 | 未查證 | 未查證 | 社群授權資格：年營收低於 100 萬美元、開發者不超過 5 人、員工不超過 10 人。付費 Team 授權每月 395 美元（最多 5 位開發者） | 否（只有搜尋摘要） | [社群授權](https://www.syncfusion.com/products/communitylicense) |
| **Scribus** | 能匯入 IDML 的開源排版軟體 | GPL-2.0 或更新版 | 不適用 | **排除**（GPL） | **排除** | 未查證 | GPL | 是（授權檔直讀） | [COPYING](https://github.com/scribusproject/scribus/blob/master/COPYING) |

## 2. 需求對照

每種輸出格式，誰能把它畫成影像。「真引擎」指使用者最後用來開檔的那個程式。

| 輸出格式 | 自己架設的替身引擎 | 真引擎，伺服器端拿得到的途徑 | 真引擎，使用者端拿得到的途徑 |
|---|---|---|---|
| PPTX（PowerPoint） | LibreOffice 轉 PDF，PDFium 轉影像 | Microsoft Graph `format=pdf`（條款適用性待法務確認） | 增益集 `Slide.getImageAsBase64`（網頁版、Windows、Mac 的 Microsoft 365 版本） |
| PPTX 匯入 Google Slides | 沒有。LibreOffice 與 PowerPoint 都不代表 Google Slides 的渲染 | Drive API 匯入後，Slides API `getThumbnail` | 同左，用使用者自己的 Google 授權 |
| DOCX（Word） | LibreOffice 轉 PDF，PDFium 轉影像 | Microsoft Graph `format=pdf`（同上） | 增益集 `getFileAsync(Pdf)`，只限桌面版與 iPad；網頁版 Word 拿不到 |
| XLSX（Excel） | LibreOffice 轉 PDF，PDFium 轉影像 | Microsoft Graph `format=pdf`（同上） | 增益集 `Range.getImage` 或 `getFileAsync(Pdf)`，網頁版也支援 |
| 文字可編輯的 PDF（給 Illustrator） | PDFium 直接畫（見 pdf.md）。這是「PDF 檢視器看到的樣子」 | 沒有查到可用的途徑。Illustrator 沒有伺服器版；Firefly 的 Illustrator API 是否吃 PDF 未查證 | 使用者的 Illustrator 跑腳本 |
| IDML（給 InDesign） | 沒有授權可用的開源渲染器。Scribus 能匯入 IDML 但是 GPL | InDesign Server（付費）、InDesign API（付費，是否吃 IDML 未查證） | 使用者的 InDesign 跑 UXP 腳本或外掛 |
| 模板（只留版面骨架） | 依實際交付格式，同上各列 | 同上 | 同上 |

## 3. 逐條事實與來源

### 3.1 LibreOffice

1. **授權。** GitHub 上的 LibreOffice 核心程式庫同時放了三份授權文：`COPYING.MPL`（Mozilla Public License 2.0）、`COPYING.LGPL`（LGPL 第 3 版）、`COPYING`（GPL 第 3 版）。原始檔的標頭寫的是「This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0」；從 Apache OpenOffice 承襲的檔案另帶 Apache 授權的來源聲明。【直讀】[COPYING.MPL](https://github.com/LibreOffice/core/blob/master/COPYING.MPL)、[COPYING.LGPL](https://github.com/LibreOffice/core/blob/master/COPYING.LGPL)、[COPYING](https://github.com/LibreOffice/core/blob/master/COPYING)、[doc.cxx 的標頭](https://github.com/LibreOffice/core/blob/master/sw/source/core/doc/doc.cxx)、[NOTICE](https://github.com/LibreOffice/core/blob/master/readlicense_oo/license/NOTICE)
2. 官方授權頁（libreoffice.org/about-us/licenses）我打不開。搜尋摘要轉述：專案對外的說法是 MPL-2.0，貢獻者以 MPL-2.0 與 LGPLv3+ 雙授權提交。【搜尋摘要】[Ask LibreOffice 討論](https://ask.libreoffice.org/t/what-is-the-right-license-lgpl-or-mpl/45115)
3. MPL-2.0 不是 GPL 也不是 AGPL，不在本產品的排除條款內。它是檔案層級的著佐權：只要不修改 LibreOffice 自己的原始檔，就沒有公開原始碼的義務。這和 pdf.md 對 pikepdf 的結論相同，請法務一併確認。（這一條是我對授權文的解讀，不是官方說明。）
4. **版本。** `libreoffice-26-8` 分支的 `configure.ac` 版本字串是 `26.8.4.0.0+`，主幹是 `27.2.0.0.alpha1+`。也就是目前的穩定系列是 26.8。【直讀】[configure.ac（26.8 分支）](https://github.com/LibreOffice/core/blob/libreoffice-26-8/configure.ac)
5. **無介面轉檔的指令。** 官方說明檔記載：`--headless` 讓程式不帶使用者介面啟動；`--convert-to 副檔名[:濾鏡名稱[:濾鏡參數]] [--outdir 輸出目錄]` 做批次轉檔，範例是 `--convert-to pdf:writer_pdf_Export --outdir /home/user *.doc`。【直讀】[start_parameters.xhp](https://github.com/LibreOffice/help/blob/master/source/text/shared/guide/start_parameters.xhp)
6. 濾鏡清單裡有 `writer_pdf_Export`、`impress_pdf_Export`、`calc_pdf_Export`、`draw_pdf_Export`、`writer_png_Export`。PDF 匯出參數說明裡有 `PageRange`、`ExportHiddenSlides`、`ExportNotesPages`、`SinglePageSheets`、`SelectPdfVersion`、`UseLosslessCompression`、`ReduceImageResolution` 等。【直讀】[convertfilters.xhp](https://github.com/LibreOffice/help/blob/master/source/text/shared/guide/convertfilters.xhp)、[pdf_params.xhp](https://github.com/LibreOffice/help/blob/master/source/text/shared/guide/pdf_params.xhp)
7. **直接轉 png 只會得到第一頁。** 多位使用者回報 `--convert-to png` 對簡報只輸出第一張投影片，論壇回覆說這是預期行為；Bugzilla 157425 討論同一件事，並被指為 tdf#137601 的重複回報。有人回報對 Writer 帶 `PageRange` 參數也仍然只出第一頁。我沒有在 26.8 上實測。【搜尋摘要】[論壇討論一](https://ask.libreoffice.org/t/errors-when-trying-to-convert-ppt-in-headless-mode/8857)、[論壇討論二](https://ask.libreoffice.org/t/batch-conversion-from-odp-to-png/21556)、[Bug 157425](https://www.libreoffice.org/bugzilla/show_bug.cgi?id=157425)。實務上的做法是先轉 PDF，再用 PDF 引擎逐頁轉影像。
8. **字型替代表。** LibreOffice 內建的預設字型替代設定檔裡：Calibri 的替代是 Carlito；Cambria 的替代是 Caladea；Arial 的替代清單以 Liberation Sans 起頭；Times New Roman 的清單含 Liberation Serif；Courier New 的清單含 Liberation Mono。PMingLiU 與 MingLiU 的替代清單是 `fzmingti;msunglighttc;mingliu;pmingliu;ming;hei;...`，裡面沒有 Noto 或思源。我在檔案裡找不到 Aptos、Segoe UI、Microsoft JhengHei（微軟正黑體）、DFKai-SB（標楷體）、Yu Gothic 的條目。【直讀】[VCL.xcu](https://github.com/LibreOffice/core/blob/master/officecfg/registry/data/org/openoffice/VCL.xcu)。我只檢查了上面列出的這幾個名稱。
9. 上一條的含意：輸出檔如果指定了 Office 內建但伺服器上沒有的字型，LibreOffice 會換成別的字型來排版，字寬不同，斷行位置就不同。Calibri、Cambria、Arial、Times New Roman 有度量相容的替代字型，Aptos（Office 自 2023 年起的預設字型）與微軟的中文字型在預設表裡沒有。（這是從字型表推論，不是實測。）
10. 搜尋找到一個聲稱與 Aptos 度量相容的開源字型 Akasia（OFL 1.1），是單一開發者的新專案，沒有被 LibreOffice 收進替代表。Aptos 本身的授權條款、能不能裝在伺服器上，沒有查到 Microsoft 的原文。【第三方】[Akasia](https://codeberg.org/bloudraad/akasia)
11. **Writer 與 Word 的排版差異，原始碼自己列了一長串。** Writer 的文件設定列舉裡有一整段「相容性旗標」，逐項對應 Word 的排版行為，例如：`JUSTIFY_LINES_WITH_SHRINKING`（註解：tdf#119908 new paragraph justification）、`MS_WORD_COMP_TRAILING_BLANKS`、`MS_WORD_COMP_MIN_LINE_HEIGHT_BY_FLY`（註解：MS Word 在某些模式下行高取決於圖形高度而非字型）、`SURROUND_TEXT_WRAP_SMALL`（註解：MS Word still wraps text around objects with less space than LO would）、`MS_WORD_COMP_GRID_METRICS`、`LINE_SPACING_AS_GAP_BELOW`（註解提到 tdf#172113，為了可互通地決定行高）、`BALANCE_SPACES_AND_IDEOGRAPHIC_SPACES`（中日韓文脈下的空格寬度）。【直讀】[IDocumentSettingAccess.hxx](https://github.com/LibreOffice/core/blob/master/sw/inc/IDocumentSettingAccess.hxx)。這證明兩件事：LibreOffice 確實在逐項追 Word 的排版行為；兩者的預設排版行為本來就不同，追到哪裡算哪裡。
12. 24.2 版本說明：Word 2013 之後對左右對齊的段落用了不同的斷行方式，LibreOffice 新增了對應的演算法，當時只用在 DOCX 互通。【搜尋摘要】[ReleaseNotes/24.2](https://wiki.documentfoundation.org/ReleaseNotes/24.2)
13. 26.8.1 維護版修了「文字對齊、段落換行、行距與 Word 不同」的問題，其中一項是相對 25.8 的退步。【第三方】（科技媒體轉述，我沒有讀到官方版本說明）。這說明同一份 DOCX 在不同 LibreOffice 版本下的斷行可能不同，升級前要跑回歸測試。
14. **簡報的文字自動縮放（PowerPoint 的「溢位時縮小文字」）和 PowerPoint 的結果有出入。** Bugzilla 上有多筆回報：tdf#122023（同一個文字方塊，Impress 算出的字級略有不同，把一個字拆到兩行）、tdf#136228（自動縮放的文字在 Impress 比 PowerPoint 高）、tdf#157773（7.5 版縮了字仍然溢出，7.6.2 修正）。另有一筆提交說明指出：PowerPoint 需要檔案裡存一個字級縮放值才能正確顯示，而這個值只是近似值，PowerPoint 編輯後會重算。【搜尋摘要】[tdf#122023](https://www.libreoffice.org/bugzilla/show_bug.cgi?id=122023)、[tdf#157773](https://www.libreoffice.org/bugzilla/show_bug.cgi?id=157773)
15. **LibreOfficeKit。** 標頭檔以 MPL-2.0 授權，文件物件的函式表裡有 `saveAs`、`setPart`、`paintTile`、`getDocumentSize`、`paintPartTile`、`renderShapeSelection`。【直讀】[LibreOfficeKit.h](https://github.com/LibreOffice/core/blob/master/include/LibreOfficeKit/LibreOfficeKit.h)
16. **外殼工具。** unoserver 3.7 於 2026-06-10 發布，MIT 授權。【直讀】[PyPI](https://pypi.org/project/unoserver/)、[LICENSE](https://github.com/unoconv/unoserver/blob/master/LICENSE)。Gotenberg 是 MIT 授權，README 寫「Office documents to PDF via LibreOffice (100+ formats)」，Docker Hub 上最新標籤是 9.9.1（2026-11-20）。【直讀】[LICENSE](https://github.com/gotenberg/gotenberg/blob/main/LICENSE)、[README](https://github.com/gotenberg/gotenberg/blob/main/README.md)、[Docker 標籤](https://hub.docker.com/r/gotenberg/gotenberg/tags)

### 3.2 Microsoft 對伺服器端無人值守執行 Office 的立場

1. **產品條款明文禁止用產品提供商業代管。** 「通用授權條款：所有軟體」的限制條款原文：「Customer may not (and is not licensed to) use the Products to offer commercial hosting services to third parties, work around any technical limitations in the Products or restrictions in Product documentation, replicate product functionality, or separate the software for use in more than one OSE under a single License ..., unless expressly permitted by Microsoft.」【直讀】[Universal License Terms: For all Software](https://www.microsoft.com/licensing/terms/en-US/product/ForallSoftware/MCA)
2. **多工條款。** 同一頁：任何用來匯集連線、減少直接或間接存取產品的裝置或使用者數量的方法（硬體、軟體或自動化），都不會減少所需的授權數量。【直讀】同上
3. **Unattended License 的定義與使用範圍。** Microsoft 365 產品條款把 RPA 定義為「用來擷取資料並操作應用程式以執行重複性工作的一個或一組應用程式」；不嚴格符合「有人在場的機器人」定義的，一律算無人值守機器人。Unattended License 只能指派給在下列環境執行的機器人：客戶專用的硬體、Azure 上的虛擬機（含 Azure Virtual Desktop）、Windows 365 Enterprise。每份授權只涵蓋一個作業系統環境、一個 Microsoft 365 Apps for enterprise 實例。【直讀】[Microsoft 365 產品條款](https://www.microsoft.com/licensing/terms/en-US/productoffering/Microsoft365/MCA)
4. **Unattended License 的使用限制，其中一條直接和「拿輸出來驗證並改進轉檔引擎」有關。** 原文：「Unattended Bots may not create or replicate activities or workflows on behalf of an unlicensed user or device.」以及「Customer may not use and may not permit any third party to use data, logs, recordings, or other output generated through Robotic Process Automation or bots to develop, train, improve, replicate product functionality, or fine tune machine learning or artificial intelligence algorithms or models.」【直讀】同上
5. **授權指引的說法。** 「Any Unattended bot that accesses Windows desktop OS or Microsoft 365 Apps must be licensed with Microsoft 365 - Unattended License.」頁面舉的無人值守例子包含「Having an application use Microsoft Word to automatically open submitted files and extract data to a CRM application」，和「程式自動用 Word 開啟使用者送來的檔案」是同一種型態。【直讀】[Microsoft 365 Unattended License licensing guidance](https://www.microsoft.com/licensing/guidance/Microsoft-365-Unattended-License)
6. **技術支援立場（KB 257757）。** Microsoft 不建議也不支援伺服器端自動化 Office。授權段落說：現行授權準則不允許在伺服器上用 Office 應用程式服務用戶端請求，除非那些用戶端自己有 Office 授權；用伺服器端自動化向未授權的工作站提供 Office 功能不在使用者授權合約範圍內。【搜尋摘要】[Considerations for server-side Automation of Office](https://support.microsoft.com/en-us/help/257757)。我打不開這一頁，原文沒有逐字核對。
7. **有了 Unattended License 之後的技術立場。** Microsoft Learn 的文章說：授權雖然允許無人值守自動化，但所有現行版本的 Office 都是設計成在有使用者在場的用戶端工作站上執行；Microsoft 目前不建議、也不支援從任何無人值守、非互動式的用戶端程式或元件自動化 Office；建議改用 Microsoft Graph 或直接處理檔案格式（Open XML）。【搜尋摘要】[Considerations for unattended automation of Office in the Microsoft 365 for unattended RPA environment](https://learn.microsoft.com/en-us/office/client-developer/integration/considerations-unattended-automation-office-microsoft-365-for-unattended-rpa)
8. Unattended License 的價格沒有查到 Microsoft 官方數字。經銷商列價約每月 39 到 43.2 美元。【第三方】[Secureframe 列價](https://secureframe.com/marketplace/microsoft/microsoft-365-e3-unattended-license-commercial)
9. 綜合以上：在自己的伺服器上跑 Office 替 SaaS 的外部客戶轉檔，同時碰到「不得提供商業代管」「不得代未授權使用者執行」「輸出不得用於改進或複製產品功能」三條，技術上又不受支援。這條路排除。（結論是我對條款的解讀；條款原文是直讀的。）

### 3.3 Microsoft Graph 把 PPTX、DOCX 轉成 PDF

1. **端點。** `GET /drive/items/{item-id}/content?format={format}`，另有以路徑定址與 `/me/drive` 的寫法。回應是 `302 Found`，轉向一個預先驗證的下載網址，文件說這個網址只在短時間內有效（幾分鐘），不需要帶 Authorization 標頭。【直讀】[driveitem-get-content-format.md（v1.0）](https://github.com/microsoftgraph/microsoft-graph-docs-contrib/blob/main/api-reference/v1.0/api/driveitem-get-content-format.md)
2. **`format=pdf` 支援的來源副檔名**（v1.0 文件原檔的完整清單）：doc、docx、dot、dotx、dotm、dsn、dwg、eml、epub、fluidframework、form、htm、html、loop、loot、markdown、md、msg、note、odp、ods、odt、page、pps、ppsx、ppt、pptx、pulse、rtf、task、tif、tiff、wbtx、whiteboard、xls、xlsm、xlsx。清單裡沒有 pptm、potx、csv。【直讀】同上
3. **`format=jpg`。** 來源清單很長，包含 doc、docx、pptx、pdf 等；必須帶 `width` 與 `height`（像素）。多頁文件回傳哪一頁，文件沒有寫。【直讀】同上
4. **檔案大小上限，官方文件與實際行為不一致。** v1.0 文件開頭有一則註記：「The maximum size limit for files to use format conversion is 2 MB.」這則註記來自 2026-10-08 合併的拉取請求 #9564（提出者帳號 westleyMS，2025-04-30 開啟），說明是依產品團隊（原文「per the PG」）的要求加上檔案大小限制，並寫到超過 2 MB 常常逾時、但有時仍會成功。beta 版的文件原檔沒有這則註記。社群問答裡有人回報 37.8 MB 的 PPTX 轉檔成功，也有人回報 19.4 MB、170 張投影片的 PPTX 失敗，另有 45 秒逾時的回報。【直讀】[v1.0 原檔](https://github.com/microsoftgraph/microsoft-graph-docs-contrib/blob/main/api-reference/v1.0/api/driveitem-get-content-format.md)、[beta 原檔](https://github.com/microsoftgraph/microsoft-graph-docs-contrib/blob/main/api-reference/beta/api/driveitem-get-content-format.md)、[PR #9564](https://github.com/microsoftgraph/microsoft-graph-docs-contrib/pull/9564)；社群回報為【搜尋摘要】[問答一](https://learn.microsoft.com/en-us/answers/questions/1634108/download-a-file-in-another-format-size-limit)、[問答二](https://learn.microsoft.com/en-us/answers/questions/1818304/timeout-error-converting-pptx-to-pdf-using-graph-a)、[問答三](https://learn.microsoft.com/en-us/answers/questions/2029392/officeconversion-fatal-error-trying-to-convert-ppt)。設計上應該把 2 MB 當成「官方保證的範圍」，超過就要有逾時與失敗的處理。
5. **權限。** 委派權限最低是 `Files.Read`；應用程式權限最低是 `Files.ReadWrite.All`。【直讀】[權限表](https://github.com/microsoftgraph/microsoft-graph-docs-contrib/blob/main/api-reference/v1.0/includes/permissions/driveitem-get-content-format-permissions.md)
6. **計費。** Graph 的 API 分三類：標準、高容量、進階。標準 API 在用量門檻內包含在使用者授權裡，不另收費。目前的計量 API 清單只有一項：SharePoint 與 OneDrive 的 `assignSensitivityLabel`（每次呼叫 0.00185 美元）。格式轉換不在清單上。【直讀】[計量 API 總覽](https://github.com/microsoftgraph/microsoft-graph-docs-contrib/blob/main/concepts/metered-api-overview.md)、[計量 API 清單](https://github.com/microsoftgraph/microsoft-graph-docs-contrib/blob/main/concepts/metered-api-list.md)
7. **節流。** OneDrive 與 SharePoint 的上限以「資源單位」計。Graph 每次請求的成本：單一項目查詢與下載檔案 1 單位；多項目查詢、建立、更新、刪除、上傳 2 單位；權限相關操作 5 單位。每個應用程式在每個租戶的上限，授權數 0 到 1,000 的租戶是每分鐘 1,250 單位、每 24 小時 1,200,000 單位，另有每小時進出各 400 GB 的流量上限。頁面註明 Microsoft 保留調降未付費或未授權使用量上限的權利。【直讀】[Avoid getting throttled or blocked in SharePoint Online](https://github.com/SharePoint/sp-dev-docs/blob/main/docs/general-development/how-to-avoid-getting-throttled-or-blocked-in-sharepoint-online.md)。文件沒有寫格式轉換這個動作算幾個單位。
8. **縮圖端點。** `GET .../thumbnails?select=c300x400` 這種寫法可以要求自訂尺寸的縮圖。它回傳的是檔案的縮圖，不是逐頁影像。【直讀】[driveitem-list-thumbnails.md](https://github.com/microsoftgraph/microsoft-graph-docs-contrib/blob/main/api-reference/v1.0/api/driveitem-list-thumbnails.md)
9. **資料處理條款。** 計量 API 總覽裡寫了一條原則：「Customer data belongs to the customer.」【直讀】。Microsoft APIs 使用條款的內容我只有搜尋摘要：透過 Graph 取得並存在本地的資料要保持最新，並執行與來源一致的更正、限制與刪除；要有保存與刪除政策；不得把取得的資料（含彙總、匿名化或衍生的資料）用於廣告行銷；要取得法律要求的使用者同意。【搜尋摘要】[Microsoft APIs Terms of Use](https://developer.microsoft.com/en-us/graph/terms-of-use)
10. **用在本產品的兩種方式，條款風險不同。** 方式一：Mortise 自己租一個 Microsoft 365 租戶，把所有客戶的輸出檔丟進去轉 PDF。這等於用 Microsoft 的線上服務替第三方處理檔案，是否觸及多工條款或其他限制，我沒有找到 Microsoft 的明文說明。方式二：使用者用自己的 Microsoft 帳號授權，檔案放在他自己的 OneDrive，由他的授權呼叫轉換。這種方式每個使用者都有自己的授權，條款上最單純，但只對有 Microsoft 365 帳號的使用者有效。（這一條是我的分析，不是查到的事實。）

### 3.4 Google Slides API 與 Drive API

1. **取得投影片縮圖。** `presentations.pages.getThumbnail` 的官方說明：「Generates a thumbnail of the latest version of the specified page in the presentation and returns a URL to the thumbnail image. This request counts as an expensive read request for quota purposes.」MIME 類型只有 PNG。尺寸選項：`SMALL`（寬 200 像素）、`MEDIUM`（寬 800）、`LARGE`（寬 1600）、`WIDTH2000_PX`（寬 2000）；不指定時由伺服器決定，而且說明寫了這個預設值未來可能改變。【直讀】[slides.v1.json](https://github.com/googleapis/google-api-python-client/blob/main/googleapiclient/discovery_cache/documents/slides.v1.json)（Google 官方用戶端程式庫內的 API 描述檔，修訂版 20260930）
2. 回傳的縮圖網址預設 30 分鐘有效，網址帶著請求者的帳號標記，拿到網址的人等同以請求者的身分存取影像；簡報的分享設定改變後可能失去存取權。【直讀】同上
3. **Slides API 配額。** 讀取：每專案每分鐘 3,000 次、每使用者每分鐘 600 次。昂貴讀取（就是 `getThumbnail`）：每專案每分鐘 300 次、每使用者每分鐘 60 次。寫入：每專案每分鐘 600 次、每使用者每分鐘 60 次。超過會收到 429，官方建議用指數退避重試。【搜尋摘要】[Usage limits](https://developers.google.com/workspace/slides/api/limits)
4. **超出配額會不會收費，兩種說法並存。** 一個版本的頁面說超出配額不會額外收費；另一個版本說預計在 2026 年稍晚開始向 Google Cloud 帳單帳戶收取超額費用。生效日期與費率都沒有查到。【搜尋摘要】同上
5. **Drive 匯出。** `files.export` 的官方說明：「Exports a Google Workspace document to the requested MIME type and returns exported byte content. ... Note that the exported content is limited to 10 MB.」【直讀】[drive.v3.json](https://github.com/googleapis/google-api-python-client/blob/main/googleapiclient/discovery_cache/documents/drive.v3.json)（修訂版 20261106）
6. **Drive 縮圖連結。** 檔案的 `thumbnailLink` 是短效連結（說明寫通常以小時計），因為跨來源政策不適合直接用在網頁應用程式，檔案沒有公開分享時要用帶憑證的請求去抓。【直讀】同上
7. **Drive API 配額。** 搜尋到兩種寫法：一種是每 60 秒 12,000 次請求；另一種是以配額單位計，每專案每分鐘 1,000,000 單位、每使用者每分鐘 325,000 單位，`files.get` 算 5 單位、`files.list` 算 100 單位。哪一種是現行的沒有確認。【搜尋摘要】[Usage limits](https://developers.google.com/drive/api/guides/limits)
8. **把 PPTX 匯入成 Google Slides 的做法。** 描述檔裡 `About.importFormats` 是「來源 MIME 類型到可轉換目標的對照表」，`About.maxImportSizes` 是「各 MIME 類型的匯入大小上限」，表示匯入轉換是 Drive API 的正式功能，上限要在執行時向 API 查。【直讀】同上。具體的呼叫方式（上傳時指定目標 MIME 類型為 `application/vnd.google-apps.presentation`）與 PPTX 的匯入大小上限，我沒有讀到官方文件原文；社群有人回報指定了目標類型卻沒有轉換。【第三方】
9. **Google Slides 匯入 PPTX 的已知失真。** 沒有找到 Google 官方的清單。第三方整理的共同說法：不在 Google 字型庫裡的字型會被替換（常換成 Arial），即使字型內嵌在 PPTX 裡也一樣，文字因此可能溢出；Morph 轉場、動作路徑、複雜觸發條件會被移除或簡化；部分圖形效果會掉；SmartArt 會變成一般圖形；巨集不支援；匯入時 Google Slides 會提示有不支援的功能。【第三方】[BrightCarbon](https://www.brightcarbon.com/blog/convert-powerpoint-google-slides/)、[Pixel Presentation](https://pixelpresentation.com/blog/powerpoint-to-google-slides)、[Microsoft 問答](https://learn.microsoft.com/en-us/answers/questions/5848796/ppt-to-a-google-slide-presentation)
10. 上一條對本產品的含意：字型是 Google Slides 這條輸出的最大風險。要讓匯入後版面不跑掉，PPTX 裡用的字型最好是 Google 字型庫裡有的。（這是推論，要實測。）

### 3.5 Office 增益集（Office.js）

1. **PowerPoint 有把投影片畫成影像的介面，而且已經是正式版。** `PowerPoint.Slide.getImageAsBase64(options?)`：「Renders an image of the slide. The image is scaled to fit into the desired dimensions. If width and height aren't specified, the true size of the slide is used.」回傳 Base64 編碼的 PNG。文件標示 API set 是 PowerPointApi 1.8，`isPreview: false`。`SlideGetImageOptions` 可以指定 `width` 與 `height`（像素），只給一邊時另一邊按比例計算。【直讀】[powerpoint.slide.yml](https://github.com/OfficeDev/office-js-docs-reference/blob/main/docs/docs-ref-autogen/powerpoint/powerpoint/powerpoint.slide.yml)、[PowerPoint 1.8 介面表](https://github.com/OfficeDev/office-js-docs-reference/blob/main/docs/includes/powerpoint-1-8.md)
2. 網路上還查得到「這個介面只在預覽版」的說法（2025 年的論壇貼文與舊的文件鏡像）。那是過時的資訊，以上一條直讀到的現行文件為準。
3. **PowerPointApi 1.8 的平台支援。** 網頁版：支援。Windows 的 Microsoft 365 訂閱版與零售永久版：2504 版（組建 18730.20030）起。Mac：16.96 起。Windows 的大量授權永久版與 LTSC：不提供。iPad：不提供。【直讀】[需求集平台表](https://github.com/OfficeDev/office-js-docs-reference/blob/main/docs/requirement-sets/powerpoint/powerpoint-api-requirement-sets.md)
4. PowerPointApi 1.10 另外加了 `Shape.getImageAsBase64`（把單一圖形畫成影像），Windows 2601 版、Mac 16.105 起，網頁版支援。【直讀】[PowerPoint 1.10 介面表](https://github.com/OfficeDev/office-js-docs-reference/blob/main/docs/includes/powerpoint-1-10.md)
5. **取整份文件的 PDF。** `Office.context.document.getFileAsync(Office.FileType.Pdf, ...)` 把整份文件以 PDF 位元組回傳，分片取回，每片最大 4 MB（iPad 是 64 KB）。各平台支援的檔案類型如下。【直讀】[office.document.yml](https://github.com/OfficeDev/office-js-docs-reference/blob/main/docs/docs-ref-autogen/office/office/office.document.yml)、[office.filetype.yml](https://github.com/OfficeDev/office-js-docs-reference/blob/main/docs/docs-ref-autogen/office/office/office.filetype.yml)

   | 應用程式 | 網頁版 | Windows | Mac | iPad |
   |---|---|---|---|---|
   | Excel | Pdf | Compressed、Pdf、Text | Compressed、Pdf、Text | 不支援 |
   | PowerPoint | Compressed | Compressed、Pdf | Compressed、Pdf | Compressed、Pdf |
   | Word | Compressed | Compressed、Pdf、Text | Compressed、Pdf、Text | Compressed、Pdf |

6. **Word 沒有把頁面畫成影像的介面。** 我讀了 `Word.Page` 類別的成員清單：`breaks`、`height`、`index`、`width`、`getNext`、`getRange` 等，沒有任何輸出影像的方法。`Word.Document.exportAsFixedFormat`（存成 PDF 或 XPS）標示為「WordApi BETA (PREVIEW ONLY)」。所以 Word 在正式版介面裡能拿到的渲染結果只有上一條的 `getFileAsync(Pdf)`，而且網頁版 Word 不支援。【直讀】[word.page.yml](https://github.com/OfficeDev/office-js-docs-reference/blob/main/docs/docs-ref-autogen/word/word/word.page.yml)、[word.document.yml](https://github.com/OfficeDev/office-js-docs-reference/blob/main/docs/docs-ref-autogen/word/word/word.document.yml)、[Word 預覽介面表](https://github.com/OfficeDev/office-js-docs-reference/blob/main/docs/includes/word-preview.md)
7. **預覽介面不能用在正式環境。** 官方說明：「Preview APIs are subject to change and are not intended for use in a production environment.」要用還得載入 beta 版的 Office.js，可能要加入 Microsoft 365 Insider。【直讀】[using-preview-apis-host.md](https://github.com/OfficeDev/office-js-docs-reference/blob/main/docs/includes/using-preview-apis-host.md)
8. **Excel。** `Excel.Range.getImage()`：「Renders the range as a Base64-encoded PNG image.」API set 是 ExcelApi 1.7。【直讀】[excel.range.yml](https://github.com/OfficeDev/office-js-docs-reference/blob/main/docs/docs-ref-autogen/excel/excel/excel.range.yml)
9. **執行環境。** 網頁版的增益集跑在帶 HTML5 sandbox 屬性的 iframe 裡；Windows 與 Mac 上跑在和 Office 主程式分開的網頁檢視行程裡。增益集可以讀寫目前開啟的文件，網路存取受限。【直讀】[privacy-and-security.md](https://github.com/OfficeDev/office-js-docs-pr/blob/main/docs/concepts/privacy-and-security.md)
10. **資源限制**（Windows 與 Mac 的 Excel、PowerPoint、Word）。單一 CPU 核心使用率 90%，以 5 秒為間隔連續觀察到三次，Office 會問使用者要不要繼續執行；記憶體門檻依裝置實體記憶體動態決定；一個文件工作階段內當機四次；持續無回應 5 秒。網頁版 Excel 的請求與回應酬載上限是 5 MB。文件沒有列 Word 與 PowerPoint 的酬載上限。【直讀】[resource-limits-and-performance-optimization.md](https://github.com/OfficeDev/office-js-docs-pr/blob/main/docs/concepts/resource-limits-and-performance-optimization.md)
11. 綜合以上：增益集只在使用者開著文件的 Office 工作階段裡執行，是使用者端的工具，不能當伺服器端的渲染器。它的價值在於：這是唯一一條不碰授權問題、又能拿到真正 PowerPoint 渲染結果的路。（結論是推論；各項事實是直讀的。）

### 3.6 Adobe Illustrator 與 InDesign 的自動化途徑

1. **InDesign 與 InDesign Server 都支援 UXP。** 官方文件原文：「InDesign, along with its counterpart InDesign Server, supports UXP.」UXP 是以 JavaScript ES6 為基礎的擴充平台。【直讀】[UXP 簡介](https://github.com/AdobeDocs/uxp-indesign/blob/main/src/pages/introduction/index.md)
2. **UXP 腳本與外掛的差別。** 腳本是單一 JavaScript 檔，副檔名 `.idjs`，自 InDesign v18.0 起可在 InDesign 與 InDesign Server 執行，只能用對話框和使用者互動，不能上 Adobe Marketplace。外掛由 JavaScript、HTML、CSS 加一份 manifest 組成，打包成 `.ccx`，自 v18.5 起只支援桌面版 InDesign，可以私下散布、經自有通路或 Adobe Marketplace 散布，有命令式與面板式兩種。【直讀】[腳本與外掛比較](https://github.com/AdobeDocs/uxp-indesign/blob/main/src/pages/introduction/next-steps/script-and-plugin/index.md)
3. **InDesign Server 是什麼。** Adobe 的 InDesign API 文件這樣描述：「The InDesign Server is a headless application that lets users run InDesign scripts on their machines.」【直讀】[InDesign API FAQ](https://github.com/AdobeDocs/indesign-api-docs/blob/main/FAQs.md)
4. **InDesign Server 的授權類型。** 四種：Premium（可供公司外部的人經網際網路使用）、Limited（只限授權方員工在內部網路使用）、Development（只供開發、測試、預備環境，不得用於生產）、Trial。按伺服器計，不限使用者數，每個虛擬機各要一份授權。【搜尋摘要】[購買指南](https://www.adobe.com/products/indesignserver/buying-guide.html)、[2021 授權更新](https://blog.developer.adobe.com/en/publish/2021/06/licensing-updates-for-developers-using-indesign-server)、[2020 年產品特定條款](https://www.adobe.com/content/dam/cc/en/legal/terms/enterprise/pdfs/PSLT-InDesignServer-WW-2020v1.pdf)
5. **InDesign Server 的價格，查到的數字互相對不上。** Adobe 各地區的購買指南頁面上出現過每年 2,100、5,000、13,500 美元三個數字，但搜尋摘要無法確定各自對應哪一級授權：一次摘要把 5,000 對到 Limited、13,500 對到 Premium；另一次把 5,000 對到 Development，2,100 則出現在 Trial 字樣旁邊。Adobe 社群人員的說法是沒有公開的統一價目，按用量與規模報價。【搜尋摘要】同上、[社群討論](https://community.adobe.com/questions-624/rendition-api-license-query-1645050)
6. **Premium 能不能用在代管或 SaaS，說法矛盾。** Adobe 購買指南（搜尋摘要）說 Premium 不能部署在代管或 SaaS 產品裡；經銷商 Datalogics 說只要 InDesign Server 是解決方案裡整合的一個元件、不是單獨提供的服務，就可以。要用這條路之前必須向 Adobe 或經銷商取得書面確認。【搜尋摘要】[購買指南](https://www.adobe.com/products/indesignserver/buying-guide.html)、[Datalogics](https://datalogics.com/adobe-indesign-server)
7. **InDesign API（Firefly Services）。** Adobe 文件：InDesign API 把 InDesign Server 的多種能力做成服務端點。`create-rendition` 端點的範例輸出類型是 `image/png`、`image/jpeg` 與 PDF；驗證是伺服器對伺服器的 OAuth，權杖 24 小時有效。速率：軟上限每分鐘 250 次（超過會變慢），硬上限每分鐘 350 次。支援的儲存類型是 AWS S3、Dropbox、Azure 的預簽網址。【直讀】[README](https://github.com/AdobeDocs/indesign-api-docs/blob/main/README.md)、[FAQ](https://github.com/AdobeDocs/indesign-api-docs/blob/main/FAQs.md)。這個 GitHub 程式庫裡還留著測試期的遷移說明，內容可能比現行官網文件舊：它寫單次請求最多 99 個資產，搜尋摘要轉述的現行官網文件寫 1,000 個，檔案上限已從 1 GB 提高到 2 GB。
8. InDesign API 的現行文件另有「把 INDD 轉成 IDML」的端點（2026-11-06 的更新紀錄）。Rendition 端點是否接受 IDML 當輸入，沒有查到。【搜尋摘要】[InDesign API 文件](https://developer.adobe.com/firefly-services/docs/indesign-apis/)
9. **InDesign API 的計價。** 以「Operations」計。Rendition 的費率，一份 Adobe 文件寫每輸出一頁 1 個 Operation，另一份費率表寫每輸出一頁 2.5 個 Operation。一個 Operation 多少錢沒有查到；社群貼文說要找 Adobe 業務報價。有開發者回報個人帳號的開發者主控台裡找不到 Firefly Services。【搜尋摘要】[Operation 定義](https://helpx.adobe.com/business/enterprise/products-entitlements/manage-entitlements/automation-operation-definition.html)、[費率表](https://helpx.adobe.com/business/enterprise/products-entitlements/manage-entitlements/fireflyservices-ratecard-ver-a.html)
10. **Illustrator 的腳本語言。** JavaScript（副檔名 `.jsx`，即 ExtendScript）在 Windows 與 macOS 都能用；AppleScript 限 macOS；VBScript 限 Windows。【第三方】[社群維護的腳本指南](https://ai-scripting.docsforadobe.dev/introduction/scriptingLanguageSupport/)。沒有讀到 Adobe 官方頁面。
11. **Illustrator 的 UXP。** 2025 年的社群貼文說正式版 Illustrator 還沒有 UXP；一家外掛廠商說它的 UXP 存取是在保密協議下的搶先版。2026 年是否已正式發布，沒有查到 Adobe 的公告。【第三方】[社群討論](https://community.adobe.com/questions-652/clarification-needed-is-uxp-publicly-available-for-illustrator-in-2026-1548811)、[Mapsoft](https://mapsoft.com/posts/illustrator-uxp-status.html)
12. **Illustrator 沒有伺服器版。** 我沒有查到任何「Illustrator Server」產品。Firefly Services 底下有 Illustrator API，搜尋摘要說它的 Rendition 服務把 `.ai` 轉成其他格式，另有影像描圖與資料合併。它是否接受 PDF 當輸入、輸出哪些格式、怎麼計價，都沒有查到。【搜尋摘要】[Illustrator API](https://developer.adobe.com/firefly-services/docs/illustrator)
13. **把桌面版 Adobe 軟體裝在伺服器上自動化，條款上有疑慮。** 查到的都是舊版或企業版條款：舊版 Software Terms 禁止以代工服務（service bureau）的方式使用或提供軟體；企業版 General Terms 禁止把產品用在電腦服務業務、第三方外包、代管服務，或代任何第三方使用；2015 年的 Acrobat EULA 禁止「不是由個人啟動的操作，例如自動化伺服器處理」。現行消費者版 General Terms（2025-10-03 生效）的對應條文我沒有取得。【搜尋摘要】[2019 Software Additional Terms](https://wwwimages2.adobe.com/content/dam/acom/en/legal/servicetou/Software_Terms-en_US_20190711_2200.pdf)、[現行 General Terms](https://www.adobe.com/go/terms_linkfree)
14. Illustrator 開啟 PDF 之後的行為（缺字型、文字被拆散）見 [pdf.md](pdf.md) 的 3.12。PDFium 畫出來的影像只能代表「PDF 本身長這樣」，不能代表「Illustrator 開啟後長這樣」。

### 3.7 商用轉檔渲染函式庫

1. **Aspose 的現行版本與授權性質。** NuGet 上 `Aspose.Slides.NET`、`Aspose.Words`、`Aspose.Cells` 的最新版都是 26.11.0，`Aspose.Total` 是 26.10.0。PyPI 上 `aspose-slides` 26.11.0（2026-11-14）、`aspose-words` 26.11.0（2026-11-17）。套件要求接受授權，授權檔指向 Aspose 的 EULA；PyPI 的分類是「Other/Proprietary License」與「Free To Use But Restricted」。【直讀】[NuGet：Aspose.Slides.NET](https://www.nuget.org/packages/Aspose.Slides.NET)、[PyPI：aspose-slides](https://pypi.org/project/aspose-slides/)、[PyPI：aspose-words](https://pypi.org/project/aspose-words/)
2. NuGet 的套件說明：不必安裝 PowerPoint 或任何第三方元件，就能建立、編輯、渲染、列印、轉換簡報，可轉成 PDF、XPS、TIFF、HTML 等。【直讀】同上。這是廠商自述；它用的是自己的排版引擎，和 PowerPoint 的差距我沒有查到獨立資料。
3. **Aspose 的授權類型。** 四種授權類型：Developer Small Business、Developer OEM、Site Small Business、Site OEM，另有按用量計的 Metered。官方授權類型頁（搜尋摘要）：Developer Small Business 涵蓋一位開發者與一個實體部署地點。經銷商頁面轉述：Small Business 級不涵蓋把軟體散布給第三方、對外網站或應用程式、SaaS；OEM 級才涵蓋。官方政策頁（搜尋摘要）：產品如果對開發者提供公開 API，連 OEM 也不夠，要改用 Metered 或 SDK 授權。【搜尋摘要】[授權類型](https://purchase.aspose.com/policies/license-types)、[計量授權問答](https://purchase.aspose.com/faqs/licensing/metered)、[公開 SDK 或 API 的使用](https://purchase.aspose.com/policies/use-within-public-sdk-or-api)；【第三方】[ComponentSource](https://www.componentsource.com/product/aspose-slides-net/prices)
4. **Aspose 的價位級距（美元）。** 官方價格頁我打不開，下列數字的來源標在後面，各來源之間有出入。

   | 產品 | 授權類型 | 價格 | 來源 |
   |---|---|---|---|
   | Aspose.Slides for .NET | Developer Small Business | 999 | 官方價格頁（搜尋摘要）；另一家經銷商 XLsoft 列 799 |
   | Aspose.Slides for .NET | Developer OEM | 2,997 | ComponentSource 列的建議售價 |
   | Aspose.Slides for .NET | Site Small Business | 4,995 | ComponentSource；XLsoft 列 3,995 |
   | Aspose.Slides for .NET | Site OEM | 13,986 | ComponentSource |
   | Aspose.Words for .NET | Developer Small Business | 1,199 | XLsoft |
   | Aspose.Words for .NET | Developer OEM | 3,597 | XLsoft |
   | Aspose.Total for .NET | Developer Small Business | 3,999 | ComponentSource、XLsoft |
   | Aspose.Total for .NET | Site OEM | 55,986 | XLsoft |

   【搜尋摘要】[官方價格頁](https://purchase.aspose.com/pricing/slides/net/)；【第三方】[ComponentSource：Slides](https://www.componentsource.com/product/aspose-slides-net/prices)、[ComponentSource：Total](https://www.componentsource.com/product/aspose-total-net/prices)、[XLsoft](https://shop.xlsoft.com/en/aspose-pricing/)
5. Aspose 的計量授權沒有查到每次呼叫的單價。官方問答（搜尋摘要）說定價涵蓋 20 MB 以內的檔案，之後每 20 MB 多算一次呼叫。【搜尋摘要】[計量授權問答](https://purchase.aspose.com/faqs/licensing/metered)
6. 對本產品的含意：Mortise 是對外的 SaaS，未來又可能散布桌面版，依上面的授權類型至少要 OEM 級；同時要渲染 PPTX、DOCX、XLSX 三種格式，就是三個產品或一套 Total。（這是對搜尋摘要的解讀，購買前要向 Aspose 確認。）
7. **Syncfusion。** 社群授權（免費）的資格：年營收低於 100 萬美元、開發者不超過 5 人、員工不超過 10 人；由稅金支應的政府機關不符資格。付費的 Team 授權每月 395 美元，最多 5 位開發者，可無限部署。文件處理函式庫的單獨價格與轉檔保真度沒有查到。【搜尋摘要】[社群授權](https://www.syncfusion.com/products/communitylicense)

### 3.8 ONLYOFFICE、Collabora 與其他替代引擎

1. **ONLYOFFICE 的三個相關程式庫都是 AGPL-3.0。** `ONLYOFFICE/DocumentServer`、`ONLYOFFICE/core`（轉檔核心）、`ONLYOFFICE/DocumentBuilder` 的授權檔開頭都是「GNU AFFERO GENERAL PUBLIC LICENSE Version 3」。Docker Hub 上 `onlyoffice/documentserver` 最新標籤是 10.0.0（2026-10-12）。【直讀】[DocumentServer](https://github.com/ONLYOFFICE/DocumentServer/blob/master/LICENSE)、[core](https://github.com/ONLYOFFICE/core/blob/master/LICENSE)、[DocumentBuilder](https://github.com/ONLYOFFICE/DocumentBuilder/blob/master/LICENSE)、[Docker 標籤](https://hub.docker.com/r/onlyoffice/documentserver/tags)。依本產品的授權底線，社群版排除。
2. **ONLYOFFICE Docs Developer Edition 是商業授權。** 官方問答（搜尋摘要）：三種授權類型 Development Server、Single Server、Cluster；Development Server 不得用來向自己的終端使用者提供整合在自家品牌下的編輯器；Single Server 與 Cluster 是年訂閱，到期不續會被鎖住；各類型都含白牌選項與 Document Builder。【搜尋摘要】[官方問答](https://helpcenter.onlyoffice.com/docs/faq/developer.aspx)
3. Developer Edition 的價格：經銷商 ComponentSource 列 Single Server、250 個同時連線、一年 8,918 美元；Development 授權（20 個連線，不得用於生產）1,911 美元。官方價目頁沒有讀到。【第三方】[ComponentSource](https://www.componentsource.com/product/onlyoffice-docs-developer/prices)
4. **Collabora Online 的原始碼以 MPL-2.0 為主。** `COPYING` 檔全文是 MPL-2.0 的聲明。鏡像程式庫的 README：「Open Source – primarily under the MPLv2 license. Some parts are under other open source licences」，品牌素材是 Apache-2.0。開發已移到 Collabora 自己的 Gerrit，GitHub 只留議題追蹤與發布產物。【直讀】[COPYING](https://github.com/CollaboraOnline/online/blob/master/COPYING)、[鏡像 README](https://github.com/CollaboraOnline/online.mirror/blob/main/README.md)
5. **Collabora 的轉檔端點。** `POST /cool/convert-to/<格式>`，或把格式放在表單欄位，可以帶 PDF 版本等選項。論壇有人回報轉 png 只輸出第一頁、第一張投影片或第一張工作表。【搜尋摘要】[Conversion API](https://sdk.collaboraonline.com/docs/conversion_api.html)、[論壇回報](https://forum.collaboraonline.com/t/request-api-convert-to-png/4063)
6. **Collabora 官方發布的 CODE 版本，不建議用於生產。** 官方問答（搜尋摘要）：CODE 是持續更新的滾動發行，沒有服務等級協議也沒有長期支援，不建議用在商業或生產環境。論壇有人指出 Collabora 的授權說明寫到「Collabora 發布的二進位檔附帶額外限制，以專有授權發布」，但沒有說清楚哪些限制適用於 CODE。這些限制的原文我沒有讀到。【搜尋摘要】[官方問答](https://www.collaboraonline.com/faqs/)、[論壇討論](https://forum.collaboraonline.com/t/code-binary-license/179)
7. Collabora Online 的渲染核心是 LibreOffice 的程式碼。就「驗證用渲染器」這個用途而言，它相對於直接用 LibreOffice 沒有保真度上的好處，只是多了一層 HTTP 服務與商業支援的選項。（這是推論。）
8. **Apache POI。** Apache-2.0 授權。原始碼裡有 `PPTX2PNG` 工具，用法說明列出輸出格式 png、gif、jpg、svg、pdf 與縮放倍率參數。【直讀】[LICENSE](https://github.com/apache/poi/blob/trunk/legal/LICENSE)、[PPTX2PNG.java](https://github.com/apache/poi/blob/trunk/poi-ooxml/src/main/java/org/apache/poi/xslf/util/PPTX2PNG.java)。它的渲染保真度沒有查到資料。
9. **Scribus。** 授權是 GPL 第 2 版或更新版。【直讀】[COPYING](https://github.com/scribusproject/scribus/blob/master/COPYING)。它是我知道唯一能匯入 IDML 的開源排版軟體（這一點出自訓練記憶，沒有查證），但 GPL 讓它不能用。

## 4. 建議

先講一個貫穿全部建議的前提：**沒有任何一個授權可用、又能放在伺服器上每次都跑的引擎，等於使用者手上的 PowerPoint、Word、Google Slides、Illustrator 或 InDesign。** 所以驗證要分兩層：第一層用自己架設的替身引擎，每次轉檔都跑；第二層用真引擎，抽樣跑或在使用者端跑，用來校正第一層。還原度報告要寫清楚分數是用哪一個引擎量的。

### 首選：LibreOffice 26.8 無介面模式轉 PDF，PDFium 轉影像

用在 PPTX、DOCX、XLSX 三種輸出，伺服器上每次轉檔都跑。

理由：

- 授權乾淨。MPL-2.0 可商用，可隨桌面版散布，不是 GPL 或 AGPL。外殼用 unoserver（MIT）或直接走 LibreOfficeKit（MPL-2.0）。
- 檔案不離開自己的機器，沒有第三方資料處理條款的問題，也沒有配額與節流。
- 和 pdf.md 選定的 PDFium 接得起來：LibreOffice 只負責轉成 PDF，轉影像交給 PDFium，避開「直接轉 png 只出第一頁」的問題。
- 這是本次查到唯一一個同時滿足「可商用、可散布、自己架設、三種 Office 格式都吃」的引擎。

要接受的代價與要先做的事：

- **它量出來的是「在 LibreOffice 裡的樣子」。** 斷行、文字自動縮放、字型替代都和 Office 有已知出入（3.1 的第 8 到 14 條）。還原度報告不能把這個分數說成「在 PowerPoint 裡的還原度」。
- **字型要先處理。** 輸出檔用到的每一種字型，伺服器上都要有同一套字型檔，否則 LibreOffice 會替換字型，驗證就失去意義。最穩的做法是讓 Mortise 的輸出只使用一份「伺服器裝得到、授權允許、Office 與 Google Slides 也都有或可內嵌」的字型清單。這份清單要另外查證，本次沒有做。
- **盡量不要讓輸出檔依賴「由開檔程式自己算」的版面。** 例如文字自動縮放。寫死字級與文字方塊尺寸，各引擎的結果才會接近。（這是從 3.1 第 14 條推出來的設計建議，要實測。）
- **鎖定 LibreOffice 版本。** 同一份檔案在不同版本的斷行可能不同（3.1 第 13 條）。升級前跑回歸測試。
- **先做一次對照實測。** 拿一批代表性的 PPTX、DOCX，分別用 LibreOffice 與真正的 PowerPoint、Word 渲染，量出差異的分布。沒有這個數字，就不知道第一層的分數可以信到什麼程度。
- 桌面版若要內含 LibreOffice，安裝檔會大很多，而且要盤點 LibreOffice 安裝包裡第三方元件的授權。這一點本次沒有做。

### 第二層：用真引擎校正

依取得難度排序。

1. **PowerPoint：Office 增益集的 `Slide.getImageAsBase64`。** 正式版介面，網頁版、Windows、Mac 的 Microsoft 365 版本都支援，回傳真正 PowerPoint 畫的 PNG。它在使用者自己的 PowerPoint 裡執行，不碰任何授權問題。可以做成「在你的 PowerPoint 裡檢查還原度」的功能，也可以由團隊自己在測試機上手動跑對照測試集。限制是不能無人值守，大量授權永久版與 LTSC 不支援。
2. **Google Slides：匯入後呼叫 Slides API 的 `getThumbnail`。** 這是唯一能看到 Google Slides 實際渲染的途徑，LibreOffice 與 PowerPoint 都代替不了。建議在使用者選擇「匯出到 Google Slides」並授權他的 Google 帳號之後，對匯入完成的簡報逐頁取縮圖比對。每位使用者每分鐘 60 次的配額，60 張以內的簡報一分鐘內取得完，更長的要分批。超額是否收費有兩種說法，上線前要確認。
3. **Word 與 Excel：增益集的 `getFileAsync(Pdf)`。** Word 只限桌面版與 iPad；Excel 連網頁版都支援。用途同第 1 點。
4. **Microsoft Graph 的 `format=pdf`。** 技術上是伺服器端唯一拿得到 Microsoft 引擎輸出的途徑，不另計費。但有三件事沒有解決：拿自家租戶替外部客戶轉檔合不合條款，沒有查到明文；官方文件寫的上限是 2 MB；轉出來的 PDF 是不是和桌面版 Office 一樣的排版，沒有查到。**在法務確認之前，只建議用在內部的對照測試（用自己的授權、自己的測試檔），不要放進對外服務的轉檔流程。** 若要對外用，走「使用者以自己的 Microsoft 帳號授權」的方式條款上最單純。

### Illustrator 與 InDesign

- **給 Illustrator 的 PDF：用 PDFium 驗證 PDF 本身**（見 pdf.md）。Illustrator 開啟之後的樣子，伺服器端沒有查到可用的驗證途徑。還原度報告要標示「未經 Illustrator 驗證」。要驗證只能靠團隊自己在裝有 Illustrator 的機器上跑腳本做對照測試，或之後查清楚 Firefly 的 Illustrator API 是否吃 PDF。
- **給 InDesign 的 IDML：沒有授權可用的自架渲染器。** 在使用者最終確認要不要做 IDML 之前，建議第一版只做結構檢查（IDML 是否合乎規格、物件數與文字內容是否對得上），不做像素比對，並在還原度報告標示「未經 InDesign 渲染驗證」。若確定要做像素驗證，三條路依序評估：
  1. 使用者端的 UXP 外掛或腳本，在使用者自己的 InDesign 裡匯出影像回傳。不需要額外授權費，但不能無人值守。
  2. InDesign API（Firefly Services）。先向 Adobe 確認兩件事：Rendition 是否接受 IDML；一個 Operation 多少錢。
  3. InDesign Server Premium。先向 Adobe 取得書面確認：這個授權能不能用在 SaaS。

### 備選（需付費）：Aspose

如果對照實測顯示 LibreOffice 的差距大到不能用，而真引擎又不能放進正式流程，再評估 Aspose.Slides、Aspose.Words、Aspose.Cells。

- 好處：自己架設，不依賴 Office，授權買了就能用在 SaaS 與散布（OEM 級）。
- 代價：它同樣不是 Office 的引擎，保真度是否比 LibreOffice 好沒有任何資料，一定要先用試用版跑同一份對照測試集；三種格式一起買，依查到的經銷商數字是數萬美元的級距，而且價格與授權類型都要向 Aspose 確認。
- 如果公司規模符合 Syncfusion 社群授權的資格，可以把它一起放進對照實測，成本是零。

### 不可使用

- **在伺服器上自動化執行 Microsoft Office。** 產品條款禁止用產品向第三方提供商業代管服務；Microsoft 不支援；Unattended License 的條款還禁止把輸出用來改進或複製產品功能。
- **ONLYOFFICE 社群版**（AGPL-3.0）。要用只能買 Developer Edition。
- **Scribus**（GPL）。
- **Collabora 官方發布的 CODE 二進位檔**當生產環境的轉檔服務。官方不建議，授權條件也沒有查清楚。真要用 Collabora，要嘛買訂閱，要嘛自己從 MPL-2.0 的原始碼建置；但它的渲染核心就是 LibreOffice，直接用 LibreOffice 即可。
- **把桌面版 InDesign 或 Illustrator 裝在伺服器上跑腳本。** 查到的舊版與企業版條款都禁止代工服務與代管用途，現行條款沒有讀到，在讀到之前當作不可行。

## 5. 查不到的事

### 沒有實測，所以不知道

1. LibreOffice 26.8 渲染 PPTX、DOCX、XLSX 的結果，和 PowerPoint、Word、Excel 的像素差異有多大、集中在哪些版面元素。本次沒有任何量化數字。
2. 中日韓文字（特別是繁體中文）在 LibreOffice 與 Office 之間的斷行、標點擠壓、字型替代差異。
3. `--convert-to png` 只輸出第一頁的行為在 26.8 是否仍然如此。
4. `Slide.getImageAsBase64` 在各平台實際回傳的影像尺寸上限與速度；在網頁版 PowerPoint 是否真的可用（平台表寫支援，沒有實測）。
5. Microsoft Graph 轉出的 PDF 和桌面版 PowerPoint、Word 另存的 PDF 是否一致。
6. Aspose、Syncfusion、Apache POI 的渲染保真度。

### 文件或條款沒有讀到原文

7. KB 257757「Considerations for server-side Automation of Office」與 Microsoft Learn 的「無人值守 RPA 注意事項」兩篇的原文。本文的引述來自搜尋摘要。
8. Microsoft APIs 使用條款的現行全文，包括對快取與保存期限的具體要求。
9. 拿自家的 Microsoft 365 租戶，經 Graph 替外部客戶轉檔，是否違反多工條款或其他條款。沒有找到 Microsoft 的明文說明。
10. Graph 格式轉換一次算幾個資源單位；`format=jpg` 對多頁文件回傳哪一頁；2 MB 上限為什麼只出現在 v1.0 文件。
11. Google Slides API 用量上限頁的原文。配額數字來自搜尋摘要；超額收費的生效日期與費率沒有查到。
12. Google Drive API 現行的配額是「每 60 秒 12,000 次」還是「配額單位制」。
13. Google 官方對「PPTX 匯入 Google Slides 會失真哪些功能」的清單。只找到第三方整理。
14. 用 Drive API 把 PPTX 匯入成 Google Slides 的官方呼叫方式與大小上限的原文。
15. 用服務帳號替使用者匯入並取縮圖，是否合乎 Google API 條款。本次沒有查。
16. Adobe 現行 General Terms 與 Creative Cloud 產品特定條款中，關於伺服器使用、自動化、代工服務的條文原文。
17. InDesign Server 各級授權的現行價格，以及 Premium 能否用於 SaaS。兩個來源互相矛盾。
18. InDesign API 的 Rendition 是否接受 IDML 輸入；一個 Operation 的單價；現行的每頁費率是 1 還是 2.5 個 Operation。
19. Illustrator 的 UXP 在 2026 年是否已正式發布；Adobe 官方的 Illustrator 腳本語言支援頁；Illustrator API 是否接受 PDF 輸入、輸出格式與定價。
20. 用 UXP 或 ExtendScript 在 InDesign 裡「開啟 IDML、逐頁匯出 PNG」的具體介面。本次只確認了 UXP 的支援範圍。
21. Aspose 的官方價目、EULA 全文、計量授權的單價。本文的價格多數來自經銷商，彼此有出入。
22. ONLYOFFICE Docs Developer Edition 的官方價目，以及它能否隨桌面版散布。
23. Collabora 官方二進位檔（含 CODE）附帶的額外限制原文。
24. LibreOffice 官方授權頁的原文，以及安裝包內第三方元件的完整授權清單。隨桌面版散布之前要盤點。
25. Aptos 與微軟中文字型（微軟正黑體、新細明體、標楷體）的授權條款，能不能裝在 Linux 伺服器上。

### 沒有調查的候選

26. 其他商用函式庫：Apryse、GroupDocs、Spire.Office、GemBox、Telerik。
27. 第三方雲端轉檔服務：CloudConvert、ConvertAPI、Adobe PDF Services 的「由 Office 檔建立 PDF」。
28. 桌面版在使用者自己的電腦上，由使用者按下按鈕後呼叫他本機已安裝的 Office 另存 PDF（Windows 的 COM、macOS 的 AppleScript）。依產品條款對「有人在場的機器人」的定義，這種由使用者明確觸發、在他自己工作站上執行的自動化看起來不需要 Unattended License，但這是我對定義的解讀，沒有查到 Microsoft 的明文說明，技術細節也沒有查。

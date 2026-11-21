# 查證紀錄：字型辨識與字型授權

- 查證日期：2026-11-20
- 適用產品：Mortise（榫）文件反編譯 SaaS，未來可能有桌面版
- 授權底線：所有元件必須可商用；排除 GPL、AGPL 元件（另有可購買商業授權者列為「需付費」）；排除非商用授權的模型權重
- 需求：(1) 從影像判斷原文件用了什麼字型，或至少判斷字型類別；(2) 找出可以合法裝在伺服器上渲染、也能隨桌面版散布的繁體中文與英文字型；(3) 弄清楚 Microsoft 字型與台灣商用字型能不能裝在 Linux 伺服器上；(4) 評估從 PDF 內嵌子集字型取字形的風險；(5) 取得字型量度資料的方法；(6) Office 預設字型的變化

## 0. 查證方式與限制

### 0.1 這份紀錄沒有做的事

- **沒有執行任何字型辨識模型。** 下文所有辨識準確率都是各專案自己寫的數字，沒有在 Mortise 的資料上測過。
- **沒有任何 Microsoft 字型檔可供量測。** 「Carlito 與 Calibri 字寬相同」這類說法全部來自專案自述與 fontconfig 的對照表，我沒有拿真的 Calibri 比對過。實測只做到「兩套都自稱相容 Arial 的開放字型彼此比對」（見 3.10）。
- **這不是法律意見。** 授權條款的解讀、PDF 內嵌字型的使用界線，上線前要請法務確認。

### 0.2 來源能不能讀到

| 來源 | 狀況 |
|---|---|
| 網路搜尋 | 可用，但本輪額度在查證後段用完（見 0.4） |
| raw.githubusercontent.com、github.com | 可直接讀取。各字型的授權檔與 README、OFL 常見問答、fontconfig 對照表（GitHub 鏡像）、FreeType 標頭檔、Azure SDK 範例、Office 佈景主題檔，都是從這裡讀的 |
| GitHub 搜尋介面（程式庫搜尋、程式碼搜尋） | 可用。個別程式庫的 API 端點被擋，改讀原始檔 |
| www.microsoft.com | 可直接讀取（Windows 11 授權條款 PDF、Product Terms、Aptos 下載頁） |
| corefonts.sourceforge.net | 可直接讀取（Core fonts for the Web 的 EULA） |
| pypi.org | 可直接讀取（fontTools、uharfbuzz 的版本與授權欄位） |
| learn.microsoft.com、support.microsoft.com | **無法讀取**。Microsoft 字型常見問答、各字型的說明頁只有搜尋摘要 |
| huggingface.co | **無法讀取**。所有放在 Hugging Face 的模型權重與資料集，授權標示都沒有直讀到 |
| cns11643.gov.tw、data.gov.tw、language.moe.gov.tw | **無法讀取**。全字庫與教育部字型的授權只有搜尋摘要 |
| dynacw.com.tw、arphic.com.tw、ifontcloud.com、justfont.com | **無法讀取**。華康、文鼎、justfont 的授權條款只有搜尋摘要 |
| monotype.com、helpx.adobe.com、fonts.google.com、developers.google.com | **無法讀取**。只有搜尋摘要 |
| whatfontis.com、mixfont.com | **無法讀取**。商用字型辨識 API 的文件只有搜尋摘要 |
| openfontlicense.org、scripts.sil.org | **無法讀取**。OFL 條文與常見問答改讀 GitHub 上各字型專案內附的副本 |
| gitlab.freedesktop.org、codeberg.org、arxiv.org、tipo.gov.tw | **無法讀取** |

### 0.3 查證方式的標註

- **【直讀】** 直接讀到官方來源的原文：授權檔、官方 README、官方條款頁、原始碼、套件登錄站的中繼資料
- **【實測】** 在這台機器上用 fontTools 4.68.0 實際讀字型檔得到的結果
- **【搜尋摘要】** 搜尋工具轉述了官方頁面的內容，但我沒有親自打開該頁
- **【第三方】** 只有非官方來源（新聞、部落格、論壇、別人的程式庫）
- **【推論】** 我根據上述事實做的推論，沒有來源直接這樣說

總表的「已查證」欄：授權與主要能力都是【直讀】才寫「是」；只有一部分是【直讀】寫「部分」；全部靠搜尋摘要或第三方寫「否」。

### 0.4 搜尋額度用完

本輪的網路搜尋額度在查證後段用完。以下幾題本來要再搜一次，沒有搜成，只能靠既有結果或留在「查不到的事」：Microsoft 字型常見問答的逐句原文、OpenType 規格對 `fsType` 的官方敘述（改用 FreeType 標頭檔的說明代替）、PDF 規格對字型描述項的官方敘述（改用 pdfminer.six 原始碼代替）、Arphic Public License 與文泉驛的授權。

## 1. 總表

「可否商用」指用在收費的線上服務。「可否隨桌面版散布」指把檔案打包進安裝檔交給使用者。字型不是機器學習模型，字型列的「權重授權」一律是不適用，「程式碼授權」欄填的是字型檔本身的授權。

### 1.1 字型辨識：模型、資料集、商用 API

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **YuzuMarker.FontDetection** | 中日韓字型辨識與樣式抽取 | MIT | 放在 Hugging Face，授權標示**沒有讀到** | 程式碼可。權重**待確認**：授權沒讀到，而且訓練用的字型包來源有疑慮 | 程式碼可；權重待確認 | README 自稱第一個中日韓字型辨識模型；附完整的合成資料產生與訓練程式；骨幹 ResNet-18/34/50 | 在作者自己的合成資料上最高準確率 48.99%（ResNet-50）；訓練字型來自「VCB-Studio 的 CJK 字型包」，README 沒說那些字型的授權；背景圖是從 pixiv 抓的，不公開；權重要用 PyTorch 2.0 加 `torch.compile` 才載得起來；最後一次推送 2025-02-22 | 部分（程式碼授權與 README 直讀；權重授權沒讀到） | [LICENSE](https://github.com/JeffersonQin/YuzuMarker.FontDetection/blob/master/LICENSE)、[README](https://github.com/JeffersonQin/YuzuMarker.FontDetection/blob/master/README.md) |
| **Lens（mixfont/lens）** | 從影像找最接近的開源字型 | README 自訂條款：只限個人、學術、非商業用途 | 同左（權重在程式庫內） | **不可**（自架）。商用要書面許可，或改用它的付費 API | **不可** | ResNet18；只用開源字型訓練（多數 Google Fonts 字族）；先用 Tesseract 找影像裡最大的字詞再分類 | README 自述：螢幕截圖或一張圖裡有很多種字型時表現不好；專有字型找不到好的對應；沒有提到中日韓 | 是（README 直讀） | [README](https://github.com/mixfont/lens/blob/main/README.md) |
| **Storia-AI/font-classify** | 把影像分類到約 3,000 種 Google Fonts | **沒有授權檔**（GitHub 授權欄位為空） | 放在 Hugging Face，授權標示沒有讀到 | **不可視為可商用**：沒有授權聲明就是保留所有權利 | 同左 | 附合成資料產生與訓練腳本（字型檔加背景圖加維基百科文句）；預設 ResNet50 | 只有拉丁字母的 Google Fonts；最後一次推送 2024-03-26 | 部分（README 直讀；確認找不到授權檔） | [README](https://github.com/Storia-AI/font-classify/blob/main/README.md) |
| **gaborcselle/font-identifier** | 字型分類的示範專案 | MIT | 放在 Hugging Face，授權標示沒有讀到 | 程式碼可 | 程式碼可 | ResNet18；作者自述測試集 2,400 張圖準確率 95% 以上 | 作者自述一天做完、字型種類很少、會把 Helvetica 與 Arial 搞混，要實用得用大得多的字型集重訓 | 部分 | [README](https://github.com/gaborcselle/font-identifier/blob/main/README.md)、[LICENSE](https://github.com/gaborcselle/font-identifier/blob/main/LICENSE) |
| **robinreni96/Font_Recognition-DeepFont** | DeepFont 論文的第三方重製（Keras） | MIT | 沒有提供可用的官方權重 | 程式碼可 | 程式碼可 | 重現論文的網路結構 | 不是 Adobe 的程式；用自製小資料集訓練；README 指向的 AdobeVFR 資料集授權不明 | 部分 | [README](https://github.com/robinreni96/Font_Recognition-DeepFont/blob/master/README.md)、[LICENSE](https://github.com/robinreni96/Font_Recognition-DeepFont/blob/master/LICENSE) |
| **Adobe DeepFont（官方）與 AdobeVFR 資料集** | Adobe 的字型辨識系統與其資料集 | **沒有找到官方開源** | 沒有找到 | 無從使用 | 無從使用 | 論文自述前五名準確率 80% 以上；AdobeVFR 含 2,383 個字型類別（第三方轉述） | 找不到官方程式庫或權重；AdobeVFR 的授權條款沒有查到 | 否 | [Adobe Research 論文頁](https://research.adobe.com/publication/deepfont-identify-your-font-from-an-image)、[論文](https://arxiv.org/pdf/1507.03196) |
| **Azure AI Document Intelligence 字型屬性附加功能**（`styleFont`） | 雲端 API：回傳文字的相似字族、粗細、斜體、顏色 | 專有雲端服務 | 不適用 | 可（付費） | 不適用（雲端服務） | 每個樣式物件帶 `similarFontFamily`（例如「Arial, sans-serif」）、`fontStyle`、`fontWeight`、`color`、`backgroundColor` 與對應的文字範圍 | 回傳的是「相似字族」，不是確切字型；對中文字型回傳什麼**沒有查到**；文件要送到 Azure；價格沒有查到 | 部分（Azure 官方 SDK 範例直讀；官方文件頁讀不到，只讀到鏡像） | [Azure SDK 範例](https://github.com/Azure/azure-sdk-for-net/blob/main/sdk/documentintelligence/Azure.AI.DocumentIntelligence/samples/Sample_AddOnCapabilities.md) |
| **WhatFontIs API** | 雲端 API：從影像找字型 | 專有雲端服務 | 不適用 | 可呼叫，但條件要再確認 | 不適用 | POST 影像，回傳 JSON | 用 API 就必須放連回 WhatFontIs 的連結；流量上限依方案；價格、中文字型支援都沒有查到 | 否（只有搜尋摘要） | [API 說明頁](https://whatfontis.com/API-identify-fonts-from-image.html) |
| **Mixfont Lens API** | 雲端 API：Lens 模型的付費版 | 專有雲端服務 | 不適用 | 可（付費） | 不適用 | 端點 `POST /v1/api/lens`；只會回傳開源字型 | API 價格沒有查到；沒有提到中日韓 | 否（只有搜尋摘要） | [API 文件](https://www.mixfont.com/docs/api-reference/lens) |
| **自己訓練分類器**（用開放授權字型合成訓練資料） | 把影像裡的字歸類到「Mortise 自己的可用字型清單」 | 自有 | 自有 | 可 | 可 | 標籤空間就是伺服器上裝得到的字型，辨識結果一定有字可用；YuzuMarker（MIT）的合成與訓練程式可以參考 | 要自己準備資料、訓練、評估；效果未知 | 部分（授權依據直讀，見 3.3 第 6 點；做法是推論） | [OFL 常見問答](https://github.com/silnrsi/font-gentium/blob/master/OFL-FAQ.txt) |

### 1.2 可在伺服器安裝與渲染的繁體中文開放字型

| 名稱 | 用途 | 字型授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **Noto Sans CJK TC／Noto Sans TC（思源黑體）** | 黑體類的預設字型 | SIL OFL 1.1 | 不適用 | 可 | 可（附著作權聲明與授權文） | 七種以上字重；Google Fonts 版是可變字型（字重 100 到 900）、TrueType 外框；實測全形字寬等於 1 em、嵌入旗標為 0（可安裝嵌入） | Google Fonts 版的預設實例是 Thin（字重 100），不認可變字型的渲染器會畫成極細；noto-cjk 程式庫的靜態版是 CFF 外框的 OTF，ReportLab 不吃（見 pdf.md） | 是 | [Sans/LICENSE](https://github.com/notofonts/noto-cjk/blob/main/Sans/LICENSE)、[Google Fonts OFL.txt](https://github.com/google/fonts/blob/main/ofl/notosanstc/OFL.txt)、[METADATA](https://github.com/google/fonts/blob/main/ofl/notosanstc/METADATA.pb) |
| **Noto Serif CJK TC／Noto Serif TC（思源宋體）** | 明體、宋體類的預設字型 | SIL OFL 1.1 | 不適用 | 可 | 可 | 同上；Google Fonts 版也是可變字型、TrueType 外框 | 同上 | 是 | [Serif/LICENSE](https://github.com/notofonts/noto-cjk/blob/main/Serif/LICENSE)、[METADATA](https://github.com/google/fonts/blob/main/ofl/notoseriftc/METADATA.pb) |
| **Source Han Sans／Source Han Serif**（Adobe 發行版，與 Noto CJK 是同一套字） | 同上 | SIL OFL 1.1；保留字型名稱「Source」 | 不適用 | 可 | 可 | 同上 | 改作後的字型不得沿用「Source」這個名稱 | 是 | [思源黑體 LICENSE](https://github.com/adobe-fonts/source-han-sans/blob/release/LICENSE.txt)、[思源宋體 LICENSE](https://github.com/adobe-fonts/source-han-serif/blob/release/LICENSE.txt) |
| **LXGW WenKai TC（霞鶩文楷 TC）** | 楷體類的預設字型 | SIL OFL 1.1 | 不適用 | 可 | 可 | Light、Regular、Bold 三種字重；TrueType 外框；實測全形字寬等於 1 em；2024-05-16 進 Google Fonts | 不是教育部標準楷書的字形，風格偏手寫楷體 | 是 | [OFL.txt](https://github.com/lxgw/LxgwWenkaiTC/blob/main/OFL.txt)、[METADATA](https://github.com/google/fonts/blob/main/ofl/lxgwwenkaitc/METADATA.pb) |
| **Iansui（芫荽）** | 貼近教育部標準字體的楷體風格字型 | SIL OFL 1.1 | 不適用 | 可 | 可 | 以 Klee One 的 SemiBold 為底，盡量貼近教育部標準字體，補台客語用字；2024-11-25 進 Google Fonts | README 自述沒有計畫做完整 Big5 字集，會缺字；只有一種字重 | 是 | [OFL.txt](https://github.com/ButTaiwan/iansui/blob/main/OFL.txt)、[README](https://github.com/ButTaiwan/iansui/blob/main/README.md) |
| **源泉圓體、源樣黑體** | 圓體類、另一種風格的黑體 | SIL OFL 1.1 | 不適用 | 可 | 可 | 由思源黑體改造；README 明寫可免費商用、可再散布與改造 | 沒有量測 | 是 | [源泉圓體 README](https://github.com/ButTaiwan/gensen-font/blob/master/README.md)、[源樣黑體 README](https://github.com/ButTaiwan/genyo-font/blob/master/README.md) |
| **jf open 粉圓** | 圓體類 | SIL OFL 1.1；保留字型名稱「open huninn」「huninn」 | 不適用 | 可 | 可 | justfont 釋出的開源圓體 | 字數與字重沒有查 | 是 | [LICENSE](https://github.com/justfont/open-huninn-font/blob/master/LICENSE)、[README](https://github.com/justfont/open-huninn-font/blob/master/README.md) |
| **Chiron Hei HK（昭源黑體）** | 香港字形的黑體 | SIL OFL 1.1（README 所述） | 不適用 | 可 | 可 | 由思源黑體香港版修改；2025-05-08 進 Google Fonts，可變字型，含斜體 | 字形依香港慣用寫法，不是台灣標準 | 部分（README 與 Google Fonts 中繼資料直讀；授權檔本身沒抓到） | [README](https://github.com/chiron-fonts/chiron-hei-hk/blob/master/README.md)、[METADATA](https://github.com/google/fonts/blob/main/ofl/chironheihk/METADATA.pb) |
| **全字庫正楷體、全字庫正宋體** | 楷體、宋體；罕用字覆蓋最廣 | 政府資料開放授權條款第 1 版，另列 OFL 1.1 | 不適用 | 可（須註明出處） | 應可，**要先直讀條款** | 字符數十萬以上（第三方說法是 108,029 個）；CNS 11643 字集 | 官方頁面讀不到；兩種授權並列時如何適用沒有讀到原文；只有一種字重 | 否（只有搜尋摘要） | [政府資料開放平台資料集 5961](https://data.gov.tw/dataset/5961) |
| **教育部標準楷書、標準宋體** | 教育部標準字形 | 創用 CC「姓名標示－禁止改作 3.0 臺灣版」 | 不適用 | 可，須標示「中華民國教育部」 | **有疑慮**：禁止改作，其他用途須書面申請 | 楷書 Version 3.00，13,067 字 | 「禁止改作」是否涵蓋內嵌時做子集、轉檔，沒有查到解釋；不建議內嵌或隨產品散布 | 否（只有搜尋摘要） | [教育部字形檔說明](https://language.moe.gov.tw/files/%E5%9C%8B%E5%AD%97%E6%A8%99%E6%BA%96%E5%AD%97%E9%AB%94%E5%AD%97%E5%BD%A2%E6%AA%94.pdf) |
| **台北黑體（Taipei Sans TC）** | 由思源黑體改良的黑體 | **沒有讀到條款原文** | 不適用 | 第三方說可免費商用 | 未知 | 以思源黑體為基礎 | 條款在官網下載頁，讀不到；在讀到原文之前不要用 | 否 | [翰字鑄造下載頁](https://sites.google.com/view/jtfoundry/zh-tw/downloads) |
| **cwTeX Q 字型** | 明、楷、圓、仿宋、黑 | 五套字型是 GPLv2 以上；另有一份只含漢字與標點的版本是 OFL 1.1 | 不適用 | **排除** | **排除** | — | GPL 版本直接排除；專案 README 自己警告「版權可能存有爭議」，建議有法律風險的情境不要用 | 是 | [README](https://github.com/l10n-tw/cwtex-q-fonts/blob/master/README.md) |
| **文泉驛正黑** | 這台機器上預裝的中文字型（pdf.md 的實測用過它） | **沒有查證** | 不適用 | 未知 | 未知 | 實測嵌入旗標為 8（可編輯嵌入）、全形字寬等於 1 em | 授權沒有查證（搜尋額度用完）；正式環境不要用它，改用上面的 Noto | 否 | 無 |

Google Fonts 程式庫裡主要文字為繁體中文的字族共 19 個（程式碼搜尋結果，都在 `ofl/` 目錄下）：Noto Sans TC、Noto Serif TC、Noto Sans HK、Noto Serif HK、LXGW WenKai TC、LXGW WenKai Mono TC、LXGW Marker Gothic、Iansui、Huninn、Chiron Hei HK、Chiron Sung HK、Chiron GoRound TC、Cactus Classical Serif、Chocolate Classical Sans、WDXL Lubrifont TC、Uoq Mun Then Khung、Bpmf Huninn、Bpmf Iansui、Bpmf Zihi Kai Std。【直讀】（[google/fonts](https://github.com/google/fonts) 的程式碼搜尋）。除了上表列出的幾個，其餘我只確認了它們在 `ofl/` 目錄下，沒有逐一讀授權檔。

### 1.3 英文開放字型，以及與 Microsoft 字型字寬相容的替代字型

| 名稱 | 用途 | 字型授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **Liberation Sans、Serif、Mono、Sans Narrow** | Arial、Times New Roman、Courier New、Arial Narrow 的替代 | SIL OFL 1.1；保留字型名稱「Liberation」，以及 Arimo、Tinos、Cousine | 不適用 | 可 | 可 | README：目標是在版面上相容 Times New Roman、Arial、Courier New；fontconfig 對照表把它們列為這四套 Microsoft 字型的替代 | 見 3.10：字寬相容不是百分之百 | 是 | [LICENSE](https://github.com/liberationfonts/liberation-fonts/blob/main/LICENSE)、[README](https://github.com/liberationfonts/liberation-fonts/blob/main/README.md) |
| **Arimo、Tinos、Cousine** | Arial、Times New Roman、Courier New 的替代 | SIL OFL 1.1 | 不適用 | 可 | 可 | Google Fonts 說明：Arimo 與 Arial、Tinos 與 Times New Roman「metrically compatible」；Arimo 是可變字型 | Arimo 的說明寫「2026 年 4 月更新並以 OFL 重新釋出」，舊版檔案的授權可能不同，用的時候要拿新版 | 是 | [Arimo OFL.txt](https://github.com/google/fonts/blob/main/ofl/arimo/OFL.txt)、[Arimo 說明](https://github.com/google/fonts/blob/main/ofl/arimo/DESCRIPTION.en_us.html)、[Tinos METADATA](https://github.com/google/fonts/blob/main/ofl/tinos/METADATA.pb)、[Cousine OFL.txt](https://github.com/google/fonts/blob/main/ofl/cousine/OFL.txt) |
| **Carlito** | Calibri 的替代 | SIL OFL 1.1；保留字型名稱「Carlito」 | 不適用 | 可 | 可 | README：由 Lato 衍生，與 Calibri「metric-compatible」；拉丁與西里爾字母 | 沒有拿 Calibri 實際比對 | 是 | [OFL.txt](https://github.com/googlefonts/carlito/blob/main/OFL.txt)、[README](https://github.com/googlefonts/carlito/blob/main/README.md) |
| **Caladea** | Cambria 的替代 | SIL OFL 1.1 | 不適用 | 可 | 可 | fontconfig 對照表把它列為 Cambria 的替代；四種樣式 | README 只說它由 Cambo 改成「較窄、新的量度」，沒有自己提到 Cambria；與 Cambria 的對應關係來自 fontconfig | 是 | [OFL.txt](https://github.com/huertatipografica/Caladea/blob/master/OFL.txt)、[README](https://github.com/huertatipografica/Caladea/blob/master/README.md) |
| **Gelasio** | Georgia 的替代 | SIL OFL 1.1 | 不適用 | 可 | 可 | Google Fonts 說明：Regular、Bold、Italic、Bold Italic 與 Georgia「metrics compatible」 | 沒有實際比對 | 是 | [OFL.txt](https://github.com/google/fonts/blob/main/ofl/gelasio/OFL.txt)、[說明](https://github.com/google/fonts/blob/main/ofl/gelasio/DESCRIPTION.en_us.html) |
| **Selawik** | Segoe UI 的替代 | SIL OFL 1.1（Microsoft 釋出）；保留字型名稱「Selawik」 | 不適用 | 可 | 可 | README：Segoe UI 的開源替代品 | README 自列的已知問題：缺少與 Segoe UI 相符的字距調整、微調（hinting）待改進 | 是 | [README](https://github.com/microsoft/Selawik/blob/master/README.md)、[LICENSE](https://github.com/microsoft/Selawik/blob/master/LICENSE.txt) |
| **Google Fonts 整體** | 其他英文字型；也是 Google Slides 認得的字型庫 | 多數 SIL OFL 1.1，部分 Apache 2.0，Ubuntu 字型是 Ubuntu Font License 1.0 | 不適用 | 可 | 可，依各字型的授權 | 程式庫 README：所有字型都帶有再散布的許可，可以自己架設 | 每個字型要各自讀授權檔；有些帶保留字型名稱 | 是 | [google/fonts README](https://github.com/google/fonts/blob/main/README.md) |

### 1.4 Microsoft 字型

| 名稱 | 用途 | 字型授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **Windows 與 Office 內附的字型**（Arial、Times New Roman、Calibri、Cambria、微軟正黑體、新細明體、標楷體等） | 使用者電腦上實際顯示文件的字型 | 隨 Windows 或 Office 的軟體授權；不得複製到別的機器、不得把軟體當伺服器軟體用 | 不適用 | **不可**裝在 Linux 伺服器上做 SaaS 渲染（沒有任何條款允許） | **不可** | — | Windows 11 授權條款：字型屬於軟體的一部分，不得複製、不得安裝在伺服器供網路使用、不得用來提供商業代管服務；Microsoft 字型常見問答：一般不允許再散布，文件內嵌是例外 | 部分（Windows 11 條款與 Product Terms 直讀；字型常見問答只有搜尋摘要） | [Windows 11 授權條款](https://www.microsoft.com/content/dam/microsoft/usetm/documents/windows/11/oem-(pre-installed)/UseTerms_OEM_Windows_11_English.pdf)、[Product Terms](https://www.microsoft.com/licensing/terms/product/ForallSoftware/all)、[字型常見問答](https://learn.microsoft.com/typography/fonts/font-faq) |
| **Core fonts for the Web**（2002 年以前 Microsoft 免費釋出的舊版字型檔） | 舊版的 Arial、Times New Roman 等 | 專屬 EULA | 不適用 | **灰色地帶**：EULA 允許安裝與使用不限份數，但沒有談到伺服器或商業服務；需法務確認 | **不可**：不得為營利而散布，不得包進自己的產品 | EULA：可安裝使用不限份數；可原封不動連同 EULA 再散布 | 不得改名、編輯或做衍生作品（內嵌文件時做子集除外）；是二十多年前的舊版；不含 Calibri、Cambria 與任何中文字型（這句是我的認知，清單沒有直讀到） | 部分（EULA 直讀；字型清單沒有直讀） | [EULA](https://corefonts.sourceforge.net/eula.htm) |
| **Aptos** | Office 現行預設字型 | 沒有讀到條款原文 | 不適用 | **未知，傾向不可** | **不可** | Microsoft 下載中心提供 Aptos 字型包（4.40 版，2024-06-04），說明寫的是給「無法使用 Office 字型服務的應用程式」做本機安裝 | 下載頁沒有授權文字；第三方轉述字型檔內的授權只允許依嵌入旗標內嵌到內容、暫時下載到印表機，其他用途禁止；Microsoft 字型說明頁另指向「伺服器安裝」的獨立授權 | 部分（下載頁直讀；授權內容只有第三方） | [下載頁](https://www.microsoft.com/en-us/download/details.aspx?id=106087)、[Office Watch](https://office-watch.com/2023/aptos-microsoft-fonts-legally-do/) |
| **向 Monotype 購買 Microsoft 字型的延伸授權** | 合法在伺服器上使用 Microsoft 字型的唯一已知途徑 | 商業授權 | 不適用 | 可，**需付費** | 需另談（Monotype 有「應用程式授權」類別） | Microsoft 字型常見問答：許多 Microsoft 擁有的字型可向 Monotype 取得延伸授權；Monotype 有「伺服器授權」類別，適用雲端服務與 SaaS | 價格、哪些字型有得授權、新細明體與標楷體（著作權屬威鋒數位）是否在內，都沒有查到 | 否（只有搜尋摘要） | [Monotype 授權類別說明](https://support.monotype.com/en/articles/7872341-licensing)、[Monotype 名詞定義](https://cms-prod.monotype.com/resources/expertise/font-licensing-management-terms-and-definitions) |

### 1.5 台灣商用字型與 Adobe Fonts

| 名稱 | 用途 | 字型授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **華康字型（威鋒數位）** | 台灣最常見的商用中文字型；新細明體、標楷體也是它的 | 商業授權，依使用方式個別簽約 | 不適用 | 伺服器用途**需另購**「伺服器專用字型授權」 | 需另談內嵌授權 | 官網列有「伺服器專用字型授權」，對象是要把字型裝在伺服器上的客戶 | 一般授權書明文禁止把字型轉檔、重製後放上任何伺服器；價格沒有公開 | 否（官網內容只有搜尋摘要） | [授權方案](https://www.dynacw.com.tw/business/business_licensing.aspx)、[使用授權範圍](https://www.dynacw.com.tw/business/licensing_usage.aspx)、[授權書](https://www.dynacw.com.tw/fileupload/annual_products/files/License_Font_Product.pdf) |
| **文鼎字型（iFontCloud 森澤文鼎雲字庫）** | 商用中文字型 | 年租授權 | 不適用 | 伺服器用途**不在標準授權內**，需洽談 | 內嵌於軟體、App 不在標準授權內，需洽談 | 另有「伺服器／自動化印刷字體授權」，適用在伺服器上批次產生 PDF、雲端印刷服務 | 條款明文不得安裝在內部或外部網路的伺服器；教育版與學生版不予授權；價格沒有查到 | 否（官網內容只有搜尋摘要） | [授權範圍](https://ifontcloud.com/index/eula.jsp?lang=zh&country=TW)、[方案](https://ifontcloud.com/index/plan_detail.jsp?lang=zh&country=TW)、[伺服器授權說明](https://www.ifontcloud.com/index/newknowledge_detail.jsp?id=170) |
| **justfont（金萱等）** | 商用中文字型 | 商業授權 | 不適用 | 伺服器用途需直接洽談 | 內嵌需直接洽談 | — | 官方部落格：內嵌字型屬特殊需求，要直接談授權；沒有找到公開的伺服器或 SaaS 方案 | 否 | [justfont 字型授權常見問題](https://blog.justfont.com/?p=15920) |
| **Adobe Fonts** | Creative Cloud 訂閱附的字型庫 | 訂閱授權 | 不適用 | **不可**裝在伺服器 | **不可** | 內嵌到 PDF 有授權 | Adobe 說明：授權與伺服器安裝不相容；不能自己架設網頁字型；字型檔不得移動或複製到別的電腦 | 否（只有搜尋摘要） | [Adobe Fonts 授權常見問答](https://helpx.adobe.com/sg/fonts/web/font-licensing/font-licensing.html) |

### 1.6 字型量度與處理工具

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **fontTools 4.68.0** | 讀寫字型檔的每一張表 | MIT | 不適用 | 可 | 可 | 實測讀出：em 方框單位數、x 字高、大寫字高、嵌入旗標、三組上伸下伸值、每個字的前進寬度、字元對照表、名稱表裡的著作權與授權字串、可變字型的軸與實例 | 需要 Python 3.11 以上；純 Python，大型中文字型載入較慢（沒有量時間） | 是 | [LICENSE](https://github.com/fonttools/fonttools/blob/main/LICENSE)、[PyPI](https://pypi.org/project/fonttools/) |
| **HarfBuzz 與 uharfbuzz 0.57.0** | 文字排版（把字串變成定位好的字形序列），用來算一行字的實際寬度 | HarfBuzz：「Old MIT」；uharfbuzz：Apache-2.0 | 不適用 | 可 | 可 | — | 本次只查了授權，沒有實測 | 是（授權直讀） | [HarfBuzz COPYING](https://github.com/harfbuzz/harfbuzz/blob/main/COPYING)、[uharfbuzz LICENSE](https://github.com/harfbuzz/uharfbuzz/blob/main/LICENSE) |
| **FreeType** | 把字形畫成點陣圖（合成訓練資料、渲染比對都會用到） | 雙授權二選一：FreeType License（類似 BSD，須在產品文件註明使用了 FreeType），或 GPLv2 | 不適用 | 可（**選 FreeType License**） | 可（選 FreeType License，並在文件註明） | `FT_Get_FSType_Flags` 可讀嵌入旗標 | 不要選 GPLv2 那一邊 | 是 | [LICENSE.TXT](https://github.com/freetype/freetype/blob/master/LICENSE.TXT) |
| **PDF 字型字典裡的量度**（經 PDFium 或 pdfminer.six 讀取） | 不取出字型程式，直接從原生 PDF 讀字寬與上伸下伸 | 見 pdf.md | 不適用 | 可 | 可 | pdfminer.six 會讀字型描述項的 `Ascent`、`Descent`、`ItalicAngle`、`FontBBox`、`MissingWidth`、`Leading`、`Flags`、`FontName`，以及字型字典的 `Widths`；PDFium 有 `FPDFFont_GetAscent`、`GetDescent`、`GetGlyphWidth` | 只有原生 PDF 有；掃描檔與照片沒有 | 是 | [pdffont.py](https://github.com/pdfminer/pdfminer.six/blob/master/pdfminer/pdffont.py)、[pdf.md](pdf.md) |

## 2. 字型對照：原文件的字型，伺服器上拿什麼渲染

這張表整理第 3 節的事實。「依據」欄寫的是這個對應關係從哪裡來。

| 原文件可能用的字型 | 伺服器上的開放替代 | 字寬是否相容 | 依據 |
|---|---|---|---|
| Arial | Liberation Sans、Arimo | 是（自述與對照表） | fontconfig 對照表【直讀】；Arimo 說明【直讀】 |
| Arial Narrow | Liberation Sans Narrow | 是（對照表） | fontconfig 對照表【直讀】 |
| Times New Roman | Liberation Serif、Tinos | 是（自述與對照表） | 同上 |
| Courier New | Liberation Mono、Cousine | 是（對照表） | 同上 |
| Calibri | Carlito | 是（自述與對照表） | Carlito README【直讀】；fontconfig 對照表【直讀】 |
| Cambria | Caladea | 是（對照表） | fontconfig 對照表【直讀】 |
| Georgia | Gelasio | 是（自述與對照表） | Gelasio 說明【直讀】；fontconfig 對照表【直讀】 |
| Segoe UI | Selawik | **不完全**：缺字距調整 | Selawik README【直讀】 |
| Aptos | 沒有可靠的替代 | 否 | 只找到一個單人專案 Akasia 自稱相容【第三方】，見 render.md |
| Verdana、Tahoma、Trebuchet MS | 沒有查到 | 未知 | fontconfig 對照表裡沒有這幾個 |
| 微軟正黑體 | Noto Sans TC | **否**（只有全形字同寬，見 3.5 第 6 點） | 沒有任何來源說相容 |
| 新細明體、細明體 | Noto Serif TC | **否**（同上） | 同上 |
| 標楷體 | LXGW WenKai TC；全字庫正楷體（待讀條款） | **否**（同上） | 同上 |

## 3. 逐條事實與來源

### 3.1 字型辨識

1. **Adobe DeepFont 沒有找到官方開源。** Adobe Research 的論文頁沒有程式碼連結；在 GitHub 搜尋 adobe、adobe-research、adobe-fonts、adobe-type-tools 四個組織，「deepfont」與「font recognition」都是零筆；名稱含 deepfont 的程式庫共 12 個，全是個人的重製品，星數最高的是 robinreni96/Font_Recognition-DeepFont（281 星，MIT）。【搜尋摘要】[Adobe Research](https://research.adobe.com/publication/deepfont-identify-your-font-from-an-image)；【直讀】GitHub 程式庫搜尋。「沒找到」不等於「不存在」，但可以確定沒有容易取得的官方版本。
2. DeepFont 當年是內建在 Photoshop 等 Adobe 產品裡的功能，論文自述前五名準確率 80% 以上。【搜尋摘要】[Adobe Research](https://research.adobe.com/publication/deepfont-identify-your-font-from-an-image)、[伊利諾大學新聞](https://ece.illinois.edu/newsroom/3462)
3. AdobeVFR 資料集含 2,383 個字型類別，下載點是一個 Dropbox 連結。【第三方】[robinreni96 README](https://github.com/robinreni96/Font_Recognition-DeepFont/blob/master/README.md)。它的授權條款沒有查到。
4. **YuzuMarker.FontDetection 的程式碼是 MIT。** 授權檔開頭：「MIT License／Copyright (c) 2021 gyro永不抽风」。【直讀】[LICENSE](https://github.com/JeffersonQin/YuzuMarker.FontDetection/blob/master/LICENSE)
5. YuzuMarker 的 README：自稱「First-ever CJK (Chinese, Japanese, Korean) Font Recognition」；資料集用「CJK font pack by VCB-Studio」加上從 pixiv 抓的數千張背景圖合成，pixiv 資料「will not be shared since it is just randomly scraped」；資料集 2023-11-18 起放在 Hugging Face；預訓練模型放在 Hugging Face；模型用 PyTorch 2.0 的 `torch.compile` 訓練，要照 `demo.py` 的方式載入。【直讀】[README](https://github.com/JeffersonQin/YuzuMarker.FontDetection/blob/master/README.md)
6. YuzuMarker 在作者自己的合成資料上的準確率：ResNet-18 介於 14% 到 39%，ResNet-34 為 32.03%，ResNet-50 最高 48.99%（該列註記實作有錯誤），次高 48.45%。【直讀】同上。這些數字是在它自己的資料上量的，對 Mortise 的文件影像沒有參考價值，只能說明「幾千種中日韓字型的細分類很難」。
7. YuzuMarker 的權重與資料集放在 Hugging Face，我讀不到，不知道權重標的是什麼授權。搜尋摘要說它的資料集卡片標 MIT，權重卡片沒有提到。【搜尋摘要】。另外，README 沒有說明 VCB-Studio 字型包裡的字型是什麼授權。【直讀】。【推論】這個模型的類別就是那個字型包裡的字型，如果字型包含有未經授權散布的商用字型，權重的法律狀態就不乾淨；在確認之前，只能參考它的程式碼，不要用它的權重。
8. **Lens（mixfont/lens）是非商用授權。** README 的授權段落：「This project is provided for personal, academic, and non-commercial use only.」「Commercial use is strictly prohibited without written permission.」程式庫裡沒有獨立的授權檔。README 另說它用 ResNet18、只用開源字型訓練、2026-03-16 釋出權重、推論時先用 Tesseract 找最大的字詞。【直讀】[README](https://github.com/mixfont/lens/blob/main/README.md)
9. Lens 有付費的商用 API，端點是 `POST https://api.mixfont.com/v1/api/lens`，以 `x-api-key` 標頭驗證；Mixfont 首頁自稱在內部影像上辨識率 91%。價格沒有查到。【搜尋摘要】[API 文件](https://www.mixfont.com/docs/api-reference/lens)、[價格頁](https://www.mixfont.com/pricing)
10. **Storia-AI/font-classify 沒有授權檔。** `LICENSE`、`LICENSE.md`、`LICENSE.txt`、`COPYING` 四個檔名在主分支都是 404；GitHub 搜尋介面回報的授權欄位是空的。README：分類約 3,000 種 Google Fonts；權重在 Hugging Face 的 storia/font-classify-onnx；附資料產生腳本（字型檔、COCO 背景圖、維基百科文句）與訓練腳本，預設 ResNet50。最後一次推送 2024-03-26。【直讀】[README](https://github.com/Storia-AI/font-classify/blob/main/README.md)
11. gaborcselle/font-identifier 是 MIT。作者自述：一天做完；ResNet18 在 2,400 張測試圖上準確率 95% 以上；會混淆 Helvetica 與 Arial、Trebuchet 與 Verdana；要實用得用大得多的字型集重訓。【直讀】[README](https://github.com/gaborcselle/font-identifier/blob/main/README.md)、[LICENSE](https://github.com/gaborcselle/font-identifier/blob/main/LICENSE)
12. **Azure AI Document Intelligence 有字型屬性附加功能。** Azure 官方 .NET SDK 的範例：分析時指定 `DocumentAnalysisFeature.FontStyling`，回傳的每個樣式物件有 `SimilarFontFamily`（範例註解寫「e.g., 'Arial, sans-serif'」）、`FontStyle`、`FontWeight`、`Color`、`BackgroundColor`。【直讀】[Azure SDK 範例](https://github.com/Azure/azure-sdk-for-net/blob/main/sdk/documentintelligence/Azure.AI.DocumentIntelligence/samples/Sample_AddOnCapabilities.md)。官方文件頁讀不到，我讀到的是一份第三方鏡像（文件日期 2025-02-27）：功能名稱 `ocr.font`，REST 參數 `features=styleFont`，API 版本 2024-11-30，並說「Some add-on features incur an extra cost」。【第三方】。它對中文字回傳什麼字族、是否要加價，都沒有查到。
13. **WhatFontIs 有 API。** 官方說明頁：端點 `https://www.whatfontis.com/api2/`，POST，帶 API 金鑰與影像，回傳 JSON；流量上限依方案，可申請完整存取；用 API 必須放連回 WhatFontIs 的連結。【搜尋摘要】[API 說明頁](https://whatfontis.com/API-identify-fonts-from-image.html)。API 的價格與是否支援中文字型沒有查到。各家字型資料庫的規模，不同來源的數字互相矛盾。
14. MyFonts 的 WhatTheFont、Fontspring 的 Matcherator 都沒有找到公開的開發者 API。【搜尋摘要】，證據薄弱。
15. **中文字型辨識的商用 API 沒有查到。** 用「求字體網、識字體網、字由、字體識別 API」搜尋，回來的都是一般文字辨識（OCR）產品，沒有字型辨識。【搜尋摘要】
16. 學術界的中文字型資料集：北京大學的 VFRWild-CHS（2015，自稱第一個公開的自然影像中文字型辨識資料集）；南開大學的 Nankai Chinese Font Style 與 SwordNet（2022）；DropRegion 論文（2017）說當時沒有公開的中文字型辨識資料集，自己用 25 種字型做了一個。【搜尋摘要】[北京大學專案頁](https://www.wict.pku.edu.cn/zlian/frwild/)、[SwordNet](https://doaj.org/article/727f63e3554c41049d012dec871f92d9)、[DropRegion](https://arxiv.org/pdf/1703.05870)。三者的授權都沒有查到。

### 3.2 開放授權的中文字型

1. **Noto Sans CJK 與 Noto Serif CJK 是 SIL OFL 1.1。** noto-cjk 程式庫的 `Sans/LICENSE` 與 `Serif/LICENSE` 開頭都是「This Font Software is licensed under the SIL Open Font License, Version 1.1.」【直讀】[Sans/LICENSE](https://github.com/notofonts/noto-cjk/blob/main/Sans/LICENSE)、[Serif/LICENSE](https://github.com/notofonts/noto-cjk/blob/main/Serif/LICENSE)
2. Noto CJK 在 Google Fonts 上的名稱不同：TC 對應 Traditional Chinese，HK 對應 Hong Kong。【直讀】[noto-cjk README](https://github.com/notofonts/noto-cjk/blob/main/README.md)
3. noto-cjk 程式庫提供的格式：可變字型合集（OTC）、各語言的可變字型（OTF 與 TTF 兩種）、各地區的子集可變字型、靜態的合集、各語言的靜態 OTF、各地區的靜態子集 OTF。【直讀】[Sans/README](https://github.com/notofonts/noto-cjk/blob/main/Sans/README.md)。靜態版只有 OTF，沒有靜態 TTF。
4. **思源黑體與思源宋體是 SIL OFL 1.1，保留字型名稱「Source」。** 思源黑體：「Copyright 2014-2025 Adobe (http://www.adobe.com/), with Reserved Font Name 'Source'.」思源宋體：「Copyright 2017-2022 Adobe … with Reserved Font Name 'Source'.」【直讀】[思源黑體 LICENSE](https://github.com/adobe-fonts/source-han-sans/blob/release/LICENSE.txt)、[思源宋體 LICENSE](https://github.com/adobe-fonts/source-han-serif/blob/release/LICENSE.txt)
5. Google Fonts 的 Noto Sans TC 與 Noto Serif TC：授權欄位 OFL；檔名 `NotoSansTC[wght].ttf`、`NotoSerifTC[wght].ttf`（可變字型）；Noto Sans TC 的授權檔著作權行是「Copyright 2014-2021 Adobe … with Reserved Font Name 'Source'」。【直讀】[Noto Sans TC METADATA](https://github.com/google/fonts/blob/main/ofl/notosanstc/METADATA.pb)、[OFL.txt](https://github.com/google/fonts/blob/main/ofl/notosanstc/OFL.txt)、[Noto Serif TC METADATA](https://github.com/google/fonts/blob/main/ofl/notoseriftc/METADATA.pb)
6. **Google Fonts 的整體授權。** 程式庫 README：多數字型是 SIL OFL 1.1，部分是 Apache 2，Ubuntu 字型是 Ubuntu Font License 1.0；最上層目錄名稱就代表其下所有檔案的授權；「all the fonts available here are licensed with permission to redistribute, subject to the license terms」；「It is important to always read the license for every font that you use.」【直讀】[google/fonts README](https://github.com/google/fonts/blob/main/README.md)
7. Google Fonts 常見問答說可以用於商業用途，包含用在販售的產品裡；使用與再散布的條件依各字型的授權。【搜尋摘要】[Google Fonts 常見問答](https://developers.google.com/fonts/faq)
8. LXGW WenKai TC：「Copyright 2022-2026 The LXGW WenKai Project Authors … SIL Open Font License, Version 1.1」；Google Fonts 收錄日期 2024-05-16，Light、Regular、Bold 三個靜態檔。【直讀】[OFL.txt](https://github.com/lxgw/LxgwWenkaiTC/blob/main/OFL.txt)、[METADATA](https://github.com/google/fonts/blob/main/ofl/lxgwwenkaitc/METADATA.pb)
9. Iansui（芫荽）：SIL OFL 1.1；基於 Fontworks 以開源授權釋出的 Klee One，以 SemiBold 為底；超過 4,000 個字符修改過、補字超過 2,000 字；「盡可能調整字形貼近教育部標準字體」；「目前沒有計畫製作完整 Big5 字集」。【直讀】[OFL.txt](https://github.com/ButTaiwan/iansui/blob/main/OFL.txt)、[README](https://github.com/ButTaiwan/iansui/blob/main/README.md)
10. 源泉圓體與源樣黑體：README 寫「本字型基於 SIL Open Font License 1.1 授權。您可以免費商用，無需支付授權費用。您可基於本授權規定再散佈或改造本字型。」源泉圓體的授權檔沿用 Adobe 的著作權行與保留字型名稱「Source」。【直讀】[源泉圓體 README](https://github.com/ButTaiwan/gensen-font/blob/master/README.md)、[授權檔](https://github.com/ButTaiwan/gensen-font/blob/master/SIL_Open_Font_License_1.1.txt)、[源樣黑體 README](https://github.com/ButTaiwan/genyo-font/blob/master/README.md)
11. jf open 粉圓：SIL OFL 1.1；授權檔列出三段著作權（Kosugi Maru、Varela Round、jf open huninn），保留字型名稱「open huninn」與「huninn」。【直讀】[LICENSE](https://github.com/justfont/open-huninn-font/blob/master/LICENSE)
12. 昭源黑體：README 寫採用 SIL OFL 1.1，由思源黑體香港版修改而成。【直讀】[README](https://github.com/chiron-fonts/chiron-hei-hk/blob/master/README.md)。授權檔我試的路徑是 404，沒有讀到本文；Google Fonts 的中繼資料標 OFL，收錄日期 2025-05-08。【直讀】[METADATA](https://github.com/google/fonts/blob/main/ofl/chironheihk/METADATA.pb)
13. **cwTeX Q 要排除。** README：五套字型（中明、中楷、中圓、中仿宋、粗黑）是 GPLv2 以上；2014 年四位作者同意把漢字與中文常用標點另外獨立，以 SIL OFL 1.1 授出；「由於本專案內容的版權可能存有爭議……我們建議，在可能存在法律風險的使用情境中避免使用本專案字體」。【直讀】[README](https://github.com/l10n-tw/cwtex-q-fonts/blob/master/README.md)
14. **全字庫正楷體、正宋體。** 政府資料開放平台的資料集頁列出兩種授權：「政府資料開放授權條款－第1版」與「開放字型授權條款 OFL-1.1」，計費方式免費；字型有正宋體與正楷體兩種。【搜尋摘要】[資料集 5961](https://data.gov.tw/dataset/5961)。字符數 108,029 個是第三方文章的說法。【第三方】。官方頁面我讀不到，兩種授權並列時怎麼適用沒有看到原文。
15. **教育部標準楷書、標準宋體。** 教育部的字形檔說明：著作權採創用 CC「姓名標示－禁止改作 3.0 臺灣版」授權，引用時須標示「中華民國教育部」，其他用途須書面向終身教育司申請；楷書 Version 3.00，13,067 字。【搜尋摘要】[教育部字形檔說明](https://language.moe.gov.tw/files/%E5%9C%8B%E5%AD%97%E6%A8%99%E6%BA%96%E5%AD%97%E9%AB%94%E5%AD%97%E5%BD%A2%E6%AA%94.pdf)
16. 【推論】教育部字型的「禁止改作」對 Mortise 是實質障礙：把字型內嵌進 PDF 通常要做子集，寫出 IDML 或隨桌面版散布也可能涉及轉檔。這些算不算改作，沒有查到主管機關的解釋。在取得書面同意之前，教育部字型只適合「裝在伺服器上原封不動地拿來渲染比對」，不適合內嵌或散布。
17. 台北黑體：第三方報導說由翰字鑄造以思源黑體為基礎改良，下載前要先讀授權條款。條款原文在官網下載頁，我讀不到。【第三方】[數位時代](https://www.bnext.com.tw/article/53883/jt-foundry-taipei-sans-tc)

### 3.3 SIL OFL 1.1 的條款重點

以下第 1 點讀自思源黑體的授權檔，第 2 到 7 點讀自 SIL 的 OFL 常見問答（2023 年 11 月的 1.1-update7 版，取自 SIL 字型專案內附的副本）。【直讀】[思源黑體 LICENSE](https://github.com/adobe-fonts/source-han-sans/blob/release/LICENSE.txt)、[OFL-FAQ.txt](https://github.com/silnrsi/font-gentium/blob/master/OFL-FAQ.txt)

1. 五個條件：(1) 字型本身不得單獨販售；(2) 可以與任何軟體一起打包、再散布、販售，只要每份副本帶有著作權聲明與授權文；(3) 改作版不得使用保留字型名稱；(4) 不得用著作權人的名義替改作版背書；(5) 字型本身必須維持 OFL，但「does not apply to any document created using the Font Software」。
2. **可以和非開源的商業軟體一起散布。** 問答 1.3：程式不必因此開源，「The intent of the license is to allow aggregation or bundling with software under restricted licensing as well.」問答 1.4：可以販售內含這些字型的軟體，例子包含文書處理器、設計與出版應用程式、行動應用程式。
3. **可以放在伺服器上使用。** 問答 1.9：可以把字型放在伺服器供內部網路使用。
4. **可以內嵌到文件。** 問答 1.12：可以完整內嵌或子集內嵌，「The restrictions regarding font modification and redistribution do not apply, as the font is not intended for use outside the document.」問答 1.13：內嵌不會改變文件本身的授權。問答 1.10：字型內嵌在文件裡或打包在程式裡時，是唯一可以不附授權全文的情況。
5. 隨行動應用程式打包時，至少要附著作權聲明、授權聲明與授權文，放在「關於」畫面即可，只需要附實際用到的字型。問答 1.20。【推論】桌面版比照辦理。
6. **用 OFL 字型產生非字型的輸出是正常使用。** 問答 1.26：AI 設計工具可以使用 OFL 字型，只要產出的不是字型。問答 1.25：拿 OFL 字型當訓練資料去產生「新的字型」並以別的授權釋出，不行。【推論】用 OFL 字型畫出文字影像來訓練一個「字型分類器」，產出的是分類模型不是字型，落在 1.26 這一邊。這是我的解讀，問答沒有直接講分類器。
7. **做子集算改作。** 問答 2.6：把網頁字型做子集「is considered modification」，OFL 允許，但通常就不能沿用保留字型名稱。【推論】如果 Mortise 的線上編輯器要把子集過的字型檔傳到瀏覽器，帶有保留字型名稱的字型（Carlito、Liberation、Selawik、jf open 粉圓，以及名稱含「Source」的思源字型）要嘛傳完整檔案，要嘛改名。內嵌進 PDF 不受這一點影響（見第 4 點）。Noto Sans TC 的保留名稱是「Source」，字型名稱裡沒有這個字，子集後沿用 Noto 的名稱不違反這一條，但 Noto 是 Google 的商標，這一點我沒有查。
8. 問答 1.14 談到從 PDF 把 OFL 字型取出來：通常只拿得到部分外框，不是完整字型；取出來的東西仍然是 OFL，不能改用別的授權散布；「We strongly discourage any font extraction - we recommend directly using the font sources instead」。

### 3.4 Microsoft 字型的授權

1. **Windows 11 授權條款（2024 年 4 月版）把字型算在軟體裡。** 第 1.a 條：本合約適用於 Windows 軟體以及「any fonts, icons, images or sound files included with the software」。【直讀】[Windows 11 授權條款](https://www.microsoft.com/content/dam/microsoft/usetm/documents/windows/11/oem-(pre-installed)/UseTerms_OEM_Windows_11_English.pdf)
2. 同一份條款第 2.c 條的限制，使用者不得：「use or virtualize features of the software separately」；「publish, copy (other than the permitted backup copy), rent, lease, or lend the software」；「use the software as server software or to operate the device as a server」；「use the software to offer commercial hosting services」；「install the software on a server for remote access or use over a network」。【直讀】同上。【推論】把 Windows 的字型檔複製到 Linux 伺服器，同時踩到「複製」「把軟體的一部分分開使用」「用於商業代管服務」三條。
3. **Microsoft 的大量授權通用條款有一條專講字型。** Product Terms 的「Font Components, Images, and Sounds」：客戶執行軟體時，「may use the fonts included with or installed by that software to display and print content」；「Customer may only embed fonts in content as permitted by the embedding restrictions in the fonts」；可以暫時下載到印表機。同頁另有：「Customer is not permitted to use or access software on devices that are under the management or control of a third party」，例外是經授權的委外業者。【直讀】[Product Terms](https://www.microsoft.com/licensing/terms/product/ForallSoftware/all)。【推論】字型的使用權綁在「執行該軟體」上，沒有執行 Windows 或 Office 的機器就沒有使用這些字型的依據。
4. **Microsoft 字型常見問答。** 我讀不到原頁，以下是三次搜尋的摘要彼此一致的部分：隨 Windows 提供的字型一般不允許再散布，「document font embedding」是特例；使用者無權把字型從 Windows 複製到網頁伺服器自行架設，也無權轉成網頁字型格式；在 CSS 裡只寫字型名稱不需要授權，因為「the 'use' of the font occurs on the Windows device, not on your web server」；要做 Windows 授權不允許的事（例如隨 App、遊戲、裝置出貨），可以向 Monotype 取得延伸授權。【搜尋摘要】[字型常見問答](https://learn.microsoft.com/typography/fonts/font-faq)
5. 【推論】上一點的 CSS 那句話對 Mortise 很重要：**在輸出的 PPTX、DOCX 裡寫上「Calibri」「微軟正黑體」這些字型名稱，不等於使用了這些字型**。字型的使用發生在使用者自己那台有授權的電腦上。Mortise 的伺服器可以完全不持有 Microsoft 字型，照樣產出指定這些字型名稱的檔案。這是類推，Microsoft 的原文講的是網頁，沒有講文件。
6. Microsoft 字型說明頁的搜尋摘要：微軟正黑體的著作權行是「©2016 Microsoft Corporation」，設計者一欄在不同語言版本寫法不一（Monotype Imaging，或 China Type Design），授權區塊連到字型常見問答，以及一個說明如何為「enterprises, web developers, for hardware & software redistribution or server installations」取得授權的頁面。細明體與新細明體的著作權行是「© DynaComware Corp. 2015」，字型廠商 DynaLab。【搜尋摘要】[微軟正黑體](https://learn.microsoft.com/typography/font-list/microsoft-jhenghei)、[細明體](https://learn.microsoft.com/en-us/typography/font-list/mingliu)
7. Microsoft 問答社群有人問「能不能替 Linux 伺服器買 Microsoft 字型的伺服器授權」，搜尋結果裡沒有看到回答。【搜尋摘要】[該問題](https://learn.microsoft.com/en-us/answers/questions/1188261/can-we-buy-server-licence-for-microsoft-fonts-for)。社群回答不是 Microsoft 的正式授權聲明。
8. **Monotype 的授權類別。** 伺服器授權的定義：允許第三方使用放在雲端服務、SaaS 應用程式或內容管理系統上的字型；應用程式授權：把字型內嵌在手機、平板應用程式與 SaaS 產品；伺服器產出的文件若是商業性的內嵌文件（電子書、雜誌等）要另外的授權。【搜尋摘要】[Monotype 授權說明](https://support.monotype.com/en/articles/7872341-licensing)、[名詞定義](https://cms-prod.monotype.com/resources/expertise/font-licensing-management-terms-and-definitions)。價格沒有查到。
9. **Core fonts for the Web 的 EULA。** 「You may install and use an unlimited number of copies of the SOFTWARE PRODUCT.」「You may reproduce and distribute an unlimited number of copies … provided that each copy shall be a true and complete copy … and shall be accompanied by a copy of this EULA. Copies of the SOFTWARE PRODUCT may not be distributed for profit either on a standalone basis or included as part of your own product.」「You may not rename, edit or create any derivative works from the SOFTWARE PRODUCT, other than subsetting when embedding them in documents.」【直讀】[EULA](https://corefonts.sourceforge.net/eula.htm)
10. 【推論】Core fonts 的 EULA 沒有禁止商業使用，也沒有提到伺服器，所以「把這批舊版 Arial、Times New Roman 裝在伺服器上渲染」字面上沒有違反哪一條。但它是 2002 年以前的條款，沒有預想 SaaS；絕對不能包進桌面版安裝檔（「included as part of your own product」）。有 Liberation 與 Arimo 可用，沒有必要冒這個險。
11. **Aptos。** Microsoft 下載中心的頁面：「Microsoft Aptos fonts for local installation for use in applications that do not have access to the Office Font Service」，版本 4.40，發布日期 2024-06-04，檔案 2.8 MB；頁面上沒有授權文字。【直讀】[下載頁](https://www.microsoft.com/en-us/download/details.aspx?id=106087)。第三方文章轉述 Aptos 字型檔內的授權：只允許依嵌入旗標內嵌到內容、暫時下載到印表機，其他用途禁止；Microsoft 的 Aptos 說明頁另外指向伺服器安裝的獨立授權。【第三方】[Office Watch](https://office-watch.com/2023/aptos-microsoft-fonts-legally-do/)；【搜尋摘要】[Aptos 說明頁](https://learn.microsoft.com/typography/font-list/aptos)。字型檔本身我下載不到，沒有親自讀它的授權字串。

### 3.5 字寬相容的替代字型

1. **fontconfig 內建的「字寬相容」對照表。** 檔頭的表格（我讀的是 GitHub 上的鏡像）：Arial 對 Liberation Sans、Arimo、Albany；Arial Narrow 對 Liberation Sans Narrow；Times New Roman 對 Liberation Serif、Tinos、Thorndale；Courier New 對 Liberation Mono、Cousine、Cumberland；Cambria 對 Caladea；Calibri 對 Carlito；Symbol 對 SymbolNeu；Georgia 對 Gelasio。【直讀】[30-metric-aliases.conf](https://github.com/behdad/fontconfig/blob/master/conf.d/30-metric-aliases.conf)。官方程式庫在 gitlab.freedesktop.org，我讀不到，鏡像是否為最新版沒有確認。
2. 這張表裡沒有任何中日韓字型，也沒有 Aptos、Segoe UI、Verdana、Tahoma、Trebuchet MS。【直讀】同上
3. LibreOffice 的預設字型替代表同樣沒有 Aptos、微軟正黑體、標楷體的條目，新細明體的替代清單裡沒有 Noto 或思源。見 [render.md](render.md) 3.1 第 8 點。
4. Liberation 字型：授權檔寫「Digitized data copyright (c) 2010 Google Corporation with Reserved Font Arimo, Tinos and Cousine. Copyright (c) 2012 Red Hat, Inc. with Reserved Font Name Liberation.」，SIL OFL 1.1；README：「aims to provide document layout compatibility as usage of Times New Roman, Arial, Courier New」。【直讀】[LICENSE](https://github.com/liberationfonts/liberation-fonts/blob/main/LICENSE)、[README](https://github.com/liberationfonts/liberation-fonts/blob/main/README.md)
5. Carlito、Caladea、Arimo、Tinos、Cousine、Gelasio、Selawik 的授權與自述，見總表 1.3 的來源連結，全部【直讀】。Arimo 的說明原文：「metrically compatible with Arial」「Updated in April 2026 and re-released under the OFL」。
6. **中文沒有查到任何字寬相容的替代字型。** fontconfig 對照表、LibreOffice 替代表、搜尋結果（ArchWiki 的字寬相容字型頁只涵蓋拉丁字型與一套日文字型）都沒有。【直讀】【搜尋摘要】[ArchWiki](https://wiki.archlinux.org/title/Metric-compatible_fonts)。【推論】中文字型的漢字與全形標點通常都是 1 em 寬（實測 Noto Sans TC、Noto Serif TC、LXGW WenKai TC、文泉驛正黑都是），所以純中文的一行字，換字型後寬度不變；會跑版的是混在裡面的英文、數字、半形標點（每套字型的拉丁字寬都不同），以及行高（每套字型的上伸下伸值不同）。這個推論沒有用 Microsoft 字型驗證過。
7. 搜尋摘要提到新細明體的拉丁字母是比例寬度。【搜尋摘要】（維基百科的中日韓字型列表）。這表示「新細明體換成 Noto Serif TC」時，英數部分的寬度一定會變。

### 3.6 台灣商用字型的伺服器授權

1. **華康（威鋒數位）：依使用方式個別簽約。** 官網的授權方案頁列有「伺服器專用字型授權」，對象是需要把字型安裝在伺服器上使用的客戶；官網說明不論有償無償、商用非商用，依字型的使用方式需另外簽訂授權合約並酌收費用。【搜尋摘要】[授權方案](https://www.dynacw.com.tw/business/business_licensing.aspx)、[使用授權範圍](https://www.dynacw.com.tw/business/licensing_usage.aspx)
2. 華康的字型使用授權書：「貴客戶不得將本軟體轉檔、重製後使用或搭載於任何伺服器(Server)上」；珍藏專業版的條款多一句例外，以字轉圖的方式用於網站設計不在此限。【搜尋摘要】[授權書](https://www.dynacw.com.tw/fileupload/annual_products/files/License_Font_Product.pdf)
3. **威鋒數位 2018 年對 Windows 內建字型的聲明。** 透過合法授權的微軟系統使用新細明體、標楷體，不論商用或非商用，威鋒不會主動收費；但把字型取出、內嵌到非微軟系統的第三方軟硬體屬於例外，列舉的例子是嵌入式裝置、電子遊戲、App、電子書，「以及安裝於提供 ASP（應用服務提供者）等網路服務伺服器」。【搜尋摘要】【第三方】[iThome](https://www.ithome.com.tw/news/125776)、[科技新報](https://technews.tw/2018/09/11/dynafont-authorization-statement/)、[T客邦](https://www.techbang.com/posts/61044-hua-kang-font-wei-feng-digital-issued-a-statement-explaining-the-windows-system-built-in-font-authorization-specification)
4. 【推論】上一點直接對應 Mortise 的情境：SaaS 就是聲明裡說的「提供網路服務的伺服器」。把新細明體或標楷體裝在 Mortise 的伺服器上，Microsoft 的條款不允許（3.4），字型著作權人威鋒數位也明講這種情況要另外確認。
5. **文鼎（iFontCloud，現名森澤文鼎雲字庫）：標準授權不含伺服器。** 授權條款：可以安裝字庫管理工具，「但不得安裝本字庫軟體於內部或外部網路之伺服器」；方案表把「字型使用於內部或外部網路伺服器」列為不在授權範圍內，標準版與進階版需洽談，教育版與學生版不予授權；把字型內嵌於軟體、App、遊戲同樣不在範圍內。【搜尋摘要】[授權範圍](https://ifontcloud.com/index/eula.jsp?lang=zh&country=TW)、[方案](https://ifontcloud.com/index/plan_detail.jsp?lang=zh&country=TW)
6. 文鼎另有獨立的授權類別：「伺服器／自動化印刷字體授權」，適用大量自動產製印刷品（報表、帳單、客製化 DM、雲端印刷服務）、字體裝在伺服器上批次產生 PDF 的情境；另有「嵌入式系統字型授權」「電子書嵌入字型授權」。【搜尋摘要】[伺服器授權說明](https://www.ifontcloud.com/index/newknowledge_detail.jsp?id=170)。搜尋摘要提到標準版與進階版授權書 v8.0 的適用日是 2026-04-30。
7. 文鼎的企業商用授權可用於印刷、網頁設計、影片字幕與產品手冊等 PDF 製作，企業內部安裝的電腦數量不限。【搜尋摘要】[文鼎授權頁](https://www.arphic.com.tw/business/)
8. justfont：官方部落格的授權常見問題說，內嵌字型屬特殊需求，需要直接洽談，不能直接把字型嵌入 App 或網頁（該文最後更新 2023 年 9 月）。沒有找到公開的伺服器或 SaaS 授權方案。【搜尋摘要】[justfont 部落格](https://blog.justfont.com/?p=15920)
9. Adobe Fonts：授權與伺服器安裝不相容，要另外向字型廠商或經銷商購買；不提供自行架設網頁字型；字型檔不得移動或複製到別的使用者或電腦；內嵌到 PDF 有授權，但字型必須做子集，只供檢視或列印既有內容。【搜尋摘要】[Adobe Fonts 授權常見問答](https://helpx.adobe.com/sg/fonts/web/font-licensing/font-licensing.html)
10. 以上三家台灣廠商的伺服器授權都沒有公開價格，都要洽談。【搜尋摘要】

### 3.7 從 PDF 內嵌的子集字型取出字形

1. **嵌入旗標的定義。** FreeType 標頭檔對 `fsType` 各旗標的說明：0x0000 可安裝嵌入（可內嵌，也可永久安裝到遠端系統）；0x0002 限制授權嵌入（未經著作權人許可不得修改、內嵌或交換）；0x0004 預覽與列印嵌入（可內嵌並暫時載入，文件必須以唯讀開啟）；0x0008 可編輯嵌入（可內嵌，只能暫時安裝，文件可以編輯並存檔）；0x0100 不得子集；0x0200 只能內嵌點陣。【直讀】[freetype.h](https://github.com/freetype/freetype/blob/master/include/freetype/freetype.h)
2. 同一段說明的附註：「While the `fsType` flags can indicate that a font may be embedded, a license with the font vendor may be separately required to use the font in this way.」【直讀】同上。也就是說旗標只是技術標記，真正的權利義務在授權合約裡。
3. 【推論】幾乎所有商用字型的內嵌許可，都是「為了讓收到文件的人能檢視、列印，有些還允許編輯該文件」。沒有一種旗標的意思是「收到文件的人可以把字型拿出來用在別的地方」。即使是最寬的「可安裝嵌入」，也要看字型自己的授權。
4. Microsoft 的條款把內嵌限制在旗標允許的範圍：Product Terms「may only embed fonts in content as permitted by the embedding restrictions in the fonts」；Core fonts EULA 禁止衍生作品，「other than subsetting when embedding them in documents」。【直讀】見 3.4 第 3、9 點
5. iText 的知識庫文章引用 Monotype 對 Arial 的 EULA：只能依字型內含的嵌入限制內嵌，其他用途一律禁止。【搜尋摘要】【第三方】[iText 知識庫](https://kb.itextpdf.com/itext/why-can-t-i-embed-a-font-due-to-licensing-restrict)
6. Adobe 的文件：內嵌在 PDF 裡的字型一定能用來檢視與列印；但要編輯文字，系統上必須安裝該字型，否則 Acrobat 會警告並改用替代字型。【搜尋摘要】[Acrobat Distiller 字型處理](https://helpx.adobe.com/acrobat/kb/font-handling-in-acrobat-distiller.html)。【推論】連 Adobe 自己的 PDF 編輯器都不拿內嵌字型來編輯新文字，這是業界對「內嵌字型只供顯示」的實際做法。
7. OFL 字型是例外：從 PDF 取出來的 OFL 字型仍然是 OFL，可以使用，但 SIL 不鼓勵，建議直接用原始字型。見 3.3 第 8 點。【直讀】
8. **台灣法律對字型的保護。** 學術論文與主管機關資料的搜尋摘要：字型在著作權法上分別以「字型繪畫」（美術著作）與字型檔（電腦程式著作）受保護；智慧財產局的函釋認為字型繪畫的保護範圍是「整組字群」，不及於個別文字；現行見解不保護字體的「通常使用」（用字型打字、輸出）。【搜尋摘要】[智慧財產局資料](https://www.tipo.gov.tw/wSite/public/Attachment/0/f1747879186862.pdf)、[臺大學位論文](https://tdr.lib.ntu.edu.tw/handle/123456789/693)、[justfont 字型版權講座紀錄](https://blog.justfont.com/2018/10/copyright-course-1/)
9. **沒有找到任何權威來源直接回答「從 PDF 取出內嵌的子集字型拿來比對或渲染是否合法」。** 搜尋到的都是技術教學（提醒要遵守字型授權）與論壇討論。【搜尋摘要】
10. 【推論】依風險由低到高分四級，供法務討論用：
    - **讀字型名稱與 PDF 字型字典裡的量度數字**（字寬陣列、上伸下伸值）。沒有複製字型程式，風險最低。這些數字本來就是 PDF 為了讓閱讀器排版而公開放在字典裡的（3.8 第 6 點）。
    - **在記憶體裡用內嵌字形的外框做比對**（例如拿來和候選的開放字型比字形相似度），不落地保存、不輸出。有重製字型程式的疑慮，但只是分析用途，不對外散布。需法務判斷。
    - **在同一份文件的輸出裡沿用原本的內嵌字型**（PDF 進、PDF 出，文字沒有改）。接近「文件本身被轉存」，但只要編輯了文字就超出「檢視與列印」的範圍；子集也沒有新字可用。需法務判斷。
    - **把內嵌字型取出來存檔、安裝到伺服器、拿去渲染別的文件、或放進 PPTX、DOCX、IDML**。等於未經授權重製並使用字型程式，而且子集字型缺字，技術上也不可行。不要做。

### 3.8 字型量度資料怎麼取得

1. **fontTools 是 MIT 授權。** 授權檔：「MIT License／Copyright (c) 2017 Just van Rossum」。【直讀】[LICENSE](https://github.com/fonttools/fonttools/blob/main/LICENSE)。PyPI：4.68.0 版，2026-10-27 上傳，授權欄位 MIT，需要 Python 3.11 以上。【直讀】[PyPI](https://pypi.org/project/fonttools/)
2. fontTools 程式庫裡附的測試用字型（Lobster、Noto、XITS、Iosevka）是 SIL OFL 1.1，列在另一個授權檔。【直讀】[LICENSE.external](https://github.com/fonttools/fonttools/blob/main/LICENSE.external)。用 pip 安裝的套件是否包含這些測試字型，沒有確認。
3. **各項量度在字型檔的哪裡**（以下都用 fontTools 4.68.0 實際讀到）：【實測】
   - em 方框的單位數：`head` 表的 `unitsPerEm`
   - x 字高、大寫字高：`OS/2` 表的 `sxHeight`、`sCapHeight`（`OS/2` 第 2 版以上才有；文泉驛正黑是第 1 版，讀不到）
   - 每個字的前進寬度：`cmap` 查字形名稱，再查 `hmtx`
   - 上伸與下伸有三組：`hhea` 的 `ascent`、`descent`、`lineGap`；`OS/2` 的 `sTypoAscender`、`sTypoDescender`、`sTypoLineGap`；`OS/2` 的 `usWinAscent`、`usWinDescent`
   - 嵌入旗標：`OS/2` 的 `fsType`
   - 著作權與授權字串：`name` 表的 0 號（著作權）、13 號（授權說明）、14 號（授權網址）
   - 可變字型的軸與具名實例：`fvar` 表
   - 中日韓字型的表意字基線：`BASE` 表（Noto Sans TC、Noto Serif TC、LXGW WenKai TC 都有，這次只確認存在，沒有解讀內容）
4. **三組上伸下伸值可能不一致，哪個程式用哪一組沒有查。** 實測 Arimo 與 Liberation Sans 的 `hhea` 完全相同（1854、-434、67），但 `OS/2` 的兩組不同（Arimo 的 typo 是 1854、-434、67，win 是 2136、797；Liberation Sans 的 typo 是 1491、-431、307，win 是 1854、434）。【實測】。【推論】「字寬相容」只保證橫向的寬度，行高可能因為程式採用的那一組數值不同而有差異。PowerPoint、Word、LibreOffice 各用哪一組，沒有查到，要實測。
5. HarfBuzz 是「Old MIT」授權；uharfbuzz（Python 綁定）是 Apache-2.0，PyPI 最新版 0.57.0（2026-10-23）；FreeType 是 FreeType License 與 GPLv2 二選一。【直讀】見總表 1.6 的來源。freetype-py 在 PyPI 的授權欄位是空的，沒有查證。
6. **原生 PDF 的字型字典本身就帶量度。** pdfminer.six 的原始碼從字型描述項讀 `FontName`、`Flags`、`Ascent`、`Descent`、`ItalicAngle`、`MissingWidth`、`Leading`、`FontBBox`，從字型字典讀 `Widths`；內嵌的字型程式在 `FontFile`、`FontFile2`。【直讀】[pdffont.py](https://github.com/pdfminer/pdfminer.six/blob/master/pdfminer/pdffont.py)。PDFium 也有讀上伸、下伸、字形寬度的函式（見 [pdf.md](pdf.md) 3.1 第 6 點）。PDF 規格書對這些欄位的正式定義，這次沒有讀到原文。
7. 【推論】對原生 PDF，原文件字型的寬度資料可以直接從 PDF 拿，不需要持有那套字型，也不需要取出內嵌的字型程式。這讓「原文是 Calibri，伺服器上只有 Carlito」的情況可以量化驗證：把 PDF 裡記錄的字寬和 Carlito 的字寬逐字比對。

### 3.9 Office 預設字型的變化

1. **Aptos 取代 Calibri 成為 Microsoft 365 的預設字型。** Calibri 從 Office 2007 起是預設字型，取代了 Word 的 Times New Roman 與 PowerPoint、Excel、Outlook 的 Arial；Aptos 原名 Bierstadt，設計者 Steve Matteson；Microsoft 在 2021 年提出五個候選。【搜尋摘要】【第三方】[Windows Central](https://www.windowscentral.com/software-apps/so-long-calbri-welcome-microsofts-new-default-font-for-its-office-apps-aptos)、[TechSpot](https://www.techspot.com/news/99410-goodbye-calibri-hello-aptos-new-default-font-microsoft.html)、[Daring Fireball](https://daringfireball.net/2023/07/aptos)
2. 切換時間的說法不一致：有的來源說 2023 年釋出，有的說預設值的切換從 2024 年 1 月開始、在幾個月內陸續推到 Word、Outlook、PowerPoint、Excel。【搜尋摘要】【第三方】[heise](https://heise.de/en/news/Microsoft-Office-Wechsel-der-Standard-Schrift-auf-Aptos-beginnt-9729575.html)。【推論】兩者不矛盾：2023 年 7 月宣布，之後分批推送。Microsoft 的官方公告我沒有讀到。
3. **新版 Office 佈景主題檔裡的字型設定。** 我讀了三個公開程式庫裡、由新版 Office 產生的 `theme1.xml`（字型配置名稱都是「Office」）：【直讀】（檔案是第三方程式庫裡的 Office 產出物，不是 Microsoft 的文件）
   - Word（[pandoc 的參考文件](https://github.com/jgm/pandoc/blob/main/data/docx/word/theme/theme1.xml)）：標題字型 Aptos Display，內文字型 Aptos；繁體中文（Hant）標題與內文都是「新細明體」；簡體中文是「等线 Light」與「等线」；日文是「游ゴシック Light」與「游明朝」
   - PowerPoint（[OfficeIMO 的資源檔](https://github.com/EvotecIT/OfficeIMO/blob/master/OfficeIMO.PowerPoint/Resources/theme1.xml)）：標題 Aptos Display，內文 Aptos；繁體中文都是「新細明體」
   - Excel（[一個 VBA 示範專案的活頁簿](https://github.com/DecimalTurn/VBA-Build-Demo/blob/main/src/ExcelWorkbook.xlsm/XMLsource/xl/theme/theme1.xml)）：標題 Aptos Display，內文 **Aptos Narrow**；繁體中文都是「新細明體」
4. 舊版（Calibri 時期）的佈景主題檔：標題 Calibri Light，內文 Calibri；繁體中文都是「新細明體」。【直讀】[docx4j 的範例檔](https://github.com/plutext/docx4j/blob/master/docx4j-samples-xlsx4j/src/main/java/org/xlsx4j/samples/theme1.xml)
5. 【推論】從佈景主題檔看，Office 換成 Aptos 只改了拉丁字型，繁體中文的佈景字型前後都是新細明體。但這是英文版預設佈景主題的內容；繁體中文版 Office 的預設範本有沒有另外指定字型（例如 PowerPoint 用微軟正黑體），我沒有查證。
6. Microsoft 另外提供 Aptos 的本機安裝包，給用不到 Office 字型服務的應用程式。【直讀】見 3.4 第 11 點。【推論】這表示 Aptos 平常是由 Office 的雲端字型服務下發的，沒裝新版 Office 的電腦上不一定有。
7. LibreOffice 的字型替代表沒有 Aptos。見 [render.md](render.md) 3.1 第 8 到 10 點。

### 3.10 實測

環境：這台查證用的 Linux 機器，Python 3.13，fontTools 4.68.0（裝在暫存的虛擬環境）。字型檔來源：Google Fonts 程式庫的 Noto Sans TC、Noto Serif TC、LXGW WenKai TC、Arimo、Tinos、Carlito；系統已安裝的 Liberation 2.1.5、Carlito、文泉驛正黑。沒有任何 Microsoft 字型。

1. **Noto Sans TC（Google Fonts 的可變字型檔，11.9 MB）：** `unitsPerEm` 1000；`fsType` 0；外框是 TrueType（有 `glyf` 表，沒有 CFF）；有 `fvar`、`BASE`、`vmtx`；字重軸 100 到 900，**預設值 100（Thin）**，具名實例九個（Thin 到 Black）；「中」「，」「「」的前進寬度都是 1000；基本區漢字（U+4E00 到 U+9FFF）對應到 15,383 個；名稱表 13 號是 OFL 1.1 的聲明。
2. **Noto Serif TC（16.9 MB）：** `unitsPerEm` 1000；`fsType` 0；TrueType 外框；可變字型；全形字寬 1000；基本區漢字 15,384 個。
3. **LXGW WenKai TC Regular（13.1 MB）：** `unitsPerEm` 1000；`fsType` 0；TrueType 外框；靜態字型；全形字寬 1000；基本區漢字 15,518 個。
4. **Arimo 對 Liberation Sans**（兩者都自稱相容 Arial）：共同的 2,307 個字元裡，前進寬度不同的有 29 個；可列印的 ASCII 95 個字元**全部相同**。不同的例子：U+0192（ƒ）一個是 571、一個是 1139。
5. **Tinos 對 Liberation Serif**（兩者都自稱相容 Times New Roman）：共同的 2,308 個字元裡不同的有 16 個（上標與下標數字，614 對 714）；ASCII 95 個全部相同。
6. 對照組，**Carlito 對 Liberation Sans**（兩者不相容）：ASCII 95 個全部不同。這證明上面的「全部相同」不是量測方法的假象。
7. 【推論】第 4、5 點說明「字寬相容」在基本拉丁字母上是精確的，但在擴充字元上不保證。文件裡如果有歐洲語言的特殊字母、上下標、符號，換字型後仍可能跑版。
8. Carlito Regular：`unitsPerEm` 2048；`fsType` 0；`sxHeight` 978；`sCapHeight` 1314。Google Fonts 版與系統安裝版讀到的數值相同。
9. 文泉驛正黑：`unitsPerEm` 1024；`OS/2` 第 1 版（沒有 x 字高欄位）；`fsType` 8；全形字寬 1024。
10. 量測用的兩支小程式沒有放進專案。

## 4. 建議

以下是依本產品授權限制做的建議。事實依據在第 3 節，這一節是我的判斷。

### 4.1 伺服器上裝什麼字型

**首選：只裝 SIL OFL 1.1 的字型。**

- 繁體中文：黑體類用 **Noto Sans TC**，明體與宋體類用 **Noto Serif TC**，楷體類用 **LXGW WenKai TC**。圓體類視需要加源泉圓體或 jf open 粉圓。
- 英文：**Liberation Sans、Liberation Serif、Liberation Mono、Liberation Sans Narrow、Carlito、Caladea、Gelasio**，分別頂替 Arial、Times New Roman、Courier New、Arial Narrow、Calibri、Cambria、Georgia。再加 Selawik 頂替 Segoe UI（不完全相容）。其他英文字型從 Google Fonts 挑。
- 理由：授權全部直讀過；OFL 明文允許裝在伺服器、內嵌到文件、和商業軟體一起散布，桌面版可以直接帶走同一批字型；Noto 的 TC 版在 Google Fonts 裡，Google Slides 認得；Google Fonts 的 Noto 是 TrueType 外框，ReportLab 吃得下（pdf.md 指出 ReportLab 不吃 CFF）。
- 要處理的坑：Google Fonts 的 Noto 是可變字型，預設實例是 Thin。上線前要用 fontTools 把需要的字重各切成一個靜態檔，並實測 LibreOffice 與 ReportLab 拿到的是不是對的字重。
- 桌面版安裝包要附每一套字型的著作權聲明與 OFL 授權文。

**備選：** 楷體如果要更接近標楷體的樣子，評估全字庫正楷體，但要先讀到條款原文。教育部標準楷書只在取得教育部書面同意後才考慮內嵌或散布。

**不要做：** 不要在伺服器上安裝任何 Microsoft 字型（含 Core fonts for the Web 與 Aptos）、華康、文鼎、Adobe Fonts 的字型；不要用 cwTeX、台北黑體、文泉驛（後兩者是授權沒讀到，不是確定有問題）。

### 4.2 輸出檔裡寫什麼字型名稱

這題要和渲染驗證（render.md）一起決定。兩種做法：

- **首選（檔案急救市場）：輸出檔寫原文件的字型名稱，伺服器用開放替代字型驗證。** 例如辨識出原文是 Calibri，PPTX 裡就寫 Calibri，使用者在自己的 Office 裡開啟時用的是他自己有授權的 Calibri，版面最接近原件；伺服器上用 Carlito 渲染來驗證。寫字型名稱不等於使用字型（3.4 第 5 點，這是類推，要請法務確認）。還原度報告要寫明「以字寬相容的替代字型驗證」。中文同理：寫「新細明體」「微軟正黑體」，伺服器用 Noto 驗證，報告要標示「中文字型無字寬相容的替代，英數與行高可能有出入」。
- **備選（靈感模板市場、Google Slides、給 Illustrator 的 PDF、IDML）：輸出檔直接用開放字型。** Google Slides 不認得的字型會被換掉；PDF 要內嵌字型，只能內嵌我們有權內嵌的字型。這幾種輸出一律用 Noto、Liberation、Carlito 這批字型，所見即所得，驗證結果也準。

**Aptos 是目前的缺口。** 新版 Office 的文件預設就是 Aptos，沒有可靠的開放替代，伺服器上不能裝。遇到 Aptos 的文件，輸出檔照寫 Aptos，驗證時用最接近的無襯線字型，並在報告裡標示這一項無法精確驗證。如果這類文件佔比高，再考慮向 Monotype 詢價，或改走 Microsoft Graph 轉檔那條路讓 Microsoft 自己的引擎來渲染（見 render.md）。

### 4.3 字型辨識怎麼做

**結論：沒有現成、授權乾淨、又能辨識中文字型的模型可以直接用。** 唯一的中日韓模型（YuzuMarker）權重來源有疑慮、準確率不到五成；Lens 是非商用授權；Storia 沒有授權；DeepFont 沒有官方開源。

- **首選：不追求「認出確切字型」，改成「歸到我們有的字型」。** 分三層：
  1. 原生 PDF 直接讀字型名稱，不需要辨識。這涵蓋檔案急救市場的大部分情況。
  2. 影像輸入時，先分大類（中文：黑、明、楷、圓、其他；英文：無襯線、襯線、等寬；再加粗細與斜體），再對應到 4.1 那份清單裡的字型。大類分類比幾千類的細分類容易得多。
  3. 需要在同類的幾套字型裡挑一套時，用「畫出來比對」：拿候選字型把 OCR 讀到的同一段字畫出來，比較行寬、字高比例與筆畫粗細，挑最接近的。這一步用 fontTools 與 HarfBuzz 算寬度，用 FreeType 畫圖，不需要訓練模型。
- 第 2 層的分類器**自己訓練**：用 4.1 清單裡的 OFL 字型加上其他 OFL 字型合成訓練影像（OFL 常見問答 1.26 支持這種用法，見 3.3 第 6 點），程式架構可以參考 YuzuMarker（MIT）與 Storia 的 README 描述的流程（Storia 的程式碼沒有授權，只能參考做法，不能複製程式碼）。
- 還原度報告裡，「確切字型」一律標為「推論」，並列出實際採用的替代字型。這符合產品「不宣稱還原原始檔」的承諾。
- **備選：** 英文字型可以評估 Azure Document Intelligence 的字型屬性附加功能（回傳相似字族、粗細、斜體、顏色），代價是文件要送到 Azure、要付費、中文支援未知。WhatFontIs API 與 Mixfont Lens API 也可以評估，但前者要求放連回它的連結，兩者的中文支援與價格都沒有查到。

### 4.4 Microsoft 字型與台灣商用字型

- **首選：伺服器完全不持有。** 桌面版用使用者電腦上已安裝的字型（使用者自己有授權），不隨安裝包附帶任何 Microsoft 或商用字型。
- **備選（需付費，有客戶需求再談）：** Microsoft 字型向 Monotype 詢問伺服器授權；華康詢問「伺服器專用字型授權」；文鼎詢問「伺服器／自動化印刷字體授權」。三者都沒有公開價格。新細明體與標楷體的著作權在威鋒數位，要一併問清楚該找誰。

### 4.5 PDF 內嵌的子集字型

- **首選：只讀字型名稱與字型字典裡的量度數字，不取出字型程式。** 這已經足夠做字型對應與字寬驗證（3.8 第 6、7 點）。
- 讀 `fsType` 與名稱表的授權字串當作輔助資訊記錄下來，但不要把「旗標允許內嵌」當成「可以取出來用」的依據。
- 「在記憶體裡用內嵌字形做比對」與「PDF 進 PDF 出時沿用原內嵌字型」兩件事，等法務給意見再決定要不要做。
- 絕對不做：把客戶 PDF 裡的字型存下來、累積成字型庫、裝到伺服器、或放進其他輸出檔。

### 4.6 量度工具

**首選：fontTools（MIT）讀量度，HarfBuzz 經 uharfbuzz（Apache-2.0）算排版後的行寬，FreeType（選 FreeType License，產品文件要註明）畫字形。** 三者授權都直讀過，都可以隨桌面版散布。

## 5. 查不到的事

1. YuzuMarker.FontDetection 的權重與資料集在 Hugging Face 上標示的授權（huggingface.co 讀不到）。
2. VCB-Studio 的 CJK 字型包裡有哪些字型、各是什麼授權。
3. AdobeVFR 資料集的授權條款。
4. Storia 與 gaborcselle 兩個模型放在 Hugging Face 的權重，各自標示的授權。
5. 有沒有商用的中文字型辨識 API（求字體網、識字體網、字由等是否提供、條件為何）。
6. WhatFontIs API 的價格、完整使用條款、是否支援中文字型；Mixfont Lens API 的價格與是否支援中日韓。
7. Azure Document Intelligence 字型屬性功能對中文字回傳什麼、是否加價、準確度如何。
8. MyFonts WhatTheFont、Adobe（Photoshop 的字型比對）有沒有對外的 API。
9. 北京大學 VFRWild-CHS、南開大學資料集的授權與是否仍可下載。
10. Microsoft 字型常見問答的逐句原文（learn.microsoft.com 讀不到，只有搜尋摘要）。
11. 向 Monotype 購買 Microsoft 字型伺服器授權的價格、涵蓋哪些字型、是否涵蓋 Linux 上的 SaaS 渲染；微軟正黑體、新細明體、標楷體是否買得到這種授權、該向誰買。
12. Aptos 字型檔內的授權字串原文（字型檔下載不到）；Aptos 能否合法安裝在不執行 Office 的伺服器上。
13. Core fonts for the Web 的字型清單（沒有直讀到）；這批舊版字型的字寬與現行版本是否一致；EULA 是否允許裝在 SaaS 伺服器上（條文沒有提到）。
14. Carlito 與 Calibri、Caladea 與 Cambria、Liberation 與 Arial、Gelasio 與 Georgia 的字寬是否真的逐字相同（沒有 Microsoft 字型可以比對）。
15. 微軟正黑體、新細明體、標楷體的實際量度（em 方框、上伸下伸、拉丁字寬），以及它們和 Noto Sans TC、Noto Serif TC、LXGW WenKai TC 的差距有多大。
16. PowerPoint、Word、Excel、LibreOffice、Google Slides 各用哪一組上伸下伸值（`hhea`、`OS/2` typo、`OS/2` win）來算行高。
17. 繁體中文版 Office 的預設範本是否另外指定了中文字型（例如 PowerPoint 是否預設微軟正黑體）；Office 2007 以前繁體中文版的預設字型。
18. Microsoft 官方對 Aptos 推送時程的公告原文。
19. Aptos Display、Aptos Narrow 與 Aptos 的關係，以及有沒有任何可用的替代字型。Akasia 只有第三方自稱，沒有驗證。
20. 全字庫正楷體、正宋體的授權條款原文，「政府資料開放授權條款」與 OFL 並列時怎麼適用，最新版本與字符數（官方網站讀不到）。
21. 教育部字型的「禁止改作」是否涵蓋內嵌時做子集、格式轉換、隨軟體散布；書面申請的實際流程。
22. 台北黑體的授權條款原文。
23. 文泉驛正黑、文鼎 PL 系列（Arphic Public License）的授權內容（搜尋額度用完，沒有查）。
24. 華康「伺服器專用字型授權」、文鼎「伺服器／自動化印刷字體授權」、justfont 伺服器授權的價格與條款全文；這些授權是否允許把字型內嵌進交付給終端客戶的 PPTX、PDF。
25. 台灣主管機關或法院對「從 PDF 取出內嵌字型」「以字型渲染結果訓練模型」有沒有任何見解。
26. PDF 規格書（ISO 32000）對字型描述項各欄位的正式定義（這次以 pdfminer.six 的原始碼代替）；OpenType 規格對 `fsType` 的正式敘述（這次以 FreeType 標頭檔代替）。
27. fontconfig 官方程式庫（gitlab.freedesktop.org）的對照表是否和我讀的 GitHub 鏡像一致。
28. Google Fonts 版 Noto Sans TC 這種可變字型，LibreOffice 與 ReportLab 能不能正確選到指定字重。沒有實測。
29. Noto 的名稱是 Google 的商標，把 Noto 字型切成靜態檔或做子集後沿用原名，有沒有商標上的限制。
30. Google Fonts 裡其餘 13 個繁體中文字族各自的授權檔內容（只確認它們位於 `ofl/` 目錄）。
31. PowerPoint、Word 的「在檔案內嵌字型」功能能不能用來把 OFL 字型帶進輸出檔，以及 Google Slides、Keynote、LibreOffice 是否認得這種內嵌字型。這次沒有查。

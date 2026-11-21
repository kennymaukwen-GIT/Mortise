# 查證紀錄：文字偵測與文字辨識（OCR）

- 查證日期：2026-11-20
- 適用產品：Mortise（榫）文件反編譯 SaaS，未來可能有桌面版
- 授權底線：所有元件必須可商用；排除 GPL、AGPL 元件（另有可購買商業授權者列為「需付費」）；排除非商用授權的模型權重
- 需求：繁體中文加英文混排、橫排為主（直排為未來需求）、每一行四角座標、每個字或每個詞的位置、候選字與分數、可在自家伺服器執行

## 0. 查證方式與限制

本次查證的工具狀況，會影響下面每一條結論的可信度，先講清楚。

| 來源 | 狀況 |
|---|---|
| 網路搜尋 | 可用 |
| github.com、raw.githubusercontent.com | 可直接讀取（LICENSE、README、原始碼、官方文件的 Markdown 原檔、Releases） |
| pypi.org | 可直接讀取（版本、上傳日期、授權欄位） |
| huggingface.co | **無法直接讀取**（被工作環境的網路規則擋下）。模型卡內容只能靠搜尋結果的摘要 |
| www.paddleocr.ai、paddlepaddle.github.io | **無法直接讀取**。改讀 GitHub 上同一份文件的 Markdown 原檔 |
| arxiv.org | **無法直接讀取**。論文數字只能靠搜尋摘要 |
| roboflow.com | **無法直接讀取** |

下文每條事實後面標註查證方式：

- **【直讀】** 直接讀到官方來源的原文（LICENSE、官方文件、原始碼、PyPI 中繼資料）
- **【搜尋摘要】** 搜尋工具轉述了官方頁面的內容，但我沒有親自打開該頁
- **【第三方】** 只有非官方來源

準確度數字幾乎都是**廠商自己公布、用自己內部資料集量的**，不是獨立評測。沒有找到任何一份針對繁體中文、橫跨這些引擎的獨立評測。

## 1. 總表

「可否商用」指用在收費的線上服務；「可否隨桌面版散布」指把程式與權重打包進安裝檔交給使用者。

### 1.1 傳統管線（先偵測文字行，再逐行辨識）

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **PaddleOCR 3.7.0（PP-OCRv6，另含 PP-OCRv5）** | 文字行偵測加辨識的主力模型來源 | Apache-2.0 | Apache-2.0（PP-OCRv5 模型卡由搜尋摘要確認；PP-OCRv6 模型卡未能直接打開，由 RapidOCR 的授權聲明間接佐證） | 可 | 可，需附 Apache-2.0 授權文與歸屬聲明 | 單一模型支援簡中、**繁中**、英、日加 46 種拉丁語系；輸出四點多邊形、整行文字、整行分數；開啟 `return_word_box` 後中日韓文字逐字給框、英數逐詞給框 | 不輸出候選字；分數只有整行一個平均值；字框是由 CTC 時間步位置推算的近似值；旋轉行的字框會變成水平矩形；準確度只有內部資料集數字；預設推論引擎是 PaddlePaddle 框架（另可選 Transformers 或 ONNX Runtime 外掛）；`paddlex[ocr-core]` 依賴 LGPL 的 python-bidi | 是（權重授權部分為間接） | [LICENSE](https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE)、[Releases](https://github.com/PaddlePaddle/PaddleOCR/releases)、[PP-OCRv6 文件](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PP-OCRv6/PP-OCRv6.en.md)、[OCR 管線文件](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.en.md) |
| **RapidOCR 3.10.0** | 用 ONNX Runtime 等引擎執行 PP-OCR 模型的輕量外殼 | Apache-2.0 | Apache-2.0（專案的 MODEL_LICENSES.md 明文聲明，版權歸百度與 PaddleOCR） | 可 | 可，需依 Apache-2.0 第 4 條附授權文、保留歸屬、標示已轉檔 | 預設 PP-OCRv6 small 加 ONNX Runtime；`return_word_box`、`return_single_char_box`；每個字或詞回傳（文字、分數、框）；內部保有逐字信心值 `char_confs`；語言集含 `chinese_cht`；相依套件全為寬鬆授權 | 一樣只取第一名、不輸出候選字；字框同為 CTC 推算的近似值；字典與字型檔不在其 Apache-2.0 聲明範圍內；10 月出現 v5.0.0 alpha 預發行版，介面可能變動 | 是 | [LICENSE](https://github.com/RapidAI/RapidOCR/blob/main/LICENSE)、[MODEL_LICENSES.md](https://github.com/RapidAI/RapidOCR/blob/main/python/MODEL_LICENSES.md)、[config.yaml](https://github.com/RapidAI/RapidOCR/blob/main/python/rapidocr/config.yaml)、[PyPI](https://pypi.org/project/rapidocr/) |
| **Tesseract 5.5.4** | 傳統 OCR 引擎，可當第二意見 | Apache-2.0 | Apache-2.0（tessdata_best） | 可 | 可 | 有 `chi_tra` 與 `chi_tra_vert`；hOCR 可輸出逐字座標（`hocr_char_boxes`）；可輸出每個字的替代候選與信心值（`lstm_choice_mode`）；輸出 hOCR、ALTO、PAGE、TSV、PDF | 沒有找到 chi_tra 的官方準確度；官方手冊註明 LSTM 引擎的逐字框是由整行結果推估、可能較不精確；行與詞的框是水平矩形（左、上、右、下）加基線；對照片輸入的表現沒有查到資料 | 是（繁中準確度未查到） | [README](https://github.com/tesseract-ocr/tesseract/blob/main/README.md)、[Releases](https://github.com/tesseract-ocr/tesseract/releases)、[tessdata_best](https://github.com/tesseract-ocr/tessdata_best)、[手冊](https://github.com/tesseract-ocr/tesseract/blob/main/doc/tesseract.1.asc) |
| **docTR 1.1.0** | 文件 OCR 函式庫 | Apache-2.0 | 隨專案 Apache-2.0（未見另立權重授權） | 可 | 可 | 頁、區塊、行、詞的巢狀結構，詞層級有座標與信心值；1.1.0 新增版面分析與表格結構 | **官方預訓練辨識模型的字集是法文（拉丁字母）**；字集清單有簡體中文，沒有繁體中文；沒有字元層級輸出 | 是 | [Releases](https://github.com/mindee/doctr/releases)、[vocabs.py](https://github.com/mindee/doctr/blob/main/doctr/datasets/vocabs.py)、[crnn/pytorch.py](https://github.com/mindee/doctr/blob/main/doctr/models/recognition/crnn/pytorch.py) |
| **EasyOCR 1.7.2** | 多語 OCR 函式庫 | Apache-2.0 | 偵測模型 CRAFT 的原始專案為 MIT；EasyOCR 自家辨識權重未見另立授權 | 可 | 可 | 支援 `ch_tra`；輸出四點框、文字、信心值 | 最後一版是 2024-09-24；繁中只有第一代模型（`chinese.pth`，來自 `pre-v1.1.6` 發布）；沒有字元層級與候選字 | 是 | [Releases](https://github.com/JaidedAI/EasyOCR/releases)、[config.py](https://github.com/JaidedAI/EasyOCR/blob/master/easyocr/config.py)、[CRAFT LICENSE](https://github.com/clovaai/CRAFT-pytorch/blob/master/LICENSE) |
| **MMOCR 1.0.1** | 研究用 OCR 工具箱 | Apache-2.0 | 未查 | 可（程式碼） | 未查 | 多種偵測與辨識演算法 | 最後一版是 2023-07-04；是否有可用的繁中預訓練模型未查到 | 部分 | [Releases](https://github.com/open-mmlab/mmocr/releases)、[PyPI](https://pypi.org/project/mmocr/) |
| **Surya 0.24.0（Surya 2.5 模型）** | 以單一視覺語言模型做 OCR、版面、表格 | Apache-2.0（0.20.0 起；0.17.1 以前是 GPL-3.0-or-later） | 修改版 AI Pubs Open RAIL-M：研究、個人、募資或營收低於 500 萬美元的新創免費；超過需向 Datalab 購買授權 | **有條件**：低於門檻免費，超過需付費 | **需付費或需法務確認**（授權全文未讀到） | 91 種語言；輸出區塊的 HTML、多邊形、信心值 | **現行版本只輸出區塊層級**，README 沒有行、詞、字層級輸出；689M 參數的視覺語言模型 | 是（權重授權全文未讀） | [README](https://github.com/datalab-to/surya/blob/master/README.md)、[LICENSE](https://github.com/datalab-to/surya/blob/master/LICENSE)、[PyPI](https://pypi.org/project/surya-ocr/) |

### 1.2 2025 至 2026 年新出現的文件解析模型（多為小型視覺語言模型）

這一類模型的共通點：輸入整頁或區塊影像，輸出 Markdown、HTML 或 JSON，座標多半只到**區塊層級**。沒有任何一個在官方文件中提供逐字位置或候選字。

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **PaddleOCR-VL-1.6** | 0.9B 文件解析模型 | Apache-2.0 | Apache-2.0（模型卡，搜尋摘要） | 可 | 可 | OmniDocBench v1.6 得分 96.33%；文字、公式、表格、圖表、印章；1.5 起有「文字行定位加辨識」 | 文字行定位的輸出格式未查到；無逐字位置與候選字 | 是（權重授權為搜尋摘要） | [Releases](https://github.com/PaddlePaddle/PaddleOCR/releases)、[1.6 文件](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PaddleOCR-VL/PaddleOCR-VL-1.6.en.md)、[模型卡](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6/blob/main/README.md) |
| **GLM-OCR** | 0.9B 文件解析模型 | Apache-2.0 | MIT | 可 | 可 | OmniDocBench v1.5 得分 94.62；版面分析用 PP-DocLayout-V3 | 語言清單未列；輸出粒度未明說 | 是 | [GitHub](https://github.com/zai-org/GLM-OCR) |
| **MinerU 4.2.3** | 完整的文件解析管線 | MinerU Open Source License（以 Apache-2.0 為底加附加條款） | 未單獨查到 | 可，但有門檻：月活躍使用者超過 1 億或月營收超過 2,000 萬美元需另購授權；對外提供線上服務必須揭露使用了 MinerU | 可（附加條款未限制再散布） | 區塊層級座標 | 線上服務有揭露義務；模型權重授權未單獨查到 | 是 | [LICENSE.md](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md)、[PyPI](https://pypi.org/project/mineru/) |
| **dots.mocr（原 dots.ocr-1.5）** | 3B 文件解析模型 | MIT | MIT 加「dots.mocr LICENSE AGREEMENT」 | 可（協議明文允許商用與 SaaS），但有禁止用途條款 | 可，同上 | 區塊層級框加類別加文字；展示範例含繁體中文 | 協議禁止「未經授權的出版品數位化或文件掃描」，與本產品用途相近，**需法務判讀**；3B 參數 | 是 | [GitHub](https://github.com/rednote-hilab/dots.ocr)、[協議原文](https://github.com/rednote-hilab/dots.ocr/blob/master/dots.ocr%20LICENSE%20AGREEMENT) |
| **DeepSeek-OCR / DeepSeek-OCR-2** | 文件轉 Markdown | MIT / Apache-2.0 | 同左（GitHub 頁面標示） | 可 | 可 | 帶定位標記的 Markdown 輸出 | 語言、粒度未列 | 是 | [DeepSeek-OCR](https://github.com/deepseek-ai/DeepSeek-OCR)、[DeepSeek-OCR-2](https://github.com/deepseek-ai/DeepSeek-OCR-2) |
| **HunyuanOCR 1.5** | 文件解析、文字定位、翻譯 | 騰訊混元社群授權 | 同左 | **不建議**：授權不適用於歐盟、英國、南韓；月活躍超過 1 億需另取授權 | **不可**全球散布 | 多任務 | 地域限制；禁止用輸出改進其他模型 | 是 | [GitHub](https://github.com/Tencent-Hunyuan/HunyuanOCR)、[LICENSE](https://github.com/Tencent-Hunyuan/HunyuanOCR/blob/main/LICENSE) |
| **olmOCR 0.4.0** | 7B 模型，PDF 轉 Markdown | Apache-2.0 | GitHub 頁面未單獨寫明 | 可（程式碼） | 權重授權未確認 | olmOCR-Bench 82.4 | 只輸出 Markdown，未提座標；7B 參數 | 部分 | [GitHub](https://github.com/allenai/olmocr) |
| **Chandra OCR 2** | Datalab 的文件解析模型 | Apache-2.0 | 修改版 OpenRAIL-M：募資或營收低於 200 萬美元免費；不得用於與其 API 競爭 | **需付費**或受限 | **需付費** | 90 多種語言；區塊層級框 | 有競爭限制條款 | 是 | [GitHub](https://github.com/datalab-to/chandra) |
| **MonkeyOCR** | 文件解析 | Apache-2.0 | v1 權重**僅限學術研究與非商業評估**；v2 橫幅標示 Apache-2.0（另一個程式庫，未查證） | v1 不可 | v1 不可 | 區塊層級 | README 自述尚未完整支援繁體中文 | 是（v2 未查） | [GitHub](https://github.com/Yuliang-Liu/MonkeyOCR) |
| **GOT-OCR2.0** | 端到端 OCR 模型 | Apache-2.0 | GitHub 頁面未寫明；訓練資料標示 CC BY-NC 4.0 | 未確認 | 未確認 | 可指定區域辨識 | 2024-09 發布；權重授權不明 | 部分 | [GitHub](https://github.com/Ucas-HaoranWei/GOT-OCR2.0) |
| **OpenOCR（SVTRv2、OpenDoc-0.1B）** | 復旦的偵測加辨識系統 | Apache-2.0 | 未單獨查到 | 可（程式碼） | 未確認 | 中文加英文；可匯出 ONNX；OpenDoc-0.1B 在 OmniDocBench v1.5 得 90.57% | 沒有提到繁體中文；輸出格式未說明 | 部分 | [GitHub](https://github.com/Topdu/OpenOCR) |
| **LightOnOCR-2 / LightOnOCR-3** | 約 1B 的端到端 OCR | — | Apache-2.0（LightOnOCR-2 為搜尋摘要；LightOnOCR-3 只有第三方來源） | 未確認 | 未確認 | 轉 Markdown，可選框 | 中文支援未查到 | 否 | [LightOn 部落格](https://huggingface.co/blog/lightonai/lightonocr-2) |
| **Granite-Docling-258M** | IBM 的小型文件轉換模型 | — | Apache-2.0（只有次級來源） | 未確認 | 未確認 | 輸出 DocTags | 第三方來源稱主要語言是英文 | 否 | [IBM 公告](https://ibm.com/new/announcements/granite-docling-end-to-end-document-conversion) |
| **Xiaomi-OCR-0** | 0.8B 文件解析模型 | — | Apache-2.0（只有第三方來源） | 未確認 | 未確認 | 文件解析、資訊抽取 | 發布日期來源互相矛盾（2026-09-30 與 2026-10-30）；分數為自報 | 否 | [報導](https://pasqualepillitteri.it/en/news/28641/xiaomi-ocr-0-open-source-ocr-model-documents) |

## 2. 需求對照

把「能不能滿足 Mortise 的五項需求」攤開來看。這張表是下面建議的依據。

| 需求 | PP-OCRv6（經 PaddleOCR 或 RapidOCR） | Tesseract 5.5 | Surya 2.5 | 小型視覺語言模型類 |
|---|---|---|---|---|
| 繁體中文加英文 | 官方明列支援，有繁中分項數字 | 有 `chi_tra`，無官方數字 | 列 `zh`，未區分繁簡 | 多數未明列 |
| 每行四角座標 | 有（`dt_polys`、`rec_polys`，四點） | 只有水平矩形加基線，不是四角 | 無，只有區塊 | 多數只有區塊 |
| 逐字或逐詞位置 | 有，但是推算值 | 有，官方手冊註明是推估值、可能較不精確 | 無 | 無 |
| 候選字與分數 | **內建沒有**。模型輸出是完整的機率矩陣，要自己寫解碼才拿得到 | **內建有**（`lstm_choice_mode`） | 無 | 無 |
| 自家伺服器執行 | 可，官方有 CPU 速度數字 | 可 | 可，模型 689M 參數 | 可，模型 0.26B 至 7B 參數，運算成本較高（未實測） |
| 授權符合底線 | 符合 | 符合 | 權重超過門檻需付費 | 視個別模型 |

## 3. 逐條事實與來源

### 3.1 PaddleOCR 與 PP-OCR 模型

1. **PaddleOCR 程式碼授權是 Apache-2.0。**【直讀】LICENSE 檔、README 的「This project is released under the Apache 2.0 license」、pyproject.toml 的 `license = {text = "Apache License 2.0"}` 三處一致。
   來源：<https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE>、<https://github.com/PaddlePaddle/PaddleOCR/blob/main/README.md>、<https://github.com/PaddlePaddle/PaddleOCR/blob/main/pyproject.toml>

2. **最新版是 3.7.0，2026-06-11 發布，內容是 PP-OCRv6。**【直讀】PyPI 上傳時間 2026-06-11T12:09:51，之後沒有更新的版本。先前版本：3.6.0（2026-05-28，PaddleOCR-VL-1.6）、3.5.0（2026-04-21）、3.4.0（2026-01-29，PaddleOCR-VL-1.5）。
   來源：<https://pypi.org/project/paddleocr/>、<https://github.com/PaddlePaddle/PaddleOCR/releases>

3. **PP-OCRv6 分三級：tiny（150 萬參數）、small（770 萬）、medium（3,450 萬）。**【直讀】辨識模型檔案大小分別是 4.4 MB、20.4 MB、73.3 MB。對照 PP-OCRv5_server_rec 為 81 MB、PP-OCRv5_mobile_rec 為 16 MB。
   來源：<https://github.com/PaddlePaddle/PaddleOCR/blob/main/README.md>、<https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/module_usage/text_recognition.en.md>

4. **PP-OCRv6 的 medium 與 small 用單一模型支援 50 種語言，官方明列包含繁體中文。**【直讀】原文：「the medium/small tiers support 50 languages with a single unified model, including Simplified Chinese, Traditional Chinese, English, Japanese, and 46 Latin-script languages (tiny supports 49, excluding Japanese)」。這表示繁中加英文混排不需要切換模型。
   來源：<https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PP-OCRv6/PP-OCRv6.en.md>

5. **PP-OCRv6 的準確度（廠商自報，內部多場景資料集，未公開）。**【直讀】

   文字偵測 Hmean（%）：

   | 模型 | 平均 | 繁體中文 | 印刷中文 | 印刷英文 | 旋轉 |
   |---|---|---|---|---|---|
   | PP-OCRv6_medium | 86.2 | 86.3 | 95.1 | 93.7 | 93.8 |
   | PP-OCRv6_small | 84.1 | 85.7 | 94.2 | 93.6 | 93.7 |
   | PP-OCRv6_tiny | 80.6 | 83.7 | 93.1 | 92.3 | 91.0 |
   | PP-OCRv5_server | 81.6 | 81.5 | 94.5 | 91.7 | 80.0 |
   | PP-OCRv5_mobile | 75.2 | 82.3 | 90.5 | 91.0 | 64.7 |

   文字辨識準確率（%）：

   | 模型 | 加權平均 | 繁體中文 | 印刷中文 | 印刷英文 | 易混淆字 |
   |---|---|---|---|---|---|
   | PP-OCRv6_medium | 83.2 | 78.6 | 91.5 | 94.1 | 64.9 |
   | PP-OCRv6_small | 81.3 | 77.0 | 90.5 | 93.3 | 64.1 |
   | PP-OCRv6_tiny | 73.5 | 65.0 | 86.7 | 88.4 | 52.3 |
   | PP-OCRv5_server | 78.1 | 74.7 | 90.1 | 85.1 | 59.4 |
   | PP-OCRv5_mobile | 73.7 | 72.0 | 86.0 | 86.0 | 55.7 |

   同一張表也列了通用視覺語言模型在同一資料集的辨識成績：Qwen3-VL-235B 繁中 76.4、Gemini-3.1-Pro 繁中 69.5、GPT-5.5 繁中 57.5。文件並聲稱視覺語言模型會依語言先驗「幻覺式修正」文字，PP-OCRv6 則照實重現畫面內容。這是廠商說法。
   來源：<https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PP-OCRv6/PP-OCRv6.en.md>

6. **PP-OCRv5 官方明列支援繁體中文、直排文字、手寫、拼音、日文。**【直讀】PP-OCRv5_server_rec 分項：繁體中文 0.7472、直排文字 0.9314、印刷中文 0.9013、印刷英文 0.8679、加權平均 0.8401。PP-OCRv6 的文件**沒有**直排文字這個分項，也沒有提到直排。
   來源：<https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PP-OCRv5/PP-OCRv5.en.md>

7. **來源互相矛盾：PP-OCRv5_server_rec 的繁體中文準確率有兩個差很多的官方數字。**【直讀】OCR 管線文件的「PP-OCRv5 Multi-Scenario Models」表寫繁中平均 93.29%（中文 86.38、英文 64.70、日文 60.35；mobile 版繁中 83.55）；PP-OCRv5 與 PP-OCRv6 的演算法文件則寫 74.7% 上下。兩份文件用的評測集不同，官方自己也註明「As the evaluation sets differ, the metrics are not directly comparable」。**結論：這些數字都不能直接拿來當 Mortise 的預期準確度，必須自建繁中測試集實測。**
   來源：<https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.en.md>

8. **速度（廠商自報，200 張圖，含讀檔與前後處理，秒每張）。**【直讀】

   | 硬體與後端 | medium | small | tiny |
   |---|---|---|---|
   | NVIDIA A100，PaddlePaddle | 0.29 | 0.25 | 0.13 |
   | Intel Xeon 8350C，OpenVINO | 1.40 | 0.59 | 0.20 |
   | Intel Xeon 8350C，ONNX Runtime | 3.31 | 0.61 | 0.22 |
   | Apple M4，ONNX Runtime | 5.55 | 1.29 | 0.35 |

   來源：<https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PP-OCRv6/PP-OCRv6.en.md>

9. **OCR 管線的輸出欄位。**【直讀】`dt_polys`（文字偵測多邊形，每個是四個頂點、形狀 (4, 2)）、`dt_scores`（偵測框信心值）、`rec_texts`（整行文字）、`rec_scores`（整行信心值）、`rec_polys`（過濾後的多邊形）、`rec_boxes`（水平矩形 [x_min, y_min, x_max, y_max]）、`textline_orientation_angles`。
   來源：<https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.en.md>

10. **逐字與逐詞位置：`return_word_box=True` 時多出 `text_word`、`text_word_region`、`text_word_boxes` 三個欄位。**【直讀原始碼】做法是把辨識結果的每個字對應回 CTC 的時間步欄位，再換算成框（函式 `cal_ocr_word_box`）。字元分三類：`cn`（Unicode U+4E00 到 U+9FFF）、`en&num`、`symbol`；中日韓文字逐字成組，英數連續成詞。這是**推算**出來的位置，不是模型直接預測的字框。
    來源：<https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/inference/pipelines/ocr/pipeline.py>、<https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/inference/models/text_recognition/processors.py>

11. **信心值只有整行一個數字，沒有候選字。**【直讀原始碼】解碼時 `preds_idx = preds.argmax(axis=-1)`、`preds_prob = preds.max(axis=-1)`，每個時間步只取第一名；整行分數是保留下來的時間步機率的平均（`np.mean(conf_list)`）。輸出不含逐字機率，也不含第二、第三名。
    來源：<https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/inference/models/text_recognition/processors.py>

12. **模型本身輸出的是完整機率矩陣，候選字可以自己解出來。**【直讀原始碼，後半句是推論】CTC 輸出層在推論時套用 softmax（`if not self.training: predicts = F.softmax(predicts, axis=2)`）；RapidOCR 的引擎基底類別註解寫明辨識模型輸出形狀是 (batch, time, vocab)，之後才被縮減成第一名。也就是說，自己直接呼叫模型、對每個時間步取前幾名，就能得到候選字與機率。**這需要自己寫解碼程式，兩個函式庫都沒有現成功能。**
    來源：<https://github.com/PaddlePaddle/PaddleOCR/blob/main/ppocr/modeling/heads/rec_ctc_head.py>、<https://github.com/RapidAI/RapidOCR/blob/main/python/rapidocr/inference_engine/base.py>

13. **已知問題：旋轉文字行的字框會變成水平矩形。**【直讀】2025-11-21 的討論串回報 `text_word_boxes` 是在文字行被拉正之後算的，沒有轉回原圖座標，維護者未回覆。對 Mortise 的照片輸入（常有傾斜）有直接影響，需要自己保留並反算變換矩陣。
    來源：<https://github.com/PaddlePaddle/PaddleOCR/discussions/17150>

14. **已知問題：英文分詞只認 ASCII。**【直讀】PaddleX 議題 #5188（2026-08-03 開立，至今未關閉；PaddleOCR 3.7.0、PaddleX 3.7.2）回報帶變音符號的字會被切成碎片，例如「Köln」變成「K」「ö」「ln」。對純繁中加英文影響小，對歐語文件有影響。
    來源：<https://github.com/PaddlePaddle/PaddleX/issues/5188>

15. **權重授權：PP-OCRv5_server_rec 的 Hugging Face 模型卡中繼資料寫 `license: apache-2.0`。**【搜尋摘要】搜尋工具轉述了官方模型卡 README 的中繼資料；我沒有直接打開。
    來源：<https://huggingface.co/PaddlePaddle/PP-OCRv5_server_rec/blob/main/README.md>

16. **權重授權：PP-OCRv6 的官方模型卡，我沒能直接確認。**【間接】RapidOCR 的 MODEL_LICENSES.md 寫「Official PaddlePaddle model cards identify the PP-OCRv6 model weights and ONNX representations as Apache-2.0」並附上官方 ONNX 模型卡連結；多個第三方鏡像也標 Apache-2.0。PaddleOCR 程式庫本身沒有另外為權重立一份授權。**上線前應有人親自打開官方模型卡確認。**
    來源：<https://github.com/RapidAI/RapidOCR/blob/main/python/MODEL_LICENSES.md>、<https://huggingface.co/PaddlePaddle/PP-OCRv6_small_rec_onnx>、<https://huggingface.co/collections/PaddlePaddle/pp-ocrv6>

17. **相依套件沒有 GPL 或 AGPL，但有一個 LGPL。**【直讀】`paddleocr` 依賴 `paddlex[ocr-core]`，後者包含 imagesize、opencv-contrib-python、pyclipper、pypdfium2、python-bidi、shapely。PyMuPDF（AGPL）不在清單內，PDF 讀取用的是 pypdfium2。python-bidi 在 PyPI 的分類是 LGPL，不在本產品的排除清單內，但桌面版打包時要注意 LGPL 的替換條款。
    來源：<https://github.com/PaddlePaddle/PaddleOCR/blob/main/pyproject.toml>、<https://github.com/PaddlePaddle/PaddleX/blob/develop/setup.py>、<https://pypi.org/project/python-bidi/>

18. **官方有瀏覽器端推論套件 PaddleOCR.js。**【直讀】3.5.0（2026-04-21）發布，可在瀏覽器直接跑 PP-OCRv5。對未來的桌面版或前端預覽可能有用。
    來源：<https://github.com/PaddlePaddle/PaddleOCR/releases>

### 3.2 RapidOCR

19. **程式碼授權 Apache-2.0；最新穩定版 3.10.0，2026-10-08 發布。**【直讀】PyPI `license_expression: Apache-2.0`，上傳時間 2026-10-08T15:07:45。GitHub 上另有 v5.0.0a2 到 a5 的預發行標籤（2026-10-13），代表下一個大版本正在開發，介面可能變動。
    來源：<https://github.com/RapidAI/RapidOCR/blob/main/LICENSE>、<https://pypi.org/project/rapidocr/>、<https://github.com/RapidAI/RapidOCR/releases>

20. **模型授權有明文聲明。**【直讀】MODEL_LICENSES.md：「All models registered in `rapidocr/default_models.yaml` are licensed under the Apache License, Version 2.0 (`Apache-2.0`), without exception.」上游權重版權屬百度與 PaddleOCR 權利人，RapidOCR 不主張所有權；轉檔後的 ONNX、MNN、PyTorch 版本沿用同一授權。文件也列出再散布時要做的四件事（附授權文、標示修改、保留歸屬、保留 NOTICE），並給了一段可直接使用的轉檔聲明文字。
    **例外**：字典檔（`dict_url`）與字型檔（`fonts`）「are not covered by the Apache-2.0 declaration」，各依其自身條款。
    來源：<https://github.com/RapidAI/RapidOCR/blob/main/python/MODEL_LICENSES.md>

21. **預設設定是 PP-OCRv6 small 的偵測與辨識模型，跑在 ONNX Runtime 上。**【直讀】v3.9.0（2026-06-23）起成為預設。可選引擎：onnxruntime、openvino、paddle、torch、tensorrt、mnn。
    來源：<https://github.com/RapidAI/RapidOCR/blob/main/python/rapidocr/config.yaml>、<https://github.com/RapidAI/RapidOCR/releases>

22. **PP-OCRv6 的語言集包含 `chinese_cht`，別名 `zh_tw`、`zh-tw`，對應到同一個多語模型。**【直讀】
    來源：<https://github.com/RapidAI/RapidOCR/blob/main/python/rapidocr/default_models.yaml>

23. **逐字與逐詞輸出。**【直讀原始碼】`return_word_box` 開啟後，`word_results` 每個元素是（文字、分數、框）。中文逐字給框；整行都是英數時逐詞給框，設 `return_single_char_box=True` 則一律逐字；中英混排的行，英文也是逐字給框。框的計算方式是把每個字的中心定在它對應欄位的中間，再用平均字寬撐開，最後修掉相鄰框的重疊。**同樣是推算值。** 算完之後會呼叫 `reverse_rotate_crop_image` 把字框轉回原圖座標，並依偵測框的長寬比（高除以寬大於等於 1.5）判斷為直排；這一點比第 13 條回報的 PaddleX 行為完整。
    來源：<https://github.com/RapidAI/RapidOCR/blob/main/python/rapidocr/cal_rec_boxes/main.py>、<https://github.com/RapidAI/RapidOCR/blob/main/python/rapidocr/utils/output.py>

24. **有逐字信心值，沒有候選字。**【直讀原始碼】`WordInfo` 有 `char_confs` 欄位，存每個字的機率。但取得機率的方式是 `infer_argmax`，回傳 `preds.argmax(axis=-1), preds.max(axis=-1)`，只有第一名。
    來源：<https://github.com/RapidAI/RapidOCR/blob/main/python/rapidocr/ch_ppocr_rec/utils.py>、<https://github.com/RapidAI/RapidOCR/blob/main/python/rapidocr/ch_ppocr_rec/typings.py>、<https://github.com/RapidAI/RapidOCR/blob/main/python/rapidocr/inference_engine/base.py>

25. **相依套件都是寬鬆授權。**【直讀】requirements.txt：pyclipper、opencv-python-headless、numpy、six、Shapely、PyYAML、Pillow、tqdm、omegaconf、requests、colorlog。onnxruntime 在 PyPI 標示 MIT。python-bidi 只在選用的 `rtl` 附加套件中。
    來源：<https://github.com/RapidAI/RapidOCR/blob/main/python/requirements.txt>、<https://pypi.org/project/onnxruntime/>

### 3.3 docTR

26. **授權 Apache-2.0；最新版 1.1.0，2026-08-21 發布。**【直讀】1.0.0（2025-07-09）起移除 TensorFlow，只剩 PyTorch。1.1.0 新增版面分析、表格結構辨識、閱讀順序匯出，並要求 Python 3.11 以上。另有姊妹專案 OnnxTR 0.9.0（2026-08-24，Apache-2.0）。
    來源：<https://pypi.org/project/python-doctr/>、<https://github.com/mindee/doctr/releases>、<https://pypi.org/project/onnxtr/>

27. **官方預訓練辨識模型不支援中文。**【直讀原始碼】CRNN、PARSeq、ViTSTR、VIPTR 各模型的預設設定都是 `"vocab": VOCABS["french"]`。字集清單裡有 `simplified_chinese`、`japanese`、`korean`，**沒有** `traditional_chinese`；`multilingual` 字集只合併拉丁、西里爾、希臘字母。要用在繁中必須自己訓練。
    來源：<https://github.com/mindee/doctr/blob/main/doctr/datasets/vocabs.py>、<https://github.com/mindee/doctr/blob/main/doctr/models/recognition/crnn/pytorch.py>、<https://github.com/mindee/doctr/blob/main/doctr/models/recognition/parseq/pytorch.py>

28. **輸出到詞層級。**【直讀】頁、區塊、行、詞的巢狀結構，詞有信心值與座標。官方 README 與發布說明都沒有提字元層級輸出。
    來源：<https://github.com/mindee/doctr/blob/main/README.md>、<https://github.com/mindee/doctr/releases/tag/v1.1.0>

### 3.4 EasyOCR

29. **授權 Apache-2.0；最後一版 1.7.2，2024-09-24 發布，之後兩年多沒有新版。**【直讀】
    來源：<https://github.com/JaidedAI/EasyOCR/blob/master/LICENSE>、<https://github.com/JaidedAI/EasyOCR/releases>、<https://pypi.org/project/easyocr/>

30. **支援繁體中文，但只有第一代模型。**【直讀原始碼】`ch_tra` 對應 `zh_tra_g1`，檔名 `chinese.pth`，來自 `pre-v1.1.6` 這個發布。簡體中文有第二代（`zh_sim_g2`，v1.3），繁體沒有。
    來源：<https://github.com/JaidedAI/EasyOCR/blob/master/easyocr/config.py>

31. **輸出是四點框、文字、信心值，以偵測到的文字區域為單位。**【直讀】README 沒有字元層級或候選字的選項。
    來源：<https://github.com/JaidedAI/EasyOCR/blob/master/README.md>

32. **偵測模型 CRAFT 的原始專案是 MIT 授權（NAVER Corp.）。**【直讀】
    來源：<https://github.com/clovaai/CRAFT-pytorch/blob/master/LICENSE>

### 3.5 Tesseract

33. **授權 Apache-2.0；最新版 5.5.4，2026-10-28 發布，修了 17 個安全通報。**【直讀】前一版 5.5.3 修了 2 個安全通報（發布頁顯示 7 月 24 日）。相依的 Leptonica 是 BSD 2-clause。
    來源：<https://github.com/tesseract-ocr/tesseract/blob/main/README.md>、<https://github.com/tesseract-ocr/tesseract/releases>

34. **訓練資料 tessdata_best 是 Apache-2.0，包含 `chi_tra` 與 `chi_tra_vert`（直排）。**【直讀】README：「All data in the repository are licensed under the Apache-2.0 License」。
    來源：<https://github.com/tesseract-ocr/tessdata_best>

35. **可輸出逐字座標與替代候選字。**【直讀】手冊列有 `hocr_char_boxes`（在 hOCR 加入每個字的座標）與 `lstm_choice_mode`（1 是每個時間步的替代字，2 是從 CTC 抽出的替代字），hOCR 中每個候選帶 `x_confs` 信心值。程式介面有 `ChoiceIterator` 與 `GetBestLSTMSymbolChoices()`。
    來源：<https://github.com/tesseract-ocr/tesseract/blob/main/doc/tesseract.1.asc>、<https://github.com/tesseract-ocr/tessdoc/blob/main/APIExample.md>、<https://github.com/tesseract-ocr/tesseract/blob/main/src/api/hocrrenderer.cpp>

36. **官方手冊自己註明 LSTM 引擎的逐字框是推估值。**【直讀】手冊在 `hocr_char_boxes` 條目下寫：「character-level bounding boxes may be less precise when using the LSTM engine, because LSTM operates at the line level and character positions are approximated from the line result」。hOCR 的行與詞框格式是 `bbox 左 上 右 下`（水平矩形），文字行另附基線係數。
    另有社群說法【搜尋摘要，未直接打開】：一個 GitHub 提案（PR #3787）稱 LSTM 模型的字框「accuracy ... is low」；郵件論壇上有維護者建議需要準確字框時改用舊引擎。
    來源：<https://github.com/tesseract-ocr/tesseract/blob/main/doc/tesseract.1.asc>、<https://github.com/tesseract-ocr/tesseract/blob/main/src/api/hocrrenderer.cpp>、<https://www.github.com/tesseract-ocr/tesseract/pull/3787>、<https://groups.google.com/g/tesseract-ocr/c/_zBL8fXqBKw/m/5gj0LG4cAQAJ>

### 3.6 Surya

37. **程式碼授權在 2026 年從 GPL 改為 Apache-2.0。**【直讀】PyPI 中繼資料：0.17.1（2026-01-30）是 `GPL-3.0-or-later`；0.20.0（2026-05-27，Surya 2）起是 `Apache-2.0`。目前 LICENSE 檔是 Apache-2.0。最新版 0.24.0（2026-11-13）。
    來源：<https://pypi.org/project/surya-ocr/>、<https://github.com/datalab-to/surya/blob/master/LICENSE>

38. **權重授權不是寬鬆授權。**【直讀】README：「The model weights use a modified AI Pubs Open Rail-M license (free for research, personal use, and startups under $5M funding/revenue). For broader commercial licensing of the model weights, visit our pricing page」。授權全文我沒有讀到，是否允許再散布未確認。搜尋結果中有舊版 README 寫 200 萬美元門檻與 GPL，那是舊資料。
    來源：<https://github.com/datalab-to/surya/blob/master/README.md>

39. **現行的 Surya 2.5 只輸出區塊層級。**【直讀】Surya 2.5 是 689M 參數的視覺語言模型。`surya_ocr` 每個區塊輸出 `label`、`html`、`polygon`、`bbox`、`confidence`（整個區塊的平均每 token 機率）。README 沒有文字行偵測指令，也沒有行、詞、字層級的輸出。**不符合 Mortise 的每行四角座標與逐字位置需求。**
    來源：<https://github.com/datalab-to/surya/blob/master/README.md>

40. **廠商自報成績。**【直讀】OmniParseBench 85.9、olmOCR-bench 83.7、91 種語言內部評測 74.9%、單張 H100 每秒 4.52 頁。語言清單只列 `zh`，沒有區分繁簡。
    來源：<https://github.com/datalab-to/surya/blob/master/README.md>

### 3.7 MMOCR

41. **授權 Apache-2.0；最後一版 1.0.1，2023-07-04 發布，之後三年多沒有新版。**【直讀】依賴 MMEngine、MMCV、MMDetection。README 沒有提到中文預訓練模型。
    來源：<https://pypi.org/project/mmocr/>、<https://github.com/open-mmlab/mmocr/releases>、<https://github.com/open-mmlab/mmocr/blob/main/README.md>

### 3.8 2025 至 2026 年的新模型

42. **PaddleOCR-VL-1.6：0.9B 參數，2026-05-28 隨 PaddleOCR 3.6.0 發布，OmniDocBench v1.6 得 96.33%。**【直讀】架構與 1.5 相同。1.5（2026-01-29）新增「text spotting (text-line localization and recognition)」、不規則形狀框、印章辨識。文字行定位輸出的是四邊形還是多邊形、座標格式為何，文件沒寫。模型卡授權 `apache-2.0`【搜尋摘要】。
    來源：<https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PaddleOCR-VL/PaddleOCR-VL-1.6.en.md>、<https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/algorithm/PaddleOCR-VL/PaddleOCR-VL-1.5.en.md>、<https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6/blob/main/README.md>

43. **GLM-OCR：程式碼 Apache-2.0，模型 MIT，0.9B 參數。**【直讀】原文：「The Code of this repo is under Apache License 2.0」「The GLM-OCR model is released under the MIT License」。OmniDocBench v1.5 得 94.62。版面分析階段用的是 PaddleOCR 的 PP-DocLayout-V3。
    來源：<https://github.com/zai-org/GLM-OCR>

44. **MinerU 現行授權是自訂授權，不是 AGPL。**【直讀】現行授權是「MinerU Open Source License」，以 Apache-2.0 為底加三條：月活躍使用者超過 1 億或月營收超過 2,000 萬美元需另取授權；以 MinerU 對第三方提供線上服務時，必須在產品介面或公開文件中揭露；違反則授權自動終止。最新版 4.2.3（2026-11-16）。
    來源：<https://github.com/opendatalab/MinerU/blob/master/LICENSE.md>、<https://pypi.org/project/mineru/>

45. **dots.mocr（2026-03-19 由 dots.ocr-1.5 改名）：MIT 加一份附加協議。**【直讀】協議第 2.1 條明文允許商用、SaaS、併入專有產品。但第 3.3(c) 條：「Licensees shall not use the tool for unauthorized digitization of publications/document scanning or bulk scraping of content」，違反即自動終止授權。Mortise 的使用者可能上傳受著作權保護的文件，這一條需要法務判讀。輸出是區塊層級的框、類別與文字。
    來源：<https://github.com/rednote-hilab/dots.ocr>、<https://github.com/rednote-hilab/dots.ocr/blob/master/dots.ocr%20LICENSE%20AGREEMENT>

46. **DeepSeek-OCR：MIT，2025-10-20 發布。DeepSeek-OCR-2：Apache-2.0。**【直讀 GitHub 頁面的授權標示】
    來源：<https://github.com/deepseek-ai/DeepSeek-OCR>、<https://github.com/deepseek-ai/DeepSeek-OCR-2>

47. **HunyuanOCR 1.5（2026-07-07）：授權有地域限制。**【直讀】Tencent Hunyuan Community License Agreement 把適用地域定義為「the worldwide territory, excluding the territory of the European Union, United Kingdom and South Korea」；散布只能在該地域內；月活躍超過 1 億需向騰訊申請；禁止用其輸出改進其他模型。對全球性的線上服務與桌面版不適用。
    來源：<https://github.com/Tencent-Hunyuan/HunyuanOCR/blob/main/LICENSE>

48. **olmOCR：程式碼 Apache-2.0，最新版 0.4.0（2025-10-21），模型 olmOCR-2-7B-1025，olmOCR-Bench 得 82.4±1.1。**【直讀】輸出 Markdown，頁面沒有提到座標。權重授權在 GitHub 頁面沒有單獨寫明。
    來源：<https://github.com/allenai/olmocr>

49. **Chandra OCR 2（2026-03）：程式碼 Apache-2.0，權重是修改版 OpenRAIL-M。**【直讀】募資或營收低於 200 萬美元免費；頁面寫權重「cannot be used competitively with our API」。Mortise 與 Datalab 的文件轉換服務可能被視為競爭。
    來源：<https://github.com/datalab-to/chandra>

50. **MonkeyOCR：程式碼 Apache-2.0，v1 權重僅限非商用。**【直讀】原文：「provided for academic research and non-commercial evaluation only」。README 自述尚未完整支援繁體中文、手寫、拍攝文字。2026 年 7 月的 MonkeyOCRv2 在橫幅標示 Apache-2.0，位於另一個程式庫，未查證。
    來源：<https://github.com/Yuliang-Liu/MonkeyOCR>

51. **GOT-OCR2.0：程式碼 Apache-2.0，訓練資料 CC BY-NC 4.0，權重授權頁面沒寫。**【直讀】2024-09-03 發布。
    來源：<https://github.com/Ucas-HaoranWei/GOT-OCR2.0>

52. **OpenOCR：Apache-2.0，支援中文加英文，沒有提到繁體中文。**【直讀】SVTRv2（ICCV 2025）、UniRec-0.1B、OpenDoc-0.1B；可匯出 ONNX。
    來源：<https://github.com/Topdu/OpenOCR>

53. **以下模型只有搜尋摘要或第三方來源，列為未查證**：LightOnOCR-2（Apache-2.0，Hugging Face 部落格）、LightOnOCR-3（第三方稱 Apache-2.0，0.8B、1B、4B 三種大小）、Granite-Docling-258M（次級來源稱 Apache-2.0）、Xiaomi-OCR-0（第三方稱 Apache-2.0、0.8B，發布日期有 2026-09-30 與 2026-10-30 兩種說法）、Nanonets-OCR2（Hugging Face 部落格稱其授權不明）。
    來源：<https://huggingface.co/blog/lightonai/lightonocr-2>、<https://huggingface.co/blog/ocr-open-models>、<https://ibm.com/new/announcements/granite-docling-end-to-end-document-conversion>、<https://pasqualepillitteri.it/en/news/28641/xiaomi-ocr-0-open-source-ocr-model-documents>

54. **OmniDocBench v1.6 各家分數，來源略有出入。**【搜尋摘要】PaddleOCR-VL-1.6 論文的表：PaddleOCR-VL-1.6 96.33、MinerU2.5-Pro 95.75、GLM-OCR 95.22、dots.ocr 90.77。MinerU2.5-Pro 自己的論文寫 95.69。這個評測量的是整頁文件解析，不是逐字位置，對 Mortise 的座標需求沒有直接參考價值。
    來源：<https://arxiv.org/pdf/2606.03264>、<https://arxiv.org/pdf/2604.04771>

## 4. 建議

### 首選：PP-OCRv6 模型，以 ONNX Runtime 執行，自己寫解碼層

- **模型**：伺服器用 PP-OCRv6 medium，未來桌面版用 small（辨識模型 20.4 MB，Apple M4 上 ONNX Runtime 約 1.29 秒每張）。
- **執行方式**：以 RapidOCR 為起點，或直接載入 ONNX 權重自己包一層。兩者的程式與權重都是 Apache-2.0，相依套件沒有 GPL 或 AGPL，ONNX Runtime 是 MIT。不建議把整個 PaddlePaddle 框架帶進桌面版。
- **理由**：
  1. 這次查到的方案裡，只有它同時具備：官方公布的繁體中文分項準確度、每行四點座標（可表示傾斜的行）、逐字位置、乾淨的授權、官方公布的 CPU 速度。Tesseract 也有繁中、逐字位置與乾淨授權，但行框是水平矩形，而且沒有繁中準確度數字。
  2. 單一模型同時處理繁中與英文，混排不必切換模型。
  3. 持續維護中，2026 年有四次以上的版本更新。
- **必須自己補的三件事**（函式庫都沒有現成的）：
  1. **候選字**：直接取模型輸出的 (batch, time, vocab) 機率矩陣，每個時間步取前幾名。
  2. **字框精修**：內建字框是由時間步位置推算的近似值，Mortise 要做到排版一致，需要在文字行影像上再做一次對齊。
  3. **座標反算**：傾斜的文字行要把字框轉回原圖座標。RapidOCR 已內建這一步，PaddleOCR 管線據回報沒有（第 13 條）。

### 備選一：Tesseract 5.5 加 chi_tra，當第二意見引擎

- Apache-2.0，引擎與訓練資料都乾淨。
- 它是這次查到的引擎裡唯一在官方文件記載**內建輸出候選字與信心值**的，架構與 PP-OCR 不同，適合拿來仲裁。兩者的錯誤是否真的互補，要實測才知道。
- 繁中準確度沒有官方數字，對照片輸入的表現也沒有查到資料，兩項都要自己量。在量出結果之前，先只把它用在掃描文件與原生 PDF 轉出的乾淨影像上。

### 備選二：PP-OCRv5_server_rec，當同家族的第二辨識模型

- 官方文件明列支援直排文字（分項 0.9314），PP-OCRv6 的文件沒有提直排。未來做直排時先測這個。
- 與 PP-OCRv6 共用偵測結果與解碼程式，接入成本最低。

### 備選三：PaddleOCR-VL-1.6 或 GLM-OCR，只在伺服器端重讀困難區塊

- 兩者授權都乾淨（Apache-2.0、MIT），都是 0.9B 參數，運算成本比 PP-OCR 高（未實測）。
- 只用來重讀低信心區塊的**文字內容**，不要用它們的座標。它們沒有逐字位置。PaddleOCR 官方文件另聲稱視覺語言模型會依語言習慣「自動修正」文字（見第 5 條，廠商說法），檔案急救情境要實測確認。

### 不建議

| 名稱 | 原因 |
|---|---|
| Surya、Chandra | 權重授權有營收門檻，超過需付費；Chandra 另有不得與其服務競爭的條款；Surya 現行版本只輸出區塊。若願意付費可再評估 |
| HunyuanOCR | 授權不適用於歐盟、英國、南韓 |
| MonkeyOCR v1 | 權重僅限非商用 |
| dots.mocr | 協議有禁止未經授權文件數位化的條款，需法務先判讀 |
| docTR | 沒有中文預訓練模型 |
| EasyOCR | 兩年多沒有新版，繁中模型是第一代 |
| MMOCR | 三年多沒有新版 |
| MinerU | 授權可用，但它是整套文件解析管線，不是 OCR 元件；且線上服務有揭露義務 |

## 5. 查不到的事

1. **沒有任何獨立的繁體中文準確度評測。** PaddleOCR 的數字全是內部資料集，而且同一個模型的繁中準確率在兩份官方文件裡是 93.29% 與 74.7%。Tesseract、EasyOCR 的繁中數字完全查不到。**必須自建繁中加英文測試集。**
2. **PP-OCRv6 官方模型卡的授權欄位**沒能親自打開（huggingface.co 被擋）。目前是間接證據。
3. **PP-OCRv6 是否支援直排文字**：官方文件沒寫。PP-OCRv5 有寫。
4. **PP-OCRv6 的字典大小與繁體字涵蓋範圍**：文件只說多了約 200 個變音字元，沒有給總字數。罕用字、異體字、台灣特有用字的涵蓋情況未知。
5. **CTC 推算字框的實際誤差**：沒有找到任何量化資料。這直接關係到「排版一致」的承諾，必須自己量。
6. **Tesseract LSTM 引擎逐字框的誤差大小**：官方手冊只說「可能較不精確」，沒有量化資料。
7. **PaddleOCR-VL 文字行定位的輸出格式**（四邊形或多邊形、是否含信心值）。
8. **Surya 權重授權的完整條文**，包括是否允許再散布、「競爭」如何定義。
9. **GOT-OCR2.0、olmOCR、OpenOCR、MinerU 模型權重各自的授權**：GitHub 頁面沒有單獨寫明。
10. **LightOnOCR-3、Xiaomi-OCR-0、Granite-Docling、Nanonets-OCR2、MonkeyOCRv2** 的官方模型卡都沒能打開，授權與能力都只有次級來源。
11. **MMOCR 是否有可用的繁中預訓練模型。**
12. **RapidOCR 的字典檔與字型檔的授權**：其聲明明說這兩類不在 Apache-2.0 範圍內，實際條款未查。
13. **RapidOCR v5.0.0** 預發行版改了什麼、何時正式發布。
14. **專利**：Apache-2.0 含專利授權條款，但個別模型架構是否另有第三方專利主張，未查。
15. **被擋住的網站**：huggingface.co、www.paddleocr.ai、paddlepaddle.github.io、arxiv.org、roboflow.com。若要補查上面第 2、7、8、10 項，需要先在工作環境的網路設定開放這些網域。

# 查證紀錄：照片中的文件／海報邊界偵測

- 查證日期：2026-11-23。授權底線與 [image.md](image.md) 相同：可商用、排除 GPL／AGPL、排除非商用權重；權重沒有明文授權的寫「未另立」。
- 起因：創辦人上傳的手機照片（印刷課表、米色布料背景、紙上緣捲起有折痕、右側露出另一張紙、光線不均），`find_page_quad` 回傳整張圖、信心 0.15。
- 目前做法見 [page_geometry.py](../../../mortise/recognition/page_geometry.py) 開頭說明：縮圖上「Lab Canny 輪廓凸包」與「中央眾數色區域」產生候選 → 原圖沿法線找邊緣點、Tukey 直線擬合 → 四邊支持率都 ≥ 0.4 才接受 → Zhang–He 由透視估長寬比。

## 0. 查證方式與限制

| 來源 | 狀況 |
|---|---|
| raw.githubusercontent.com、GitHub Releases 下載、pypi.org、registry.npmjs.org、developer.apple.com（`.md` 版文件）、microsoft.com 研究部落格 | 可直接讀取 |
| huggingface.co、Google Drive、docsaid.org、developers.google.com、support.apple.com、support.microsoft.com、adobe.com、dropbox.tech、arxiv.org、scanbot.io、dynamsoft.com | **讀不到**（網路規則擋下），只能用搜尋摘要 |

- 標記：**【直讀】** 親自讀到原文（LICENSE、README、原始碼、套件中繼資料、官方文件）；**【搜尋摘要】** 搜尋工具轉述官方頁；**【第三方】** 非官方來源；**【實測】** 本次在工作機上跑出來的；**【判讀】** 我的推論。
- 實測環境：4 核 CPU、無 GPU，**同一台機器同時有其他工作在跑（load average 約 10）**，所有毫秒數只能當上限參考。
- 實測資料：(a) 創辦人照片的縮小版（485×751，從介面截圖裁下）；四角由我逐列看亮度剖面手標：左上 (24,96)、右上 (481,89)、右下 (435,712)、左下 (42,671)，誤差約 ±3 像素，但**上緣是捲曲的**（x=30 處 y≈96、x=100 處 y≈100、x=475 處 y≈88），本身就不是一條直線。(b) `mortise/eval/synth.py` 的 photo 干擾，3 份 PDF × 強度 1、2 × 6 個種子 = 36 張。(c) 我另外合成 8 張「米色背景＋右側第二張紙＋光線漸暗」（820×1100）。實驗腳本與遮罩放在本次工作階段的暫存區 `research-photo/`（未進版本庫，可能被清除）。

## 1. 這張照片為什麼失敗【實測】

1. 重現：`find_page_quad` → `method="full_image"`、信心 0.15，notes：「只找到右、下、左邊（上邊缺乏邊緣支持…）」「畫面中有傾斜的長直邊…建議請使用者手動拉四角」。耗時 0.25 秒。
2. `_initial_candidates` 產生 23 個候選，**沒有一個接近真正的紙**（四角平均誤差最小 71 像素）。候選分三類：紙內的**表格框線**（課表本身就是一個很強的四邊形）、紙加上邊緣陰影一路連到畫面邊、紙加上右側第二張紙。所以問題出在候選來源，不是精修。
3. 亮度剖面看到的三個難點：
   - 上緣右半段布料 197–216、紙 219–222，**亮度差只有 5–20 色階**；中段紙緣捲起，從陰影 46 漸變到 185 跨了約 25 像素，沒有銳利邊。
   - 左下角背景 80–100、紙約 100，幾乎無對比；左側另有一條直尺狀物體。
   - 右側第二張紙與主紙同色且相連，任何「顏色區域」法都會把兩張併成一塊。
4. **最小改動驗證**：把第 2.2 節的直線候選（取前 3 名）加進 `_initial_candidates`，其餘程式不變 → `method="edge_fit"`、信心 0.836、四邊支持率 [0.83, 0.93, 1.00, 0.76]、四角平均誤差 6.7、最大 18.1 像素，耗時 2.7 秒（直線組合的 Python 迴圈未最佳化）。
5. **但信心值過高**：最大誤差 18 像素在左上角——上緣捲曲，擬合出的直線在左端跑到布料上（y=78，實際約 96）。模組回報的四角誤差卻只有 0.6 像素，長寬比 1.498±0.003（信心 0.99）；用手標四角算出的是 1.427（A4 為 1.414）。**現行誤差傳播只算「直線擬合的雜訊」，沒算「邊根本不是直線」的模型誤差。**

## 2. 傳統方法

### 2.1 做法與開源實作

| 做法 | 開源實作與授權 | 弱點 | 來源等級 |
|---|---|---|---|
| 輪廓＋多邊形近似（找最大的四邊形輪廓） | OpenCV 範例式寫法；jscanify 1.4.3（npm，MIT，OpenCV.js）；OpenCV-Document-Scanner（andrewdcampbell）根目錄 `LICENSE` 讀不到（404），**預設不可用**；OpenNoteScanner **GPL-3.0，排除** | 需要閉合輪廓；低對比、捲邊、相連的第二張紙、紙內表格都會讓它挑錯（本照片即是） | 授權【直讀】 |
| 邊緣圖＋霍夫或線段偵測＋四條線組合評分 | OpenCV `createLineSegmentDetector`（LSD）在 5.1.0 可用，`lsd.cpp` 檔頭為 OpenCV Foundation 的 BSD 式授權；Dropbox 2016 年的掃描器公開文章即是「強邊緣 → 霍夫找直線 → 挑最佳四邊形」 | 組合數爆炸要剪枝；紙內的表格線、桌緣、第二張紙的邊都是候選，要靠評分分辨 | LSD【直讀】【實測】；Dropbox【搜尋摘要】 |
| 學習式邊緣圖（HED 類）＋找四角 | hed-tutorial-for-document-scanning（iOS，HED＋OpenCV 找點）根目錄 `LICENSE` 讀不到（404），**預設不可用**；TEED 邊緣模型 MIT（權重未查） | 仍需後段的四邊形搜尋 | 【直讀】 |
| 顏色／亮度分割、GrabCut | OpenCV `grabCut`（Apache-2.0） | 背景與紙同色、陰影連到畫面邊時會溢出 | 【實測】 |
| 以文字區域當先驗 | 專案已有 RapidOCR 的 PP-OCR 偵測模型 | 第二張紙的字也會被偵測到；海報字少 | 【實測】 |
| 手機端掃描介面 | WeScan（MIT，iOS；README 列出「即時掃描、編輯偵測到的矩形、自動拍攝」）；jscanify README 寫明以 opencv.js 驅動 | 只在裝置端；底層偵測法未細查 | 【直讀】 |

### 2.2 本次實測（創辦人照片）

| 方法 | 四角誤差（平均／最大，像素） | 時間 | 說明 |
|---|---|---|---|
| 現行 `find_page_quad` | 失敗（整張圖） | 0.25 s | 見第 1 節 |
| **LSD 直線組合**（Lab 三通道 LSD → 合併共線線段 → 文字中心上下左右各取最長 6–8 條＋畫面邊 → 四條線交點組合 → 以 `_quick_support` 的邊緣支持率評分） | **4.8／10.6** | LSD 本身 60–90 ms；組合評分 5 s（Python 迴圈，可剪枝） | 第 1 名就是紙；第 3 名是表格上緣（41／83），分數 0.911 對 0.956，差距不大 |
| 同上＋文字先驗（邊不可穿過文字框、四邊形要包含文字面積） | 4.8／10.6 | 我的寫法逐框測點 59 s，必須改成「文字框先畫成遮罩再沿邊取樣」 | 表格類候選掉出前 4 名，前 4 名全是紙的變體 |
| GrabCut（以文字外包為確定前景、外擴 25% 為可能前景） | 39／90 | 0.7 s | 下方陰影區被併進前景 |
| 文字偵測（PP-OCR det） | — | 1.0–1.2 s | 85 個框，其中 6 個在第二張紙上（x>440），文字外包因此被拉到畫面右緣 |

## 3. 學習式方法

| 名稱 | 輸出 | 程式授權 | 權重授權與取得 | 大小 | CPU 速度 | 四角／信心 | 來源 |
|---|---|---|---|---|---|---|---|
| **DocAligner 1.1.1**（docaligner-docsaid，2026-01-13） | 四角 | Apache-2.0 | **未另立**；三組熱圖模型 `lcnet100`、`fastvit_t8`、`fastvit_sa24`（預設）由 Google Drive 下載；另有點迴歸模型 | 未查到 | 輸入 256×256；未實測 | 熱圖每張取 >0.3 的最大連通區質心，**不回傳信心**；README 說點迴歸模型有分類頭判斷「有沒有文件」 | 程式與 README【直讀】；**權重下載被擋，未實測** |
| **LDRNet**（ECML-PKDD 2022） | 四角＋邊線＋是否有文件 | MIT | 程式庫內 `model_file/model-icdar-1.4`（TensorFlow），未另立授權；訓練資料推測為 ICDAR 2015 SmartDoc（未證實） | 未查 | 作者稱最高 790 FPS | 有分類頭 | LICENSE【直讀】；其餘【搜尋摘要】 |
| **U²-Net-p**（經 rembg 2.0.87 的 ONNX） | 前景遮罩 | U-2-Net Apache-2.0；rembg MIT | **未另立**：原作者 README 只給 Google Drive 下載（u2netp.pth 4.7 MB），以 DUTS-TR 訓練（資料條款未查）；ONNX 轉檔在 rembg 的 GitHub Release | 4.6 MB | 0.4–1.1 s（輸入 320×320） | 無；要自己由遮罩化四邊形 | 【直讀】【實測】 |
| ISNet general-use（DIS） | 前景遮罩 | Apache-2.0 | 未另立；ONNX 在 rembg Release | 179 MB | 3.2 s | 無 | 【直讀】【實測】 |
| BiRefNet（swin tiny 版） | 前景遮罩 | MIT | 未查；ONNX 在 rembg Release | 224 MB | 1024×1024 輸入時程序常駐記憶體到 13.8 GB 被系統砍掉，**未完成實測** | 無 | 【直讀】 |
| **SAM ViT-B**（rembg 量化 ONNX） | 依提示點或框分割 | Apache-2.0 | README：模型以 Apache-2.0 授權 | 編碼器 109 MB＋解碼器 8.8 MB | 每張 4.6–18 s（輸入縮到 684×1024 內）；含 SAM 的批次程序常駐記憶體到 8.1 GB 時被系統砍掉 | 解碼器輸出 `iou_predictions`（模型自估的遮罩品質） | 【直讀】【實測】 |
| SAM 2／2.1 | 同上 | Apache-2.0 | README：權重、示範程式、訓練程式皆 Apache-2.0 | 未查 | 未實測（找到的 ONNX 版都放在 Hugging Face，讀不到） | 同上 | 【直讀】 |
| MobileSAM | 同 SAM，換成 5M 參數的 Tiny-ViT 編碼器 | Apache-2.0 | 權重在程式庫 `weights/mobile_sam.pt`（40.7 MB，已下載） | 40.7 MB | README：GPU 上編碼器 8 ms；CPU 未實測（需 PyTorch 匯出 ONNX） | 同上 | 【直讀】 |
| SAM 3 | — | 自訂 SAM License | — | — | — | — | 見 image.md 第 67 條 |
| Apple `VNDetectDocumentSegmentationRequest` | 四角＋顯著性遮罩 | 系統 API | — | — | 只在 Apple 平台 | 四角以 `VNRectangleObservation` 回傳 | 【直讀】 |

實測結果【實測】。創辦人照片寫「四角平均／最大誤差（像素）」；兩組合成照片寫「最大角誤差 ≤3 像素的張數」：

| 方法 | 創辦人照片（485×751） | 自製 8 張（米色底＋第二張紙） | synth.py 36 張（877–1827 寬） |
|---|---|---|---|
| 現行 `find_page_quad` | 失敗（整張圖） | 8/8，信心 0.97 | 23/36（≤1% 對角線：27/36） |
| LSD 直線組合（無文字先驗）＋現行精修 | 4.8／10.6（精修後 6.7／18.1，捲邊所致） | 8/8 | 22/36：海報常挑到內部的圖片框或去掉色帶的區域 |
| U²-Net-p 遮罩 → 四邊形 | 15.8／49.9：把第二張紙併進來 | 0/8（≤1% 對角線 3/8） | 1/36；但 ≤1% 對角線 30/36 |
| U²-Net-p 遮罩 → 四邊形 → 現行精修 | 16.5／50.0 | 3/8；其餘誤差 60 像素以上（併入第二張紙） | **30/36**（中位誤差 0.32 像素） |
| ISNet 遮罩 | 274／363：切出的是印刷內容，不是紙 | — | — |
| SAM：畫面中心一點 | 316／356：切到一格表格 | — | — |
| SAM：文字中心一點 | 13.2／39.8：併入第二張紙 | — | — |
| SAM：文字外包當框 | 55／108：在文字邊界就停了 | — | — |
| SAM：文字中位數與 25%、75% 分位三點 | **6.2／9.2**（精修後 5.4／10.2） | 1/8 | 0/14（只跑了 14 張）：切到文字區塊 |
| 三種候選（現行、LSD、U²-Net-p＋精修）取最好的一個（理想上限） | — | — | 31/36 |
| 只用精修後的「最弱邊支持率」在 LSD 與 U²-Net-p 之間挑 | — | — | 22/36 |

時間（中位數，負載下）：現行 0.35 s、LSD 組合 0.5 s、U²-Net-p 0.6 s、文字偵測 0.9 s。

**現行方法在 synth.py 照片上的錯誤也值得注意**【實測】：36 張裡 9 張誤差超過對角線 1%；其中一張海報把與背景（棕色）接近的紅色標題帶切掉，誤差 164 像素，**信心卻是 0.97**；另一張挑到海報內部的圖片框（信心 0.57）；一張被手的陰影帶成歪斜四邊形（信心 0.32）。

結論【判讀】：
1. 候選來源互補：直線組合救回創辦人照片；U²-Net-p＋精修在單張紙的合成照片最好（30/36）；現行輪廓法成功時最準（中位誤差 0.37 像素）。三者聯集的理想上限 31/36，但只用「最弱邊支持率」在 LSD 與 U²-Net-p 之間挑，反而掉到 22/36——**合成照片上的瓶頸在候選評分**（創辦人照片則是候選來源本身不足，見第 1 節）。海報的失敗（圖片框、色帶）都是「四邊形外還有屬於同一張紙的內容」，正是文字先驗與「外側內容」懲罰要處理的。
2. 通用前景分割模型學的是「顯著物體」：相連的第二張紙會被併入；ISNet 甚至只切印刷內容。只能當候選之一，不能直接採用其四角。
3. SAM 的提示有歧義（整張紙、表格、一段文字都合理），這份匯出只給一個遮罩；同樣的提示在創辦人照片上最好、在合成照片上全錯；CPU 上每張 5–18 秒、記憶體數 GB。現階段不建議。
4. 專門的文件角點模型（DocAligner、LDRNet）才是對題的學習式方法，但權重都沒有明文授權、訓練資料條款未查到，本次也無法下載實測。
5. 我自製的「米色底＋第二張紙」對現行方法並不難（8/8）：合成的紙邊筆直、亮度差夠大。創辦人照片的難處在**捲邊、極低對比與陰影**，synth.py 目前都沒有模擬。

## 4. 商用 App 的公開說法

- **Apple**【直讀】
  - `VNDetectDocumentSegmentationRequest`（iOS 15、macOS 12 起）：「結果包含文件四邊形的四個角點與顯著性遮罩」；結果型別是 `VNRectangleObservation`。[文件](https://developer.apple.com/documentation/vision/vndetectdocumentsegmentationrequest)
  - Swift 版 `DetectDocumentSegmentationRequest`（iOS 18、macOS 15 起）回傳 `DetectedDocumentObservation`，含 `globalSegmentationMask`。[文件](https://developer.apple.com/documentation/vision/detectdocumentsegmentationrequest)、[觀測](https://developer.apple.com/documentation/vision/detecteddocumentobservation)。開發者論壇說遮罩每個像素是「屬於文件的信心」【搜尋摘要】[討論串](https://developer.apple.com/forums/thread/695858)。
  - 較舊的 `DetectRectanglesRequest`：回傳每個矩形的信心值，可設 `minimumConfidence`、`maximumObservations`、`minimumAspectRatio`／`maximumAspectRatio`、`minimumSize`、`quadratureToleranceDegrees`（角度偏離 90° 的上限）。[文件](https://developer.apple.com/documentation/vision/detectrectanglesrequest)
  - VisionKit `VNDocumentCameraViewController`：系統提供的掃描介面，一頁一頁掃。[文件](https://developer.apple.com/documentation/visionkit/vndocumentcameraviewcontroller)。iOS 26 起另有 `RecognizeDocumentsRequest` 解析文件結構（表格、清單）。[文件](https://developer.apple.com/documentation/vision/recognizedocumentsrequest)
  - Apple 支援文件：掃描後「拖曳四角讓掃描符合頁面，再點『保留掃描』」【搜尋摘要】[說明](https://support.apple.com/108963)。
- **Google ML Kit Document Scanner**【搜尋摘要】：僅 Android（API 21 起）；模型、掃描邏輯與介面都由 Google Play 服務動態下載；自動偵測後拍攝、邊緣偵測、裁切、濾鏡、去陰影；Full 模式另有以模型清除污漬與手指。介面與流程由 Google 提供，無法放到我們的伺服器端（此句為【判讀】）。[文件](https://developers.google.com/ml-kit/vision/doc-scanner)、[發表文](https://android-developers.googleblog.com/2024/02/ml-kit-document-scanner-api.html)
- **Microsoft Lens**：2014 年 Microsoft Research 部落格：「Office Lens 偵測文件或白板的邊緣，或讓你手動設定邊框」；技術源自 Zhengyou Zhang 與 Li-wei He 的 Whiteboard It（即本模組長寬比所用 Zhang–He 方法），並就邊緣偵測請教 Piotr Dollár【直讀】[部落格](https://www.microsoft.com/en-us/research/blog/office-lens-snap/)。支援頁：取景時橘色框要框住物件；可拖曳裁切控點；Android 版有「裁切控點放大鏡」【搜尋摘要】[Android 說明](https://support.microsoft.com/en-us/lens/microsoft-lens-for-android)、[更新說明](https://techcommunity.microsoft.com/t5/office-365-blog/what-s-new-in-office-lens-december-update/ba-p/297049)。
- **Adobe Scan**【搜尋摘要】：拖曳裁切控點調整邊框；Android 版按住角落會出現放大鏡；有「自動偵測」（重新自動放邊框）與「不裁切」（顯示整張圖）兩個按鈕；設定可開「自動拍攝後讓我逐張調整邊框」。[Android 說明](https://www.adobe.com/devnet-docs/adobescan/android/en/scan.html)、[iOS 說明](https://www.adobe.com/devnet-docs/adobescan/ios/en/modify.html)
- **Dropbox**【搜尋摘要】：2016 年文章，偵測步驟是「找強邊緣 → 霍夫轉換讓邊緣點投票找直線 → 找最佳四邊形」；後續文章談在舊手機上即時跑。[文章](https://dropbox.tech/machine-learning/fast-and-accurate-document-detection-for-scanning)、[後續](https://dropbox.tech/machine-learning/improving-the-responsiveness-of-the-document-detector)
- **商用 SDK 的狀態設計**【搜尋摘要】：Scanbot 的偵測狀態分 `OK`、`OK_BUT_TOO_SMALL`、`OK_BUT_BAD_ANGLES`（透視太大）、`OK_BUT_TOO_DARK`、`ERROR_NOTHING_DETECTED`、`ERROR_TOO_DARK`、`ERROR_TOO_NOISY` 等（各版本定義略有出入）[API](https://api-docs.scanbot.io/document-scanner-sdk/capacitor/v9.0.0/types/DocumentDetectionStatus.html)；Dynamsoft Document Normalizer 可回傳**一張圖裡的多個四邊形**，每個帶 `confidenceAsDocumentBoundary`，官方流程是「由應用程式挑一個四邊形、讓使用者調整、再確認」[說明](https://www.dynamsoft.com/document-normalizer/docs/introduction)、[API](https://dynamsoft.com/document-normalizer/docs/mobile/programming/android/api-reference/detected-quad-result-item.html)。
- 查不到：Apple、Google、Microsoft、Adobe 的偵測模型架構、大小與準確率，都沒有公開。

## 5. 拖曳四角與「多張紙」的互動慣例

1. **四個角控點＋放大鏡**：Apple（拖四角後「保留掃描／重拍」）、Adobe Scan（按住角落出現放大鏡）、Microsoft Lens（Android 有裁切控點放大鏡）。手指會擋住角落，放大鏡幾乎是標配【搜尋摘要】。
2. **一鍵退路**：Adobe Scan 有「自動偵測」與「不裁切」兩個按鈕【搜尋摘要】。我們對應為「重新偵測」與「用整張圖」。
3. **偵測與調整分開**：Adobe Scan 可設定拍完逐張調整；Apple、Lens 都是先自動、使用者可回頭改【搜尋摘要】。
4. **邊中點控點**（拖一條邊而不是一個角）：常見於第三方掃描 App，但**沒查到上述四家的官方說法**。
5. **一張照片多張紙**：沒有查到任何手機掃描 App 公開「從多張紙中挑一張」的介面。最接近的是 Dynamsoft 回傳多個四邊形由應用程式挑選【搜尋摘要】，以及桌面掃描軟體（VueScan Multi Crop、AutoSplitter）先框出全部候選、讓使用者調整後各自輸出【第三方】。
6. 建議的介面【判讀】：照片上同時畫出所有候選框，最高分者預選並加粗；點另一個框即切換；控點只出現在選中的框；下方按鈕「重新偵測／用整張圖／確認」；若候選 ≥2 且分數接近，預設停在這一步，不要自動往下跑。

## 6. 給實作的建議

### 6.1 建議管線（全部 CPU；第一階段不需新增套件）

原則：**多個候選來源 → 同一套精修 → 同一套評分**。創辦人照片缺的是候選（第 1 節），合成照片缺的是評分（第 3 節結論 1），兩者都要改。

1. **保留**現行的輪廓與色塊候選、原圖精修 `_refine`、Zhang–He 長寬比。
2. **新增直線候選**（`_initial_candidates` 的替換點）：縮圖（長邊 900）的 Lab 三通道各跑一次 LSD → 長度 ≥ 短邊 4% 的線段 → 依角度（容差 3°）與距離（4 像素）合併成直線 → 以文字中位點（無字時用畫面中心）為中心，上下左右各取最長 6–8 條，另加四條畫面邊（代表紙超出畫面）→ 窮舉組合，先用幾何條件剪枝（凸、內角 45°–135°、面積 ≥10%、角點出界 ≤ 對角線 12%）再評分。
3. **文字先驗**（OCR 偵測已在管線內，順序要改成「先偵測文字框、後找頁面」，或在頁面偵測內用縮圖另跑一次偵測）：
   - 文字框先畫成一張遮罩；每條邊沿線取樣，**穿過文字框就重罰**（紙邊不會切過字）；
   - 四邊形內的文字面積比例做加分（取平方）；
   - 文字框要分群：主群是面積最大、行方向一致的那群，其他群視為「可能是另一張紙」；
   - **外側內容懲罰**：四邊形外緊鄰一條邊（距離 < 該邊長 15%）若還有主群文字、或與紙內同色的大色塊（海報的色帶），扣分——對應 synth.py 中「切掉紅色標題帶卻信心 0.97」與「挑到內部圖片框」兩種錯誤。
4. **多張紙**：保留互不重疊（IoU < 0.3）、分數 ≥ 最高分 × 0.6 的候選，全部回傳給上層（`PageQuad` 增加 `alternatives` 清單）。
5. **捲邊與彎邊檢查**（第 1 節第 5 條的修正）：精修後，每條邊的邊緣點另擬合二次曲線，量弓高（曲線與直線的最大距離）；弓高 > 2 像素或殘差有系統性彎曲時：該邊標「紙邊彎曲」、把弓高加進兩個端角的誤差、並在 notes 建議去彎曲（UVDoc，見 image.md）。
6. **第二階段：U²-Net-p 當第三個候選來源**（4.6 MB，ONNX Runtime 已是專案相依）。它在單張紙的合成照片上精修後 30/36，是三種來源中最好的；但會併入相連的第二張紙，所以只當候選、不直接採用其四角。上線前要先解決權重授權（原作者沒有另立權重授權，訓練資料 DUTS-TR 條款未查）；替代方案是用 synth.py 的合成資料自訓同架構的「紙張遮罩」模型，授權最乾淨。SAM 類與 DocAligner 暫不採用（理由見第 3 節）。

### 6.2 信心值的算法

`conf = 0.05 + 0.93 × f_sup × √f_con × f_err × f_vis × f_straight × f_gap × f_aspect × f_out`

- `f_sup`、`f_con`、`f_vis`：沿用現行定義（最弱一邊的支持率、最弱一邊的內外色差、是否有角在畫面外）。
- `f_err = 1 / (1 + (e / 1.5)²)`，但 `e` 改成 `max(擬合協方差誤差, 弓高, 候選間分歧)`；「候選間分歧」指前兩名「同一張紙」的候選精修後四角差異的最大值。
- `f_straight`：四邊最大弓高 0 像素時為 1，達到 1% 短邊時降為 0.5。
- `f_gap`：第一名與第二名（**不同區域**的候選）分數比 r；r ≤ 0.6 為 1，r ≥ 0.9 為 0.5（代表照片裡有兩張差不多可信的紙）。
- `f_aspect`：長寬比落在常見紙張（A 系列 1.414、Letter 1.294、Legal 1.647、名片 1.65–1.75、正方形 1）的 2% 內為 1，否則 0.85；海報尺寸多樣，只當輕微加權。
- `f_out`：第 6.1 節第 3 點的外側內容；有主群文字或同色色塊緊貼在某條邊外側時為 0.5，否則 1。現行算法缺這一項，所以會出現「錯得很有信心」。
- 分數用途分開：**評分**用來在候選間排序；**信心**用來決定要不要問使用者。上線前用手標照片畫「信心 vs 實際誤差」的校準圖，調到信心 0.6 以上的照片中，最大角誤差 >1% 對角線的比例低於 5%。

### 6.3 何時停下來問使用者

- 信心 < 0.6；或回傳 `full_image` 但輸入判為照片；
- 有 ≥ 2 個不同區域的候選且 `f_gap` < 1（多張紙）；
- 任一角誤差 > 對角線的 1%，或有邊被標「紙邊彎曲」；
- 角點落在畫面外（紙被裁掉），且使用者沒有明說要整張。
- 問的方式：直接進入第 5 節第 6 條的四角編輯畫面，不要用文字對話框。

### 6.4 實作步驟

1. `lsd_candidates(small, text_mask)`：直線合併、組合剪枝、評分（向量化；目標 < 300 ms）。
2. 文字遮罩與分群：沿用 `OcrEngine._detect`，回傳框；分群用框中心距離與行高。
3. 評分加入「穿過文字框」「文字面積比例」「外側內容」；用 synth.py 36 張＋自製 8 張＋創辦人照片當回歸測試，目標是逼近各來源聯集的理想上限（目前 31/36），且創辦人照片要排對。
4. `_refine` 之後加弓高檢查，並把弓高與候選分歧併入 `corner_error_px`。
5. `PageQuad` 增加 `alternatives` 與 `status`（參考 Scanbot 的狀態詞彙：`ok`、`low_contrast`、`curved_edge`、`multiple_pages`、`cut_off`、`not_found`）。
6. 前端四角編輯器：四角控點＋放大鏡、多候選切換、「重新偵測／用整張圖／確認」。
7. 測試資料：`synth.py` 加入「與紙同色的背景」「相連的第二張紙」「上緣捲曲（非線性位移場）」「紙內大表格」四種干擾；並收集 ≥ 30 張真實照片手標四角。

### 6.5 待驗證的假設

- LSD 直線組合與新評分在更多真實照片上仍能把紙排第一（目前只有 1 張真實照片與 44 張合成照片）。
- 「外側內容懲罰」能修正海報的兩種錯誤，且不會把桌上其他印刷品誤判成同一張紙的內容。
- 文字先驗的向量化實作能在 100 ms 內完成；海報字少時不會反而扣分。
- 弓高門檻 2 像素（以原圖解析度）是否合適，需用原始解析度照片（本次只有 485×751 縮圖）重量。
- DocAligner、LDRNet 的權重是否有明文授權、在本照片上的表現如何（本次下載被擋）。
- MobileSAM 在 CPU 上的速度，以及「多遮罩輸出＋幾何挑選」能否解決 SAM 提示的歧義。
- 長寬比在捲邊照片上偏差多少：本次精修後 1.498，手標 1.427，A4 為 1.414。

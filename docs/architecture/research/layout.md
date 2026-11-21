# 查證紀錄：版面分析、區域分割、表格結構辨識

- 查證日期：2026-11-20
- 適用產品：Mortise（榫）文件反編譯 SaaS，未來可能有桌面版
- 授權限制：所有元件必須可商用；排除 GPL、AGPL 元件（另有可購買的商業授權者列為需付費選項）；排除非商用授權的模型權重

## 0. 結論先講

1. **沒有任何一個現成模型能直接輸出本產品需要的分類**（文字、照片、插畫、向量圖形、表格、背景），也沒有任何一個版面偵測模型的說明文件把海報列為訓練資料。現成模型解決的是「文件型頁面」的區塊偵測；海報（字壓在圖上、元素重疊）要另外用「文字遮罩 + 通用分割 + 圖片類型分類 + 圖層分解」的組合處理，而且必須自己建測試集驗證。這一點是從各模型的類別清單與訓練資料說明推論而來，本次沒有實測。
2. **首選（全部是 Apache-2.0 或 MIT，可商用、可隨桌面版散布）**
   - 版面偵測：PP-DocLayoutV3（PaddleOCR 3.4.0 起提供；25 類、輸出多邊形輪廓與閱讀順序）。
   - 表格：PaddleOCR 的表格模型組（表格分類 → SLANeXt 有線／無線 → RT-DETR-L 儲存格偵測），輔以 Table Transformer v1.1（MIT）交叉驗證。原生 PDF 的表格先用向量線條與文字座標的規則法，模型只當後援。
   - 通用分割（海報、重疊元素）：SAM 2.1（Apache-2.0）。
3. **備選**：Docling 的 Heron 版面模型（Apache-2.0）與 TableFormer（程式碼 MIT、權重 CDLA-Permissive-2.0），可當第二偵測器或全 MIT 的替代路線。圖層分解可試 LayerD（Apache-2.0，但原始庫已於 2026-10-30 封存）與 Qwen-Image-Layered（Apache-2.0）。
4. **排除**：DocLayout-YOLO（AGPL-3.0）、以 Ultralytics YOLO 訓練的社群模型（AGPL-3.0，除非購買 Ultralytics Enterprise License）、FastSAM（授權標示互相矛盾）。
5. **需付費或需法務確認才可用**：Marker／Surya 的模型權重（修改版 Open RAIL-M：營收或募資超過 500 萬美元、或產品與授權方競爭者，須另購商業授權）；SAM 3／3.1（Meta 自訂的 SAM License，檢查點需申請）；MinerU（自訂授權，以 Apache 2.0 為基礎加附加條件，模型權重授權未查到）。
6. **長期路線**：以 RT-DETRv2 或 D-FINE（皆 Apache-2.0）為基底，用 DocLayNet（CDLA-Permissive-1.0）加上自行標註的海報、簡報資料，訓練符合本產品分類的模型。

## 1. 查證方法與限制

- 本次可直接開啟的網站只有 `github.com` 與 `raw.githubusercontent.com`。`huggingface.co`、`www.paddleocr.ai`、`paddlepaddle.github.io`、`www.ultralytics.com` 在這個工作階段的網路設定下無法解析，因此 Hugging Face 模型卡與資料集卡的內容是透過搜尋工具取得的摘要，沒有直接開啟頁面。
- 查證狀態分三級：
  - **已查證**：直接讀取官方原始庫的 LICENSE 檔、README 或官方文件；或搜尋工具讀到官方模型卡的授權欄位。
  - **部分查證**：只確認了一部分（例如程式碼授權已確認，權重授權未確認），或只有第三方來源。
  - **未查證**：查不到，或來源互相矛盾。
- 網頁內容是透過會做摘要的讀取工具取得，引用的原文都是短句。**正式採用任何元件之前，請法務或工程人員再開一次 LICENSE 檔逐條確認。**
- 本次沒有執行任何模型，所有「能力」欄都來自官方文件或論文的敘述，不是實測結果。

## 2. 總表

來源欄的代號對應第 6 節的來源清單。

| # | 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用（SaaS） | 可否隨桌面版散布 | 能力 | 限制 | 查證狀態 | 來源 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | PP-DocLayoutV3（PaddleOCR／PaddleX） | 版面偵測 | Apache-2.0 | Apache-2.0（官方模型卡標籤） | 可 | 可（附授權與 NOTICE） | 25 類；以 RT-DETR 為基礎的實例分割，輸出多邊形輪廓點與閱讀順序；訓練資料含中英文論文、多欄雜誌、報紙、PPT、合約、書籍、試卷、研究報告 | 訓練資料為「自建資料集」，來源未公開；文件未提海報；圖片類只分 image、chart、seal、header image、footer image，不分照片／插畫／向量圖；PaddleX 文件未列 mAP | 已查證（權重標籤經搜尋讀官方模型卡） | S6 S7 S11 S12 S13 S14 S82 |
| 2 | PP-DocLayoutV2、PP-DocLayout_plus-L、PP-DocLayout-L／M／S | 版面偵測（前幾代） | Apache-2.0 | V2：Apache-2.0（鏡像模型卡）；其餘未逐一確認 | 可（依專案授權） | 可（依專案授權） | V2：25 類、mAP(0.5) 81.4、203.8 MB、另接指標網路還原閱讀順序；plus-L：20 類、mAP 83.2，訓練資料含 PPT、多版面雜誌、古籍；L：23 類、mAP 90.4 | 只輸出矩形框；訓練資料為自建資料集；各模型卡的授權標籤未逐一確認 | 部分查證 | S6 S8 S9 S10 S83 |
| 3 | PP-StructureV3（管線） | 整套文件解析：版面 + OCR + 表格 + 公式 + 圖表 + 印章 + 影像校正 | Apache-2.0 | 依各子模型（PaddleOCR 專案聲明 Apache-2.0） | 可 | 可 | 版面偵測、表格分類與結構辨識、儲存格偵測、公式辨識、圖表轉表格（PP-Chart2Table）、文件影像去扭曲（UVDoc）、方向分類 | 輸出以內容為主（Markdown、JSON），不是可編輯的版面重建；需自行把結果轉成本產品的中介格式 | 已查證（程式碼與文件） | S6 S7 S8 |
| 4 | PaddleOCR 表格模型組（PP-LCNet_x1_0_table_cls、SLANeXt_wired／wireless、RT-DETR-L_wired／wireless_table_cell_det、SLANet、SLANet_plus） | 表格結構辨識 | Apache-2.0 | 各模型卡授權標籤未逐一確認 | 可（依專案授權） | 可（依專案授權） | 先分類有線／無線表格，再各自用專屬權重；輸出 HTML 結構符號，合併儲存格以 `colspan` 屬性表示（文件範例）；另有儲存格偵測模型 | 官方自測準確率：SLANeXt 69.65、SLANet_plus 63.69、SLANet 59.52；文件說 SLANeXt 的儲存格座標預測無效，需搭配儲存格偵測模型；文件沒有 `rowspan` 範例 | 部分查證 | S6 S8 S15 |
| 5 | Table Transformer（TATR） | 表格偵測與結構辨識 | MIT | MIT（官方模型卡） | 可 | 可 | 以物件偵測找出 6 類：table、table column、table row、table column header、table projected row header、table spanning cell（合併儲存格）；v1.1 有 Pub、Fin、All 三組權重 | 原始庫已於 2026-09-21 封存為唯讀；最後一次模型更新是 2023-08-22；需自行後處理把列、欄交集成儲存格；訓練資料是論文與財報表格 | 已查證 | S19 S20 S21 S22 S23 S24 |
| 6 | Docling（含 Heron／Egret 版面模型） | 文件轉換工具組；版面偵測 | MIT | Heron：Apache-2.0（官方模型庫標籤） | 可 | 可 | 預設版面模型 `docling_layout_heron`（RT-DETRv2）；另有 heron-101、egret-medium／large／xlarge；輸入支援 PDF、影像、Office 等；輸出 HTML、Markdown、JSON、DocTags 等 | 不輸出 DOCX、PPTX；技術報告的最佳模型 heron-101 mAP 約 78%；類別以 DocLayNet 為基礎，不分照片／插畫；文件未提海報 | 已查證（權重標籤經搜尋讀官方模型庫） | S43 S45 S46 S47 S48 S49 |
| 7 | Docling TableFormer（docling-ibm-models） | 表格結構辨識 | MIT | CDLA-Permissive-2.0（官方模型卡；某分支另寫 apache-2.0） | 可 | 可（需附授權文字） | 預測表格邏輯結構與儲存格框；輸出 OTSL 符號；有 FAST 與 ACCURATE 兩種模式（預設 ACCURATE）；可把預測對回 PDF 內的文字格（cell matching）；另有 V2 與 Granite Vision 兩種表格選項 | 「是否支援無框線表格」沒有找到官方明文；合併儲存格（跨列、跨欄）支援只在第三方文件找到 | 已查證（授權）；能力部分查證 | S44 S46 S50 |
| 8 | DocLayNet（資料集） | 版面偵測訓練資料 | 不適用 | CDLA-Permissive-1.0（資料授權） | 可用於訓練商用模型 | 不適用 | 80,863 頁人工標註；6 種文件類別（財報、論文、法規、政府標案、手冊、專利）；11 類標籤 | 不含海報、簡報、雜誌 | 已查證 | S17 S18 S84 |
| 9 | DocLayout-YOLO | 版面偵測 | **AGPL-3.0** | 官方未另行標示；第三方模型卡多標 AGPL-3.0 | 排除 | 排除 | 以 YOLO-v10 與 ultralytics 為基礎；DocStructBench 模型 10 類（title、plain text、abandon、figure、figure_caption、table、table_caption、table_footnote、isolate_formula、formula_caption） | AGPL；是否有可購買的商業授權未查到；預訓練資料 DocSynth300K 未標示授權 | 已查證（授權） | S1 S2 S3 S4 |
| 10 | Ultralytics YOLO 系（含以其訓練的 DocLayNet 社群模型） | 物件偵測框架 | AGPL-3.0，或 Ultralytics Enterprise License（付費） | 同程式碼 | 需付費（Enterprise License） | 需付費（Enterprise License） | 通用偵測與分割；README 提到的最新模型是 YOLO26 | 不買商業授權就是 AGPL；價格未查到 | 已查證 | S5 |
| 11 | LayoutParser | 版面分析工具組與模型庫 | Apache-2.0 | 未標示 | 不建議 | 不建議 | 模型庫涵蓋 PubLayNet（5 類）、PrimaLayout（6 類）、NewspaperNavigator（7 類，含 Photograph、Illustration）、HJDataset、TableBank、MFD | 最後一次提交是 2022-08-06；需另裝 Detectron2；模型與資料集授權在模型庫文件中都沒有寫 | 已查證（程式碼授權、維護狀態）；權重未查證 | S25 S26 S27 |
| 12 | Detectron2（框架） | 偵測與分割訓練框架 | Apache-2.0 | 官方模型庫權重：CC BY-SA 3.0 | 可（框架）；官方權重有相同方式分享條款 | 可（框架） | Mask R-CNN、Cascade R-CNN 等 | 只是框架，文件版面模型要另找或自訓 | 已查證 | S28 S29 |
| 13 | DiT（microsoft/unilm） | 文件影像骨幹；版面偵測微調權重 | 原始庫根目錄為 MIT；DiT 的 README 沒有授權段落 | 未標示 | 未確認 | 未確認 | 在 IIT-CDIP 上自監督預訓練；版面權重在 PubLayNet 微調、表格偵測在 ICDAR 2019 cTDaR 微調 | 授權不明確；只有論文類版面 | 未查證 | S30 S31 |
| 14 | SAM（Segment Anything 第一代） | 通用分割 | Apache-2.0 | Apache-2.0（README：「The model is licensed under the Apache 2.0 license」） | 可 | 可 | 以點、框提示切出任意物件遮罩 | 不分類別；SA-1B 資料集本身是研究授權（只影響下載資料集，不影響權重授權） | 已查證 | S32 |
| 15 | SAM 2／SAM 2.1 | 通用分割（影像與影片） | Apache-2.0（另含 BSD-3-Clause 元件） | Apache-2.0 | 可 | 可 | 影像與影片分割；SAM 2.1 於 2024-09-30 發布 | 不分類別；最後一則更新是 2024-12-11；示範程式的字型為 SIL OFL 1.1 | 已查證 | S33 S87 |
| 16 | SAM 3／SAM 3.1 | 以文字或範例提示做概念分割 | SAM License（Meta 自訂，2025-11-19 版） | SAM License；檢查點需在 Hugging Face 申請 | 需法務確認 | 需法務確認 | 可用短文字或範例圖「切出所有符合概念的實例」；SAM 3.1 於 2026-03-27 發布 | 不是標準開源授權；條文沒有寫明「可商用」，第三方摘要解讀為可商用；散布時須附上協議；禁止軍事、核能、受制裁對象等用途；Meta 可終止 | 已查證（授權名稱與條款）；商用與散布結論待法務 | S34 S35 S36 S85 |
| 17 | FastSAM | 快速分割 | 原始庫側欄標 AGPL-3.0 | README 寫「模型為 Apache 2.0」 | 排除 | 排除 | 以 YOLO 為基礎的快速分割 | 側欄與 README 的授權標示互相矛盾 | 未查證（來源矛盾） | S77 |
| 18 | MinerU | PDF 轉 Markdown／JSON 的解析工具 | MinerU Open Source License（以 Apache 2.0 為基礎加附加條件；2026-04-17 起） | 4.x 模型包未查到；MinerU2.5 VLM 被第三方列為 AGPL-3.0 | 有條件 | 需法務確認 | 4.0（2026-09-16）改用 PP-DocLayoutV2、PP-OCRv6、PP-FormulaNet plus-M；品質分四級（flash、basic、standard、advanced）；最新為 4.2.3 | 自訂授權；月活躍使用者超過 1 億或月營收超過 2,000 萬美元須另購授權；提供線上服務須明顯標示使用 MinerU；違反即自動終止；2026 年 3 到 4 月授權來回改了四次；輸出是 Markdown／JSON | 部分查證（程式碼授權已查證，權重未查證） | S37 S38 S39 S40 S41 S42 |
| 19 | Marker／Surya | PDF 轉 Markdown／JSON／HTML；版面、表格、OCR | Apache-2.0（Marker 於 2026-07-17 的提交改為 Apache 2.0） | 修改版 AI Pubs Open RAIL-M | 需付費（對本產品而言） | 需付費 | Surya：版面分析含閱讀順序，表格輸出 HTML 並含合併儲存格；Marker：輸出 Markdown、JSON、HTML、Chunks | 權重免費範圍只限研究、個人、以及前一年營收與募資都不超過 500 萬美元者；另禁止用於與授權方產品競爭的產品或服務；README 自述「很複雜的版面、巢狀表格與表單可能失敗」 | 已查證 | S52 S53 S54 S55 S56 S57 |
| 20 | Unstructured／unstructured-inference | RAG 用的文件切塊工具 | Apache-2.0 | 版面模型 `unstructuredio/yolo_x_layout` 未標示授權；表格用 TATR（MIT） | 可（程式碼）；版面權重未確認 | 版面權重未確認 | 版面：YOLOX（11 類，與 DocLayNet 標籤相同）或 Detectron2；表格：`microsoft/table-transformer-structure-recognition` | 目標是擷取內容給語言模型，不是版面重建；官方 README 自己列出開源版表格內容準確度 0.426，商用 API 為 0.866 | 部分查證 | S58 S59 S60 S61 S62 |
| 21 | RapidAI TableStructureRec | 表格結構辨識（有線、無線） | Apache-2.0 | 個別模型未標示（來自阿里達摩院讀光，經 ModelScope） | 未確認 | 未確認 | 有線：unet、cycle_center_net；無線：LORE；附表格分類器；輸出 HTML 與邏輯座標 | 個別模型授權未標示；README 沒有明說支援合併儲存格 | 部分查證 | S67 |
| 22 | UniTable | 表格結構、儲存格框、儲存格內容 | MIT | 未確認 | 未確認 | 未確認 | 一個架構做三件事；論文用 PubTabNet、SynthTabNet、FinTabNet、ICDAR 2019 B2 Modern、PubTables-1M | 權重授權與各訓練資料授權未逐一確認 | 部分查證 | S68 |
| 23 | RT-DETR／RT-DETRv2、D-FINE、YOLOX（自行訓練用的偵測器基底） | 物件偵測基底 | 皆為 Apache-2.0 | 依預訓練資料而定 | 可 | 可 | 可用 DocLayNet 加自有資料訓練出符合本產品分類的模型 | D-FINE 的 README 提醒：以 Objects365 預訓練的檢查點可能另受 Objects365 條款約束 | 已查證（程式碼授權） | S63 S64 S65 |
| 24 | LayerD（CyberAgent） | 把點陣設計圖分解成圖層 | Apache-2.0 | 去背模型 `cyberagent/layerd-birefnet`：Apache-2.0 | 可 | 可 | 以 BiRefNet 去背反覆抽出前景圖層，輸出 RGBA 圖層 | 原始庫已於 2026-10-30 封存為唯讀；OCR 支援標示為「即將推出」；補洞模型（simple-lama-inpainting）的授權未確認 | 已查證（授權）；補洞模型未查證 | S69 S70 S71 |
| 25 | Qwen-Image-Layered | 把影像分解成多個 RGBA 圖層 | Apache-2.0 | Apache-2.0 | 可 | 可（模型大，實務上只適合伺服器端） | 擴散模型；可指定圖層數（範例為 3、4、8 層）；可遞迴分解 | 需要 GPU；生成式模型會重畫像素，對「文字正確」與「版面比例一致」的影響未實測 | 已查證 | S72 S73 |
| 26 | Docling DocumentFigureClassifier | 圖片類型分類 | 不適用（只有權重） | v1 與 v2.5：MIT；v2.0：Apache-2.0 | 可 | 可 | 以 EfficientNet-B0 微調；可分辨圖表類型、流程圖、自然影像、標誌、簽名等（類別清單來自第三方文件鏡像） | 類別清單未對照官方模型卡；是否能分「照片／插畫／向量圖」需實測 | 部分查證 | S51 |

### 訓練資料集授權一覽

| 資料集 | 授權 | 對商用模型的影響 | 查證狀態 | 來源 |
|---|---|---|---|---|
| DocLayNet | CDLA-Permissive-1.0 | 可用於訓練商用模型 | 已查證（直接讀 LICENSE） | S18 |
| PubLayNet | 標註：CDLA-Permissive-1.0；影像：PMC Open Access Subset 使用條款（來源為可商用子集） | 可 | 已查證 | S78 S79 |
| PubTables-1M | CDLA-Permissive-2.0（Hugging Face 標籤） | 可 | 已查證（經搜尋讀資料集卡） | S22 |
| FinTabNet／FinTabNet.c | 原版：CDLA-Permissive（版本未標）；FinTabNet.c：CDLA-Permissive-2.0 | 可 | 部分查證 | S23 |
| M6Doc | CC BY-NC-ND 4.0，僅限非商用研究，且需申請 | 不可用於商用模型 | 已查證（經搜尋讀官方原始庫） | S80 |
| DocSynth300K | 未標示 | 未知 | 未查證 | S1 S3 |
| SA-1B | SA-1B Dataset Research License（下載資料集時的條款） | 只影響自行下載資料集；SAM 權重本身是 Apache-2.0 | 已查證 | S32 |
| Crello | CDLA-Permissive-2.0，另須同意 VistaCreate 授權協議 | 需另讀 VistaCreate 條款 | 部分查證 | S81 |
| Detectron2 官方模型庫權重 | CC BY-SA 3.0 | 相同方式分享條款，避免直接拿來當產品權重 | 已查證 | S29 |
| IIT-CDIP（DiT 預訓練資料） | 未查 | 未知 | 未查證 | S30 |

## 3. 逐條事實與來源

### 3.1 DocLayout-YOLO 與 Ultralytics

1. **DocLayout-YOLO 的授權是 AGPL-3.0。** 官方原始庫的 LICENSE 檔開頭是「GNU AFFERO GENERAL PUBLIC LICENSE Version 3, 19 November 2007」，原始庫側欄也標示 AGPL-3.0。［已查證；S1、S2］
2. **它建立在 YOLO-v10 與 ultralytics 之上。** README 致謝段：「The code base is built with ultralytics and YOLO-v10.」［已查證；S1］
3. 釋出的通用模型在 DocStructBench 上微調；DocSynth300K 是預訓練用的合成資料（約 113 GB），以 Mesh-candidate BestFit 演算法產生。README 沒有任何授權段落。［已查證；S1］
4. DocStructBench 模型輸出 10 類：title、plain text、abandon、figure、figure_caption、table、table_caption、table_footnote、isolate_formula、formula_caption。此清單來自第三方轉存的設定檔，不是官方頁面。［部分查證；S4］
5. **第三方轉存版本的授權標示不一致**：搜尋摘要指出有的 Hugging Face 轉存標 Apache 2.0，有的 GitHub 分支標 MIT（這兩個頁面本次沒有逐一開啟）。這些都不是官方授權，應以官方 LICENSE（AGPL-3.0）為準。［來源矛盾；S1、S2、S88、S89］
6. 論文提到 M6Doc「因著作權限制而未開源」。DocSynth300K 的元素池是否取自 M6Doc，本次沒有查到明文。［未查證；S3］
7. **Ultralytics 採雙授權**：AGPL-3.0，或 Ultralytics Enterprise License。README 對後者的描述是：可把 Ultralytics 軟體與模型整合進商業產品與服務，「bypassing the open-source requirements of AGPL-3.0」。README 提到的最新模型是 YOLO26。［已查證；S5］
8. 以 Ultralytics 訓練的 DocLayNet 社群模型授權不一（搜尋看到同類模型有標 AGPL-3.0 的，也有標 MIT 的）。標 MIT 的社群模型若實際用 Ultralytics 訓練，授權標示是否成立有疑問，本次未逐一查證。［未查證］

### 3.2 PaddleOCR、PP-DocLayout、PP-StructureV3

9. **PaddleOCR 原始庫是 Apache-2.0。** LICENSE 檔為 Apache License Version 2.0；README 寫「This project is released under the Apache 2.0 license」。PaddleX（release/3.7）的 LICENSE 同為 Apache-2.0。［已查證；S6、S7、S12］
10. **版本時間線**（README）：2025-10-16 的 3.3.0 推出 PaddleOCR-VL；2026-01-29 的 3.4.0 推出 PaddleOCR-VL-1.5 與 PP-DocLayoutV3；2026-04-21 的 3.5.0 加入可切換的推論後端與 DOCX 匯出；2026-05-28 的 3.6.0 推出 PaddleOCR-VL-1.6；2026-06-11 的 3.7.0 推出 PP-OCRv6。［已查證；S7］
11. **PP-DocLayoutV3**（PaddleX 3.7 文件）：25 類，分別是 abstract、algorithm、aside text、chart、content、display formula、document title、figure title、footer、footer image、footnote、formula number、header、header image、image、inline formula、number、paragraph title、reference、reference content、seal、table、text、vertical text、vision footnote。每個偵測結果帶 `polygon_points`（實例分割輪廓點）與 `order`（閱讀順序，從 0 起算）。模型 126 MB，DETR 架構、PPHGNetV2-L 骨幹。訓練資料是自建資料集，含中英文論文、多欄雜誌、報紙、PPT、合約、書籍、試卷、研究報告。文件沒有列 mAP。［已查證；S11］
12. PP-DocLayoutV3 的 Hugging Face 模型卡授權標籤是 `apache-2.0`；模型卡說它能直接預測多點框（不是兩點矩形框），並在一次前向運算中決定歪斜、彎曲頁面的閱讀順序。Hugging Face transformers 文件有 `pp_doclayout_v3` 頁面，另有社群 ONNX 轉存，表示不一定要綁 PaddlePaddle 執行環境。［已查證（經搜尋讀官方模型卡，未直接開啟）；S13、S14、S82］
13. **PP-DocLayoutV2**（PaddleOCR 文件）：25 類；mAP(0.5) 81.4%；203.8 MB；以 PP-DocLayout_plus-L（RT-DETR-L）為基礎，串接 6 層 Transformer 的指標網路還原閱讀順序；只輸出矩形框。［已查證；S10］
14. **PaddleOCR 文件自相矛盾**：`layout_analysis.en.md` 寫「此模組只支援 PP-DocLayoutV2」，但同一頁的範例與速度表用的是 PP-DocLayoutV3。PaddleX 3.7 的同名文件則完整描述了 V3。［來源矛盾；S10、S11］
15. **PP-DocLayout_plus-L**：20 類，mAP 83.2，訓練資料為自建資料集，含「Chinese and English papers, PPT, multi-layout magazines, contracts, books, exams, ancient books and research reports」。**PP-DocLayout-L**：23 類，mAP 90.4。另有 M、S 兩個輕量版，以及 3 類（table、image、stamp）與 17 類的舊模型。各模型的評測集不同，mAP 不能互相比較。［已查證；S8、S9］
16. **沒有任何 PP-DocLayout 文件提到海報。** 有提到 PPT 與雜誌。［已查證（文件中確實沒有）；S8、S9、S11］
17. **PP-StructureV3 的表格部分**：表格分類 PP-LCNet_x1_0_table_cls（Top-1 94.2%）；結構辨識 SLANeXt_wired 與 SLANeXt_wireless（共用準確率 69.65）；儲存格偵測 RT-DETR-L_wired_table_cell_det 與 RT-DETR-L_wireless_table_cell_det（共用 mAP 82.7）。［已查證；S8］
18. **表格結構辨識輸出**：`structure` 是 HTML 符號序列，合併儲存格以 `<td>` 的屬性表示，文件範例含 `colspan="4"`；文件沒有 `rowspan` 的範例。文件註明 SLANeXt 系列的儲存格座標預測無效。準確率：SLANet 59.52、SLANet_plus 63.69、SLANeXt 69.65。［已查證；S15］
19. PP-StructureV3 其他模組：公式（PP-FormulaNet 系列、UniMERNet）、圖表轉表格（PP-Chart2Table）、影像去扭曲（UVDoc）、文件方向與文字行方向分類、印章文字偵測。［已查證；S8］
20. PP-DocLayout_plus-L、SLANeXt、RT-DETR-L 儲存格偵測等模型在 Hugging Face 上的個別授權標籤，本次搜尋沒有取得。［未查證］

### 3.3 DocLayNet

21. **DocLayNet 的授權是 CDLA-Permissive-1.0。** 官方原始庫的 LICENSE 檔標題為「Community Data License Agreement – Permissive – Version 1.0」。［已查證；S18］
22. 80,863 頁；6 種文件類別：financial_reports、scientific_articles、laws_and_regulations、government_tenders、manuals、patents；11 類標籤。［已查證；S17］
23. 11 類為 Caption、Footnote、Formula、List-item、Page-footer、Page-header、Picture、Section-header、Table、Text、Title。此清單來自 docling-models 模型卡對版面模型的描述。［已查證（經搜尋）；S50］
24. Hugging Face 上不同鏡像的授權欄位不一致：早期 ds4sd 鏡像標「other」並連到 CDLA 條文，後來有修正為 `cdla-permissive-1.0` 的合併請求。條文本身一致。［已查證（經搜尋）；S84］

### 3.4 Docling

25. **Docling 程式碼是 MIT。** README：「The Docling codebase is under MIT license. For individual model usage, please refer to the model licenses found in the original packages.」［已查證；S43］
26. **預設版面模型是 Heron。** 原始碼定義六個版面模型：`docling_layout_v2`（已不支援，會自動改用 Heron）、`docling_layout_heron`、`docling_layout_heron_101`、`docling_layout_egret_medium`、`docling_layout_egret_large`、`docling_layout_egret_xlarge`；`LayoutOptions` 的預設值為 `DOCLING_LAYOUT_HERON`。［已查證；S45、S46］
27. Heron 的 Hugging Face 模型庫標籤為 Apache-2.0、架構 `rt_detr_v2`。技術報告說模型以 RT-DETR、RT-DETRv2、D-FINE 為基礎，訓練資料為 15 萬頁、230 萬個版面元素，最佳模型 heron-101 mAP 約 78%，並以寬鬆授權釋出。［已查證（經搜尋讀官方模型庫與論文）；S48、S49］
28. Heron 輸出 17 類（DocLayNet 的 11 類加上 document_index、code、checkbox_selected、checkbox_unselected、form、key_value_region）。此清單來自第三方量化版模型卡與第三方 Rust 移植碼，官方模型卡未直接確認。［部分查證；S48］
29. **TableFormer**：`docling-ibm-models` 原始庫為 MIT；README 描述為「辨識表格結構與表格內容邊界框的模組」，權重放在 Hugging Face 的 `ds4sd/docling-models`；提到的資料集有 PubTabNet、FinTabNet、TableBank。［已查證；S44］
30. `docling-models` 模型卡授權為 CDLA-Permissive-2.0；某個合併請求分支的中繼資料同時寫了 `cdla-permissive-2.0` 與 `apache-2.0`。兩者都是寬鬆授權，不影響「可商用」的結論。［已查證（經搜尋）；兩種寫法並存；S50］
31. 表格選項（原始碼）：`TableFormerMode` 有 FAST 與 ACCURATE，預設 ACCURATE；`do_cell_matching` 預設開啟，把結構預測對回 PDF 內的文字格；另有 `TableStructureV2Options`（docling_tableformer_v2）與 `GraniteVisionTableStructureOptions`（以視覺語言模型辨識表格）。［已查證；S46］
32. **Docling 不輸出 DOCX 或 PPTX。** 官方格式文件的輸出清單是 HTML、Markdown、JSON、DocLang XML、純文字、DocTags、WebVTT、LaTeX 等。［已查證；S47］
33. DocumentFigureClassifier：v1 與 v2.5 的模型卡標 MIT，v2.0 標 Apache-2.0，基底模型為 EfficientNet-B0。［已查證（經搜尋讀官方模型卡）；S51］

### 3.5 Table Transformer（TATR）

34. **TATR 程式碼是 MIT。** LICENSE 檔：「MIT License / Copyright (c) Microsoft Corporation.」［已查證；S20］
35. **原始庫已封存。** 頁面顯示「This repository was archived by the owner on Sep 21, 2026. It is now read-only.」最近一則消息是 2023-08-22 釋出 v1.1 模型。［已查證；S19］
36. 權重：偵測模型（DETR R18）在 PubTables-1M 訓練；結構模型 TATR-v1.0 與 v1.1-Pub 在 PubTables-1M、v1.1-Fin 在 FinTabNet.c、v1.1-All 在兩者合併資料上訓練。［已查證；S19］
37. Hugging Face 上 `microsoft/table-transformer-structure-recognition-v1.1-all` 的授權標籤為 MIT。［已查證（經搜尋讀官方模型卡）；S21］
38. 結構模型的 6 個類別：table、table column、table row、table column header、table projected row header、table spanning cell。清單來自引用該模型的設定檔副本，不是微軟自己的頁面。［部分查證；S24］
39. PubTables-1M 在 Hugging Face 的授權標籤為 CDLA-Permissive-2.0，資料取自 PubMed Open Access 的可商用子集。FinTabNet.c 標 CDLA-Permissive-2.0；其資料集卡說原版 FinTabNet 依 IBM 網站為 CDLA-Permissive。［已查證（經搜尋）；S22、S23］

### 3.6 LayoutParser、Detectron2、DiT

40. **LayoutParser 是 Apache-2.0，但已停止更新。** 主分支最近五次提交在 2022-04-03 到 2022-08-06 之間，最後一次是 2022-08-06 的「Better Automodel Design (#146)」。［已查證；S25、S27］
41. 模型庫的資料集與類別：HJDataset（7 類）、PubLayNet（Text、Title、List、Table、Figure）、PrimaLayout（TextRegion、ImageRegion、TableRegion、MathsRegion、SeparatorRegion、OtherRegion）、NewspaperNavigator（Photograph、Illustration、Map、Comics/Cartoon、Editorial Cartoon、Headline、Advertisement）、TableBank（Table）、MFD（Equation）。模型庫文件沒有任何關於資料集或模型授權的文字。［已查證；S26］
42. **Detectron2 是 Apache-2.0。** 但官方模型庫的權重另有授權：「All models available for download through this document are licensed under the Creative Commons Attribution-ShareAlike 3.0 license.」［已查證；S28、S29］
43. DiT：README 沒有授權段落；`microsoft/unilm` 根目錄的 LICENSE 是 MIT。預訓練資料為 IIT-CDIP Test Collection 1.0；版面權重在 PubLayNet 微調，表格偵測權重在 ICDAR 2019 cTDaR 微調。DiT 權重可否商用，本次無法確認。［未查證；S30、S31］

### 3.7 Segment Anything 系列

44. **SAM 第一代：程式碼與模型都是 Apache-2.0。** README：「The model is licensed under the Apache 2.0 license.」側欄標 Apache-2.0。SA-1B 資料集另受「SA-1B Dataset Research License」約束。［已查證；S32］
45. **SAM 2／2.1：Apache-2.0。** README：SAM 2 的模型檢查點、示範程式（前後端）與訓練程式碼皆為 Apache 2.0；示範程式用的 Inter Font 與 Noto Color Emoji 為 SIL Open Font License 1.1；側欄另顯示 BSD-3-Clause（來自 cc_torch 的連通元件演算法）。LICENSE 檔為 Apache License 2.0。SAM 2.1 於 2024-09-30 發布，最後一則更新是 2024-12-11。［已查證；S33、S87］
46. **SAM 3／3.1 使用 Meta 自訂的「SAM License」**（最後更新 2025-11-19），不是 Apache-2.0。README：「This project is licensed under the SAM License」。檢查點需在 Hugging Face 申請取用。SAM 3.1 Object Multiplex 於 2026-03-27 發布。［已查證；S34、S35］
47. SAM License 的重點（讀自 LICENSE 檔）：授予非專屬、全球、不可轉讓、免權利金的有限授權；再散布時必須附上協議；禁止軍事、核能、受 ITAR 管制、受制裁對象等用途；禁止逆向工程；Meta 可在違約時終止；對 Meta 提起智財訴訟則授權終止。**條文沒有出現「commercial」字樣。**［已查證；S35］
48. Roboflow 的授權摘要解讀為「免權利金且允許商用」，並指出 Meta 可修改條款、繼續使用即視為接受。這是第三方解讀，不是法律意見。［部分查證；S36、S85］
49. SAM 3 能以短文字或範例「切出所有符合某概念的實例」，功能上最貼近「找出頁面上所有照片／圖示」的需求，但授權需法務確認後才能列入設計。［能力已查證，可用性待確認；S34］
50. FastSAM：原始庫側欄標 AGPL-3.0，README 卻寫「The model is licensed under the Apache 2.0 license」。兩者矛盾，本產品應排除。［來源矛盾；S77］

### 3.8 MinerU

51. **MinerU 目前的授權是「MinerU Open Source License」**，README：「This repository is licensed under the MinerU Open Source License, based on Apache 2.0 with additional conditions.」［已查證；S37］
52. **附加條件**（讀自 LICENSE.md）：允許商用且不需另購授權；但本人與關係企業合併計算，月活躍使用者超過 1 億，或月營收超過 2,000 萬美元，就必須先向 MinerU 團隊取得授權；以 MinerU 為基礎對第三方提供線上服務時，必須在產品介面或公開文件中清楚、明顯地標示使用了 MinerU；未遵守即自動終止授權。［已查證；S38］
53. **授權在 2026 年春天來回改了四次**（LICENSE.md 提交紀錄）：2026-03-20 由 AGPL-3.0 改為 Apache 2.0；2026-03-28 由 Apache 2.0 改回 AGPLv3；2026-04-14 由 AGPL-3.0 改為 Apache-2.0；2026-04-17 改為 MinerU Open Source License 並補充商用與標示條款。［已查證；S39］
54. 第三方文章稱授權變更隨 3.1.0 版生效，並提醒不同版本的模型權重可能適用不同條款。［部分查證；S86］
55. **MinerU 4.0.0（2026-09-16）的模型**：版面 PP-DocLayoutV2、OCR 為 PP-OCRv6 Tiny Det + Small Rec、公式 PP-FormulaNet plus-M；模型包為 MinerU-4_models_onnx 與 MinerU-4_models_torch；3.x 的 pipeline／vlm／hybrid 後端改為 flash、basic、standard、advanced 四個品質等級。也就是說，MinerU 現在的版面偵測用的是 PaddleOCR 的模型，不再是 DocLayout-YOLO。［已查證；S40］
56. 最新版本為 4.2.3（發布頁顯示 11 月 16 日）。［已查證；S41］
57. MinerU2.5 VLM 權重（`opendatalab/MinerU2.5-2509-1.2B`）被第三方模型目錄列為 AGPL-3.0；MinerU2.5-Pro 與 4.x 模型包的授權，本次沒有查到官方標示。［未查證；S42］

### 3.9 Marker 與 Surya

58. **Marker 程式碼現在是 Apache-2.0。** LICENSE 檔為 Apache License 2.0；LICENSE 的提交紀錄顯示 2026-07-17 有一筆「Release prep: … Apache 2.0 …」的提交。README：「Our code is licensed under Apache 2.0 — free to use, including commercially.」［已查證；S52、S53、S55］
59. **模型權重另有授權**：「AI PUBS OPEN RAIL-M LICENSE (MODIFIED)」。README：「free for research, personal use, and startups under $5M funding/revenue」。［已查證；S54、S56］
60. MODEL_LICENSE 附件 A 第 2 條的限制：前一年總營收超過 500 萬美元、或累計募資（股權或債務）超過 500 萬美元者受限；**另外，若你提供的產品或服務與授權方的產品或服務競爭，也受限**；個人與研究用途不受金額上限約束；商業授權可向 Datalab 洽購。［已查證；S54］
61. **對本產品的判讀**：Datalab 自己提供文件轉換 API，Mortise 也是文件轉換服務，很可能落入「競爭產品」條款。這是判讀，不是法律結論；要用 Marker 或 Surya 的權重，應視為必須付費取得商業授權。［判讀］
62. Surya 的授權與 Marker 相同（程式碼 Apache 2.0、權重為修改版 Open RAIL-M、門檻 500 萬美元）。能力：版面分析含閱讀順序；表格辨識輸出 HTML 並含合併儲存格；版面標籤有 Caption、Footnote、Equation、ListGroup、PageHeader、PageFooter、Picture、SectionHeader、Table、Text、Figure、Code、Form、TableOfContents、ChemicalBlock、Diagram、Bibliography、BlankPage。［已查證；S57］
63. Marker 的 README 自述限制：「Very complex layouts, with nested tables and forms, may not work」。輸出格式為 Markdown、JSON、HTML、Chunks。［已查證；S56］

### 3.10 Unstructured

64. `unstructured` 與 `unstructured-inference` 都是 Apache-2.0。［已查證；S58、S59］
65. 版面模型：`unstructured-inference` 提供 Detectron2（LayoutParser 模型庫）與 YOLOX；YOLOX 權重從 Hugging Face 的 `unstructuredio/yolo_x_layout` 下載，標籤 11 類，與 DocLayNet 的 11 類相同。程式碼沒有寫訓練資料來源。［已查證；S60］
66. 表格模型預設為 `microsoft/table-transformer-structure-recognition`。［已查證；S61］
67. `unstructuredio/yolo_x_layout` 的模型卡在 2024 年 8 月有使用者開討論串要求補上授權資訊，顯示當時沒有授權欄位；現況未確認。［未查證；S62］
68. 官方 README 自己比較開源版與商用 API：表格儲存格內容準確度開源版 0.426、商用 Transform API 0.866。開源版的定位是替語言模型擷取內容，不是版面重建。［已查證；S58］

### 3.11 其他表格模型

69. RapidAI TableStructureRec：Apache-2.0。有線表格用 `wired_table_rec`（unet 或 cycle_center_net），無線表格用 `lineless_table_rec`（LORE），另有表格分類器。模型來自阿里達摩院讀光系列（經 ModelScope）；README 沒有個別模型的授權說明。［部分查證；S67］
70. UniTable：MIT。可輸出表格結構、儲存格內容與儲存格邊界框；論文使用 PubTabNet、SynthTabNet、FinTabNet、ICDAR 2019 B2 Modern、PubTables-1M 五個資料集。［已查證（程式碼授權）；S68］

### 3.12 自行訓練用的偵測器基底

71. RT-DETR（含 RT-DETRv2）：Apache-2.0。D-FINE：Apache-2.0，README 提醒以 Objects365 訓練或預訓練的檢查點「may be subject to the Objects365 dataset terms」。YOLOX：Apache-2.0。Grounding DINO（開放詞彙偵測）：Apache-2.0。［已查證（原始庫側欄）；S63、S64、S65、S66］

### 3.13 海報與圖層分解

72. **LayerD**（CyberAgent，ICCV 2025）：Apache-2.0。以 BiRefNet 為基礎的去背模型反覆抽出圖層，輸出 RGBA 圖層。原始庫頁面顯示「archived by the owner on Oct 30, 2026」。去背權重 `cyberagent/layerd-birefnet` 為 Apache-2.0（原始 BiRefNet 為 MIT）。建置時還會下載補洞模型 `eneshahin/simple-lama-inpainting`，其授權未確認。［已查證（授權與封存狀態）；S69、S70、S71］
73. **Qwen-Image-Layered**：Apache-2.0（README：「Qwen-Image-Layered is licensed under Apache 2.0」）。把一張影像分解成多個 RGBA 圖層，可指定層數並遞迴分解；2025-12-19 釋出權重。它是擴散模型，會重新生成像素。［已查證；S72、S73］
74. 2026 年的相關研究（只確認論文存在，程式碼授權未查）：ReDesign（KAIST，以代理人組合視覺工具，從點陣圖還原可編輯的圖層階層，並釋出 909 份 Figma 設計的評測集）、DrawAI（把點陣圖轉成 SVG 或 PPTX 的評測與流程）、Back2Struct。這些與本產品的目標高度重疊，值得設計階段追蹤。［部分查證；S74、S75、S76］
75. Crello 資料集（VistaCreate 設計範本的向量圖層資料）：Hugging Face 標 CDLA-Permissive-2.0，但資料集卡同時要求使用者同意 VistaCreate 授權協議，且散布者聲明不擁有原始範本的著作權。若要拿來合成海報訓練資料，需先讀 VistaCreate 條款。［部分查證；S81］

## 4. 對 Mortise 架構的含意

以下是依據上面事實做的判讀，不是查證結果。

1. **版面偵測應做成可抽換的介面。** 首選 PP-DocLayoutV3，備選 Docling Heron。兩者類別不同（25 類對 17 類），要在介面層統一對應到本產品自己的分類。
2. **本產品的六種分類需要第二階段。** 版面模型只會說「這是一張圖」；要分出照片、插畫、向量圖形，需要再接一個圖片類型分類器（可從 Docling DocumentFigureClassifier 起步，再用自有資料微調），或用原生 PDF 的物件類型直接判斷（原生 PDF 的向量路徑與點陣影像本來就分開）。
3. **海報走另一條路徑。** 文件型模型對「字壓在圖上」沒有對應的類別。建議流程：OCR 文字偵測產生文字遮罩 → SAM 2.1 切出物件遮罩 → 分類器判斷類型 → 圖層分解或補洞產生乾淨底圖。LayerD 與 Qwen-Image-Layered 可當實驗對照組。
4. **表格辨識分兩條路。** 原生 PDF：先用向量線條與文字座標的規則法，準確且可重現。影像與掃描：PaddleOCR 表格模型組為主，TATR 交叉驗證；兩者結果不一致時，在還原度報告標示低信心。
5. **桌面版的散布條件。** 首選組合全是 Apache-2.0 或 MIT，只需隨附授權文字與 NOTICE。CDLA-Permissive 權重需隨附協議文字（條文細節本次未逐條閱讀）。
6. **自建評測集是必要工作。** 沒有任何公開數字能回答「這些模型在海報上表現如何」。

## 5. 查不到的事

1. **Hugging Face 頁面無法直接開啟。** 所有模型卡、資料集卡的授權欄位都是經搜尋工具取得。以下模型的個別授權標籤沒有取得：PP-DocLayout_plus-L、PP-DocLayout-L／M／S、SLANeXt_wired／wireless、RT-DETR-L_wired／wireless_table_cell_det、PP-LCNet_x1_0_table_cls。
2. **PP-DocLayout 系列的訓練資料來源。** 官方只說是「自建資料集」，沒有公開資料來源與授權。權重由百度以 Apache-2.0 釋出，但訓練資料的權利狀態無從查證。
3. **任何版面模型在海報、字壓在圖上、元素重疊情境的準確度。** 沒有找到公開評測。
4. **DocLayout-YOLO 是否提供可購買的商業授權。** 沒有找到。另外即使向 Ultralytics 買了 Enterprise License，DocLayout-YOLO 自己與 YOLO-v10 的 AGPL 程式碼是否涵蓋在內，也沒有找到說明。
5. **DocSynth300K 的授權**，以及它的元素池是否取自 M6Doc（CC BY-NC-ND 4.0，非商用）。
6. **MinerU 4.x 模型包與 MinerU2.5-Pro 權重的授權。** 官方模型卡沒有讀到；只有第三方把 MinerU2.5 列為 AGPL-3.0。
7. **SAM License 是否允許本產品的商用情境，以及能否把需申請的檢查點隨桌面版散布。** 條文沒有「commercial」字樣，需法務判讀。
8. **Ultralytics Enterprise License 與 Datalab 商業授權的價格。** 官網無法開啟。
9. **`unstructuredio/yolo_x_layout` 的權重授權與訓練資料。**
10. **RapidAI TableStructureRec 內各模型（達摩院讀光 cycle-centernet、LORE）的授權。**
11. **UniTable 權重的授權。**
12. **DiT 權重可否商用。** README 沒有授權段落；IIT-CDIP 資料集的授權也沒有查。
13. **LayoutParser 模型庫各權重的授權**，以及 PRImA、HJDataset、TableBank、D4LA、DocBank 資料集的授權。
14. **Heron 的 17 類清單與 TATR 的 6 類清單**都只有第三方來源，官方模型卡未直接確認。
15. **TableFormer 對無框線表格與合併儲存格的官方能力聲明。**
16. **LayerD 使用的補洞模型（simple-lama-inpainting）的授權。**
17. **ReDesign、DrawAI、Back2Struct 的程式碼是否釋出、採用何種授權。**
18. **CDLA-Permissive-1.0 與 2.0 的條文細節**（例如再散布時的義務），本次只確認了授權名稱。
19. **Docling DocumentFigureClassifier 的完整類別清單。**
20. **PP-DocLayoutV3 的 mAP 與 PaddleOCR 文件中「只支援 V2」的矛盾何者為準。**

## 6. 來源清單

| 代號 | 內容 | 網址 |
|---|---|---|
| S1 | DocLayout-YOLO 原始庫 | https://github.com/opendatalab/DocLayout-YOLO |
| S2 | DocLayout-YOLO LICENSE | https://raw.githubusercontent.com/opendatalab/DocLayout-YOLO/main/LICENSE |
| S3 | DocLayout-YOLO 論文 | https://arxiv.org/abs/2410.12628 |
| S4 | DocStructBench 類別（第三方轉存設定檔） | https://huggingface.co/anyformat/doclayout-yolo-docstructbench/blob/main/config.json |
| S5 | Ultralytics README（授權段） | https://raw.githubusercontent.com/ultralytics/ultralytics/main/README.md |
| S6 | PaddleOCR LICENSE | https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/LICENSE |
| S7 | PaddleOCR README | https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/README.md |
| S8 | PP-StructureV3 文件 | https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/docs/version3.x/pipeline_usage/PP-StructureV3.en.md |
| S9 | PaddleOCR 版面偵測模組文件 | https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/docs/version3.x/module_usage/layout_detection.en.md |
| S10 | PaddleOCR 版面分析模組文件 | https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/docs/version3.x/module_usage/layout_analysis.en.md |
| S11 | PaddleX 3.7 版面分析模組文件 | https://raw.githubusercontent.com/PaddlePaddle/PaddleX/release/3.7/docs/module_usage/tutorials/ocr_modules/layout_analysis.en.md |
| S12 | PaddleX LICENSE | https://raw.githubusercontent.com/PaddlePaddle/PaddleX/release/3.7/LICENSE |
| S13 | PP-DocLayoutV3 模型卡 | https://huggingface.co/PaddlePaddle/PP-DocLayoutV3 |
| S14 | PaddleOCR-VL-1.5 論文 | https://arxiv.org/abs/2601.21957 |
| S15 | PaddleOCR 表格結構辨識模組文件 | https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/main/docs/version3.x/module_usage/table_structure_recognition.en.md |
| S17 | DocLayNet 原始庫 | https://github.com/DS4SD/DocLayNet |
| S18 | DocLayNet LICENSE | https://raw.githubusercontent.com/DS4SD/DocLayNet/main/LICENSE |
| S19 | Table Transformer 原始庫 | https://github.com/microsoft/table-transformer |
| S20 | Table Transformer LICENSE | https://raw.githubusercontent.com/microsoft/table-transformer/main/LICENSE |
| S21 | TATR v1.1-all 模型卡 | https://huggingface.co/microsoft/table-transformer-structure-recognition-v1.1-all |
| S22 | PubTables-1M 資料集卡 | https://huggingface.co/datasets/bsmock/pubtables-1m |
| S23 | FinTabNet.c 資料集卡 | https://huggingface.co/datasets/bsmock/FinTabNet.c |
| S24 | TATR 結構辨識模型卡 | https://huggingface.co/microsoft/table-transformer-structure-recognition |
| S25 | LayoutParser 原始庫 | https://github.com/Layout-Parser/layout-parser |
| S26 | LayoutParser 模型庫文件 | https://raw.githubusercontent.com/Layout-Parser/layout-parser/main/docs/notes/modelzoo.md |
| S27 | LayoutParser 提交紀錄 | https://github.com/Layout-Parser/layout-parser/commits/main |
| S28 | Detectron2 LICENSE | https://raw.githubusercontent.com/facebookresearch/detectron2/main/LICENSE |
| S29 | Detectron2 MODEL_ZOO | https://raw.githubusercontent.com/facebookresearch/detectron2/main/MODEL_ZOO.md |
| S30 | DiT README | https://github.com/microsoft/unilm/tree/master/dit |
| S31 | unilm LICENSE | https://raw.githubusercontent.com/microsoft/unilm/master/LICENSE |
| S32 | Segment Anything 原始庫 | https://github.com/facebookresearch/segment-anything |
| S33 | SAM 2 原始庫 | https://github.com/facebookresearch/sam2 |
| S34 | SAM 3 原始庫 | https://github.com/facebookresearch/sam3 |
| S35 | SAM 3 LICENSE | https://raw.githubusercontent.com/facebookresearch/sam3/main/LICENSE |
| S36 | Roboflow 的 SAM 3 授權摘要（第三方） | https://roboflow.com/model-licenses/segment-anything-3 |
| S37 | MinerU 原始庫 | https://github.com/opendatalab/MinerU |
| S38 | MinerU LICENSE.md | https://raw.githubusercontent.com/opendatalab/MinerU/master/LICENSE.md |
| S39 | MinerU LICENSE.md 提交紀錄 | https://github.com/opendatalab/MinerU/commits/master/LICENSE.md |
| S40 | MinerU 4.0.0 發布說明 | https://github.com/opendatalab/MinerU/releases/tag/mineru-4.0.0-released |
| S41 | MinerU 發布頁 | https://github.com/opendatalab/MinerU/releases |
| S42 | MinerU2.5 授權（第三方模型目錄） | https://docs.voxel51.com/model_zoo/models/opendatalab_MinerU2_5_2509_1_2B.html |
| S43 | Docling 原始庫 | https://github.com/docling-project/docling |
| S44 | docling-ibm-models 原始庫 | https://github.com/docling-project/docling-ibm-models |
| S45 | Docling 版面模型定義 | https://raw.githubusercontent.com/docling-project/docling/main/docling/datamodel/layout_model_specs.py |
| S46 | Docling 管線選項 | https://raw.githubusercontent.com/docling-project/docling/main/docling/datamodel/pipeline_options.py |
| S47 | Docling 支援格式文件 | https://raw.githubusercontent.com/docling-project/docling/main/docs/usage/supported_formats.md |
| S48 | Docling 版面模型技術報告 | https://arxiv.org/abs/2509.11720 |
| S49 | docling-layout-heron 模型庫 | https://huggingface.co/docling-project/docling-layout-heron |
| S50 | docling-models 模型卡 | https://huggingface.co/ds4sd/docling-models |
| S51 | DocumentFigureClassifier 模型卡 | https://huggingface.co/docling-project/DocumentFigureClassifier |
| S52 | Marker 原始庫 | https://github.com/datalab-to/marker |
| S53 | Marker LICENSE | https://raw.githubusercontent.com/datalab-to/marker/master/LICENSE |
| S54 | Marker MODEL_LICENSE | https://raw.githubusercontent.com/datalab-to/marker/master/MODEL_LICENSE |
| S55 | Marker LICENSE 提交紀錄 | https://github.com/datalab-to/marker/commits/master/LICENSE |
| S56 | Marker README | https://raw.githubusercontent.com/datalab-to/marker/master/README.md |
| S57 | Surya 原始庫 | https://github.com/datalab-to/surya |
| S58 | Unstructured 原始庫 | https://github.com/Unstructured-IO/unstructured |
| S59 | unstructured-inference 原始庫 | https://github.com/Unstructured-IO/unstructured-inference |
| S60 | unstructured-inference yolox.py | https://raw.githubusercontent.com/Unstructured-IO/unstructured-inference/main/unstructured_inference/models/yolox.py |
| S61 | unstructured-inference tables.py | https://raw.githubusercontent.com/Unstructured-IO/unstructured-inference/main/unstructured_inference/models/tables.py |
| S62 | yolo_x_layout 授權討論串 | https://huggingface.co/unstructuredio/yolo_x_layout/discussions/1 |
| S63 | RT-DETR 原始庫 | https://github.com/lyuwenyu/RT-DETR |
| S64 | D-FINE 原始庫 | https://github.com/Peterande/D-FINE |
| S65 | YOLOX 原始庫 | https://github.com/Megvii-BaseDetection/YOLOX |
| S66 | Grounding DINO 原始庫 | https://github.com/IDEA-Research/GroundingDINO |
| S67 | RapidAI TableStructureRec 原始庫 | https://github.com/RapidAI/TableStructureRec |
| S68 | UniTable 原始庫 | https://github.com/poloclub/unitable |
| S69 | LayerD 原始庫 | https://github.com/CyberAgentAILab/LayerD |
| S70 | LayerD 論文 | https://arxiv.org/abs/2509.25134 |
| S71 | layerd-birefnet 模型卡 | https://huggingface.co/cyberagent/layerd-birefnet |
| S72 | Qwen-Image-Layered 原始庫 | https://github.com/QwenLM/Qwen-Image-Layered |
| S73 | Qwen-Image-Layered 論文 | https://arxiv.org/abs/2512.15603 |
| S74 | ReDesign 論文 | https://arxiv.org/abs/2607.25565 |
| S75 | DrawAI 論文 | https://arxiv.org/abs/2608.00548 |
| S76 | Back2Struct 論文 | https://arxiv.org/abs/2609.37016 |
| S77 | FastSAM 原始庫 | https://github.com/CASIA-IVA-Lab/FastSAM |
| S78 | PubLayNet 原始庫 | https://github.com/ibm-aur-nlp/PubLayNet |
| S79 | PubLayNet LICENSE.md | https://raw.githubusercontent.com/ibm-aur-nlp/PubLayNet/master/LICENSE.md |
| S80 | M6Doc 原始庫 | https://github.com/HCIILAB/M6Doc |
| S81 | Crello 資料集卡 | https://huggingface.co/datasets/cyberagent/crello |
| S82 | transformers 的 PP-DocLayoutV3 文件 | https://huggingface.co/docs/transformers/v5.14.0/model_doc/pp_doclayout_v3 |
| S83 | PP-DocLayout 論文 | https://arxiv.org/abs/2503.17213 |
| S84 | DocLayNet 資料集卡 | https://huggingface.co/datasets/ds4sd/DocLayNet |
| S85 | SAM License 條文收錄（ScanCode LicenseDB） | https://scancode-licensedb.aboutcode.org/sam-2025-11-19.html |
| S86 | MinerU 授權變更的第三方文章 | https://toolnavs.com/article/2457-is-mineru-worth-using-the-50k-star-pdf-to-markdown-tool-has-deployment-and-licen |
| S87 | SAM 2 LICENSE | https://raw.githubusercontent.com/facebookresearch/sam2/main/LICENSE |
| S88 | DocLayout-YOLO 的第三方 Hugging Face 轉存（搜尋結果） | https://huggingface.co/collab-dr/DocLayout-YOLO-DocStructBench |
| S89 | DocLayout-YOLO 的第三方 GitHub 分支（搜尋結果） | https://github.com/chirag4862/doclayout-yolo |

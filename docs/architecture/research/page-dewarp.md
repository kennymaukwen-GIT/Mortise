# 查證紀錄：紙張攤平（彎曲、捲邊、折痕）與去陰影、去反光

- 查證日期：2026-11-23；觸發：創辦人上傳的課表照片（米色布上、上緣捲起有折痕、右側露出另一張紙、光線不均），`find_page_quad` 回傳 `full_image`、信心 0.15
- 授權底線：可商用；排除 GPL、AGPL；排除非商用權重。舊研究 [image.md](image.md) 已查過的事實只引用條號（如「image.md #12」），不重抄
- 來源等級：【直讀】親自讀了 LICENSE、README、原始碼或 PyPI 中繼資料（GitHub 庫以 `git clone` 取得後閱讀）；【搜尋摘要】搜尋工具的轉述；【第三方】非官方網站；【實測】本次在本機跑出的數字；【判讀】依事實做的推論
- 網路限制：huggingface.co、www3.cs.stonybrook.edu、arxiv.org 解析失敗，無法直讀；github.com 網頁與 API 被擋，但 `git clone` 可用；pypi.org 可用
- 實驗環境：4 核 CPU、無 GPU；Python 3.13；opencv-python-headless 5.1.0、onnxruntime 1.29.0、torch 2.15.0（只用來載入權重與匯出 ONNX）

## 0. 結論

1. **攤平這段還沒寫**：`rectify` 只做透視（單應矩陣）；`set_dewarper()` 是空的替換點，`dewarp` 預設關閉。`synth.py` 也沒有模擬紙張彎曲。【直讀】`mortise/recognition/page_geometry.py` 開頭說明與 `DEFAULT_PARAMS["dewarp"]`、`mortise/eval/synth.py` 開頭說明
2. **這張照片連透視都沒做到**：上緣捲起，所以上邊不是直線，四條直線湊不成四邊形。【實測】`find_page_quad` 的 notes：「只找到右、下、左邊（上邊缺乏邊緣支持…）」，回傳 `full_image`、信心 0.15
3. **授權乾淨、可在 CPU 上跑的學習式攤平只有 UVDoc**：程式碼與權重都是 MIT，權重就放在程式庫裡。實測 ONNX Runtime 每張約 0.3 秒。DocTr 系列是非商用；DvD 是 AGPL；其餘候選的權重沒有明文授權
4. **UVDoc 不能無條件開**：平的紙經它處理後，反而多出中位數 2～5 px、p95 4.5～13 px 的位置誤差（150 dpi 頁面像素，1 px ≈ 0.17 mm）。彎曲時它能把透視攤平後殘留的 10～15 px 中位數誤差降到約 4～6 px，但 p95 仍有 8～20 px。它的輸出只能當候選，最後的位置要由演算法驗證、精修
5. **page-dewarp 不適合**：它的曲面模型只有 z = f(x)，也就是只沿水平方向彎，表達不了上緣捲起；而且它把「整張圖減掉邊距」當成頁面。在含背景的照片上，誤差達數十 px
6. **去陰影**：現有的 `enhance`（形態學估背景後相除）對柔邊陰影已經夠用；遇到硬邊陰影，邊界會留下亮暗帶。授權乾淨的學習式去陰影模型，權重一律沒有明文授權。去反光沒有可用模型（同 image.md #29）

## 1. 傳統方法與開源實作

| 名稱（版本） | 原理 | 授權 | 適用與限制 | 來源 |
|---|---|---|---|---|
| **page-dewarp 0.3.4**（PyPI 2026-05-12） | 找文字輪廓並串成文字行，用 solvePnP 估初值，再以三次曲面加相機姿態最佳化重投影誤差，最後 remap | MIT（Matt Zucker 2016） | 曲面只有 `z = polyval([α+β, −2α−β, α, 0], x)`，只隨 x 變化，α、β 夾在 ±0.5；頁面範圍是縮圖扣掉固定邊距（預設 x 50、y 20），不偵測紙；相依 `opencv-python`（非 headless 版，內附 Qt，見 image.md #5）與 matplotlib；預設輸出二值化灰階 | 【直讀】[LICENSE](https://github.com/lmmx/page-dewarp/blob/master/LICENSE)、[projection.py](https://github.com/lmmx/page-dewarp/blob/master/src/page_dewarp/projection.py)、[說明文件 how-it-works](https://github.com/lmmx/page-dewarp/tree/master/docs/how-it-works)、[PyPI](https://pypi.org/project/page-dewarp/) |
| **Leptonica dewarp（1.88.0 開發版）** | 頁面模型：每條長文字行的中心點擬合二次曲線，得「垂直位移場」；由左右對齊的行首行尾推「水平位移場」；兩個方向再各做一次二次最小平方平滑。另有「線條模型」（`dewarpBuildLineModel`）：直接用格線（ruled lines）建兩個方向的位移場 | BSD 兩條款 | C 函式庫，輸出是位移場，由演算法量測，正合鐵律；Python 沒有現成綁定，演算法簡單，宜用 numpy 重寫【判讀】；假設文字行夠長、夠多（`MinLinesForHoriz1 = 10`） | 【直讀】[leptonica-license.txt](https://github.com/DanBloomberg/leptonica/blob/master/leptonica-license.txt)、[dewarp1.c](https://github.com/DanBloomberg/leptonica/blob/master/src/dewarp1.c)、[dewarp2.c](https://github.com/DanBloomberg/leptonica/blob/master/src/dewarp2.c) |
| **Coons patch（邊界曲線內插）** | 由四條邊界曲線做雙線性混合，得到頁面網格 | 公式，不需要套件 | 需要四條可信的邊界曲線。內部的透視縮短（捲起處字被壓扁）修不回來，因為邊界也一樣被壓縮【判讀】 | — |
| **薄板樣條（TPS）** | 用控制點對應插出平滑位移場 | SciPy 1.18.1 的 `RBFInterpolator(kernel="thin_plate_spline")`，BSD-3-Clause | 【實測】可用。【實測】OpenCV 5.1.0 headless 沒有 `createThinPlateSplineShapeTransformer`（DDCP 用的就是這個函式） | 【直讀】[PyPI scipy](https://pypi.org/project/scipy/)（授權欄為 BSD 全文） |

## 2. 學習式方法

| 方法 | 程式碼授權 | 權重 | 輸出型態（會不會改字） | 輸入解析度 | 可否商用 | 來源 |
|---|---|---|---|---|---|---|
| **UVDoc** | MIT（2023，Tanguy Magne） | 在程式庫內 `model/best_model.pkl`，32,158,393 位元組，適用同一份 MIT | 2D 網格 45×31 加 3D 網格，以 `grid_sample` 重新取樣原圖，**不生成像素** | 縮到 488×712；示範程式的輸出尺寸等於輸入尺寸，**不估頁面長寬比** | 可 | 【直讀】[LICENSE](https://github.com/tanguymagne/UVDoc/blob/main/LICENSE)、[README](https://github.com/tanguymagne/UVDoc/blob/main/README.md)、[utils.py](https://github.com/tanguymagne/UVDoc/blob/main/utils.py)、[demo.py](https://github.com/tanguymagne/UVDoc/blob/main/demo.py) |
| UVDoc（PaddleOCR 版、Transformers 5.24.0 版） | Apache-2.0（`transformers/models/uvdoc/*` 的檔頭寫「Copyright 2026 PaddlePaddle Team and HuggingFace」） | `PaddlePaddle/UVDoc_safetensors`、`PaddlePaddle/UVDoc`：**授權欄查不到**（Hugging Face 無法連線；搜尋也沒有帶出） | 同上 | 同上 | 權重待查 | 【直讀】transformers 5.24.0 wheel；【搜尋摘要】[模型卡](https://huggingface.co/PaddlePaddle/UVDoc)；image.md #15–17 |
| DewarpNet | MIT | Google Drive，沒有另立授權 | 反向對應圖（bm）後 remap | 256×256 → bm 128×128 | 權重無明文 | 【直讀】[README](https://github.com/cvlab-stonybrook/DewarpNet/blob/master/README.md)、[infer.py](https://github.com/cvlab-stonybrook/DewarpNet/blob/master/infer.py) |
| PaperEdge | MIT | Google Drive，每個 139.7 MB，沒有另立授權 | 位移場後 `grid_sample` | 256×256 | 權重無明文 | 【直讀】[README](https://github.com/cvlab-stonybrook/PaperEdge) |
| DocRes（攤平任務） | MIT | OneDrive，沒有另立授權 | 預測對應圖後 `cv2.remap` | 256×256 | 權重無明文 | 【直讀】[inference.py](https://github.com/ZZZHANG-jx/DocRes/blob/master/inference.py)；image.md #18 |
| DDCP（控制點加 TPS） | MIT | 程式庫內沒有權重；README 只寫「下載模型參數」，沒有附連結 | 控制點與參考點，以 TPS 攤平；可人工調整控制點 | 992×992 | 權重查不到 | 【直讀】[README](https://github.com/gwxie/Document-Dewarping-with-Control-Points)、`Source/README.md` |
| DocTr、DocTr++、DocGeoNet、DocScanner | 自訂授權，第 2.2 條：未經書面許可嚴禁商用 | 同左 | 反向對應圖 | DocTr 為 288×288 | **不可** | 【直讀】[DocTr-Plus LICENSE.md](https://github.com/fh2019ustc/DocTr-Plus/blob/master/LICENSE.md)、DocTr `inference.py`；image.md #22 |
| DvD（擴散式） | **AGPL-3.0** | — | — | — | **排除** | 【直讀】[LICENSE](https://github.com/hanquansanren/DvD/blob/master/LICENSE) |
| AADD（AAAI 2026）、D2Dewarp／DocDewarpHV（CVPR 2026）、Marior | 程式庫都**沒有 LICENSE 檔**；AADD 的程式碼「即將釋出」；DocDewarpHV 目前只有資料集說明 | 未釋出或無授權 | — | DocDewarpHV 為 512×512 | 目前不可用 | 【直讀】[AADD](https://github.com/chaoyunwang/aadd)、[DocDewarpHV](https://github.com/xiaomore/DocDewarpHV)、[Marior](https://github.com/ZZZHANG-jx/Marior) |

- 上表只輸出「對應圖」的方法，像素都來自原圖，不會憑空生成筆畫；但重新取樣會模糊，網格太粗時會把細部摺痕抹平。【判讀】
- 會改動像素內容的是影像對影像的方法：DocRes 的去陰影與外觀增強、GCDRNet。【判讀】
- UVDoc 的訓練資料：Doc3D 與 UVDoc 資料集。UVDoc 訓練集的文件紋理，部分「以 DeepFloyd IF 生成」；背景取自 DTD。【直讀】[UVDoc-Dataset README](https://github.com/tanguymagne/UVDoc-Dataset)。這些資料條款對權重的影響需要法務判斷，見第 7 節。

## 3. 實測

### 3.1 合成資料與指標

- 產生器（自寫，放在暫存區）：把原生 PDF 以 150 dpi 畫成影像（自製課表格線頁，以及 `tests/fixtures/doc_a4.pdf`），貼到 3D 曲面上。曲面有四種：**平面**；**橫向圓柱彎曲**；**上緣等長捲起**（半徑 0.06 頁高，捲 80°）；**捲起加水平折痕**（9°）加輕微彎曲。接著用針孔相機投影、三角網格光柵化（由遠而近畫，處理遮擋），得到每個照片像素對應的頁面座標真值。再加 Lambert 明暗、背景紋理、光照漸層、雜訊與 JPEG（紋理與 JPEG 沿用 `mortise.eval.synth` 的函式）。照片長邊 1600 px。
- **位置誤差**（主要指標）：輸出像素 → 方法給的對應圖 → 照片座標 → 真值頁面座標。對「輸出座標」做最佳仿射擬合，回報殘差的中位數與 p95（單位：頁面像素）。**涵蓋率**：頁面被輸出涵蓋的比例。
- 樣本只有 2 頁 × 4 種變形、各 1 個亂數種子，屬於方向性的結果，不是統計結論。

### 3.2 結果（兩頁平均；格式為「中位數／p95　涵蓋率」）【實測】

| 方法 | 平面 | 橫向彎曲 | 上緣捲起 | 捲起加折痕 |
|---|---|---|---|---|
| 透視攤平（真值四角） | 0.0／0.0　0.98 | 9.9／33.8　1.00 | 15.0／33.4　0.99 | 14.0／40.0　0.99 |
| 透視攤平（`find_page_quad` 自動） | 0.0／0.0　0.98 | 23.4／52.7　**0.58** | 2.0／5.5　**0.86** | 5.5／12.8　**0.87** |
| UVDoc 全圖 | 3.2／7.5　0.98 | 9.6／17.8　0.98 | 11.1／28.9　0.98 | 18.5／43.6　0.98 |
| UVDoc（頁面外接框外擴 5% 裁切後） | 2.3／4.9　0.98 | 5.0／11.2　0.98 | 5.2／20.3　0.98 | 11.1／27.5　0.98 |
| 透視攤平後接 UVDoc（目前替換點的接法） | 5.0／12.8　**0.79** | 4.5／10.6　0.94 | 2.7／7.8　**0.85** | 3.7／12.4　0.98 |
| 透視攤平、四周補 6% 深色邊，再接 UVDoc | 2.1／4.5　0.98 | **3.9／8.0**　0.98 | 6.4／19.0　0.99 | 6.1／19.9　0.99 |
| page-dewarp 全圖 | 43.1／103.9（39% 是背景） | 42.8／103.7 | 19.2／51.6 | 34.5／96.0 |
| 透視攤平後接 page-dewarp | 2.9／9.6　0.76 | 36.3／95.2　0.79 | 11.9／25.8　0.77 | 11.3／30.8　0.83 |

解讀：

- 平面紙：透視攤平已經是精確解；任何學習式攤平都只會加誤差，所以**要有開關條件**。
- 自動四角在捲起或彎曲時「看起來很準」，是因為它把捲起的那條、或彎出去的兩側**丟掉了**（涵蓋率 0.58～0.87）。要同時看涵蓋率。
- UVDoc 預期輸入的頁面四周有背景。直接餵給它透視攤平後的頁面，它會往內裁（涵蓋率 0.79～0.85）。四周補邊後涵蓋率回到 0.98 以上，但捲起區的 p95 仍有 19～20 px（約 3.3 mm）：捲起處的透視縮短是最難的部分。
- UVDoc 的 grid3D（3D 網格輸出）本次沒有使用。

### 3.3 真實照片（介面截圖裁下，485×751）【實測】

- 這張圖看起來疊有介面的辨識文字（例如「Sessio n 1」「stratic anning challer」的字形與照片不同），量到的數字只能參考。【判讀】
- UVDoc 全圖：網格外框貼住紙張四邊，連捲起的上緣都在內。攤平後格線明顯變直。輸出圖存在暫存區 `out/real_compare.png`（左：網格，中：UVDoc，右：page-dewarp）。
- page-dewarp：把上方的布料也當成頁面，格線仍是彎的。
- 在 UVDoc 輸出上再跑 `find_page_quad`：`edge_fit`，信心 0.143，仍然很低。

### 3.4 不需要真值的驗證訊號【實測】

- **格線直度**：長的水平與垂直格線各擬合直線，取最大偏離（佔頁寬 %），列出各條線的中位數與最大值。

  | 影像 | 中位數 | 最大值 |
  |---|---|---|
  | 乾淨頁 | 0.00 | 0.04 |
  | 透視攤平：平面 | 0.03 | 0.10 |
  | 透視攤平：橫向彎曲 | 0.60 | 4.00 |
  | 透視攤平：捲起加折痕 | 0.25 | 1.25 |
  | 透視攤平：上緣捲起 | 0.04 | 0.34 |
  | UVDoc：平面 | 0.15 | 0.39 |
  | UVDoc：橫向彎曲 | 0.24 | 0.58 |
  | 真實照片（未攤平） | 0.18 | 1.43 |
  | 真實照片（UVDoc 後） | 0.12 | 0.69 |

  這個訊號抓得到「彎」與「UVDoc 加進來的波紋」，但**抓不到捲起造成的壓縮**：捲起時格線還是直的，只是行距變小。
- **邊界支持率**：沿 UVDoc 網格的四條外框曲線取樣，看法線 ±3 px 內 Lab 梯度是否大於 4；對照組是往內、往外各移 12 px 的曲線。

  | 影像 | 上 | 右 | 下 | 左 |
  |---|---|---|---|---|
  | 真實照片：外框 | 0.99 | 0.99 | 1.00 | 0.79 |
  | 真實照片：往內 12 px | 0.64 | 0.00 | 0.00 | 0.06 |
  | 合成照片：外框（左、下） | — | — | 0.16～0.66 | 0.00～0.33 |
  | 合成照片：往外 12 px（左、下） | — | — | 0.47～0.93 | 0.85～1.00 |

  合成照片的左、下兩邊，往外 12 px 的支持率比外框本身高：UVDoc 在 488×712 上推論，換算回 1600 px 的照片會偏內好幾個像素。結論：**UVDoc 的外框可以拿來當候選，但要在原圖解析度精修；而且精修是可以驗證的。**

### 3.5 速度（CPU，4 核）【實測】

| 項目 | 結果 |
|---|---|
| UVDoc，torch 2.15.0 | 每張 2.3～3.1 秒 |
| UVDoc，匯出 ONNX（opset 17，32,027,610 位元組）後以 onnxruntime 1.29.0 執行 | 中位數 0.27 秒（4 執行緒）、0.29 秒（1 執行緒） |
| torch 與 ONNX 的網格差異 | 在真實照片上最大 1×10⁻⁴ px |
| page-dewarp 0.3.4（SciPy Powell，未裝 JAX） | 每張 0.4～2.2 秒 |

## 4. 去陰影、光照校正、去反光

| 名稱 | 類型 | 程式碼授權 | 權重 | 可否用 | 來源 |
|---|---|---|---|---|---|
| Mortise `enhance`（形態學閉運算、中值、高斯估照度圖後相除） | 傳統 | 自有 | — | 已在用 | 【直讀】`page_geometry.enhance` |
| OpenCV 與 contrib 的白平衡、CLAHE、gamma、BIMEF | 傳統 | Apache-2.0 | — | 可 | image.md #28 |
| DocShadow-SD7K（ICCV 2023） | 學習式，2K 解析度 | MIT | GitHub Releases 的 `Weights` 標籤，沒有另立授權；SD7K 資料集授權查不到 | 權重無明文 | 【直讀】[README](https://github.com/CXH-Research/DocShadow-SD7K/blob/main/README.md) |
| DocRes 去陰影與外觀增強 | 學習式，長邊上限 1600，求出陰影比值圖後除回原圖 | MIT | OneDrive，沒有另立授權 | 權重無明文 | image.md #18、#25 |
| GCDRNet（外觀增強，IEEE TAI） | 學習式，影像對影像 | MIT（2024） | OneDrive，沒有另立授權；RealDAE 資料集 600 對 | 權重無明文 | 【直讀】[LICENSE.txt](https://github.com/ZZZHANG-jx/GCDRNet)、README |
| DocProj（攤平加光照，2019） | 學習式，分塊 | MIT | Google Drive，沒有另立授權 | 權重無明文 | 【直讀】[README](https://github.com/xiaoyu258/DocProj) |
| BGShadowNet（及其 RDD 資料集）、BEDSR-Net 非官方實作、UDoc-GAN | 學習式 | **沒有 LICENSE 檔** | — | 不可 | 【直讀】git clone 後檢查（[BGShadowNet](https://github.com/hyyh1314/BGShadowNet)、[BEDSR-Net](https://github.com/IsHYuhi/BEDSR-Net_A_Deep_Shadow_Removal_Network_from_a_Single_Document_Image)、[UDoc-GAN](https://github.com/harrytea/UDoc-GAN)） |
| DocTr 的光照校正（IllTr） | 學習式 | 非商用 | — | 不可 | image.md #22 |
| Bako 2016、Jung 2018（water-filling）、Kligler 2018 | 傳統論文 | 找不到附授權的官方實作 | — | 照論文自寫 | 【搜尋摘要】 |
| 文件去反光（DocHR14K／L2HRNet、SHDocs） | 學習式 | 查不到程式庫與授權 | — | 不可 | 【搜尋摘要】；image.md #29 |

實測：用 `synth.degrade(photo, strength=1.3)` 強制加陰影，再以真值矩陣反投影回頁面。【實測】

| 情況 | 紙白區亮度變異係數（處理前 → 處理後） | Otsu 二值化墨跡 F1（處理前 → 處理後） |
|---|---|---|
| 無陰影 | 0.069～0.091 → 0.033～0.038 | 0.15～0.48 → 0.52～0.66 |
| 柔邊陰影 | 0.085～0.093 → 0.026～0.028 | 0.13～0.42 → 0.52～0.61 |
| 硬邊陰影 | 0.234～0.239 → 0.049～0.050 | 0.10～0.15 → 0.45～0.48 |

硬邊陰影處理後，陰影邊界留下亮暗帶（`out/shadow_view.png`）。原因是照度圖被高斯平滑，硬邊被抹開了。【判讀】

## 5. 評測基準與資料授權

- **DocUNet 基準**：130 張照片（65 份文件，每份兩張）加平台掃描檔。【搜尋摘要】官方頁沒有授權或使用條款，只要求引用。【搜尋摘要】[docunet.html](https://www3.cs.stonybrook.edu/~cvl/docunet.html)
- **評測程式**（以 UVDoc 庫附的 MATLAB 腳本為準）【直讀】[eval_code](https://github.com/tanguymagne/UVDoc/tree/main/eval/eval_code)、[docUnet_eval.py](https://github.com/tanguymagne/UVDoc/blob/main/docUnet_eval.py)、[ocr_eval.py](https://github.com/tanguymagne/UVDoc/blob/main/eval/ocr_eval/ocr_eval.py)：
  - 前處理：參考掃描檔以雙三次內插縮到面積 598,400 px；預測圖縮到同一尺寸；兩者轉灰階。
  - **MS-SSIM**：五層 `impyramid` 的 SSIM，以權重 [0.0448, 0.2856, 0.3001, 0.2363, 0.1333] **加權求和**（不是 Wang 原式的連乘）。
  - **LD**：以 C. Liu 的 SIFT flow 求稠密對應，取位移長度的平均。
  - **AD**：先以 `alignLD` 對齊，再量失真。
  - **CER、ED**：pytesseract `--oem 1`，比對的是「掃描檔的 OCR 結果」，不是人工標註的文字。
  - SIFT flow 套件的 readme 只要求引用，沒有授權條文。【直讀】
  - 本次自寫的 MS-SSIM 與以 DIS 光流代替的 LD，跟論文數字**不可比**。
- **其他基準**：UVDoc 基準與 DIR300 的資料授權查不到。UVDoc README 的勘誤表數字見 image.md #13；PaddleOCR 版 UVDoc 在 DocUNet 上的 CER 0.179 見 image.md #15。
- **訓練資料**：
  - Doc3D：GitHub 庫是 MIT（下載腳本）；README 說紋理來自 Yes! Magazine（創用 CC）、CVF Open Access、Project Gutenberg，2025-12-18 起放在 Hugging Face。【直讀】[README](https://github.com/cvlab-stonybrook/doc3D-dataset)
  - Hugging Face 資料集卡的授權欄只在第三方鏡像看到「mit」。【第三方】
- **自己合成**（建議做法，已在暫存區試作 `scripts/synth_curl.py`）：
  1. 原生 PDF 畫成影像，貼到 3D 曲面。曲面用等長變形（圓柱、捲邊、折痕，可疊加），再加相機、光照、背景。
  2. 輸出「照片像素 → 頁面座標」的稠密真值。
  3. 評測直接用真值位置誤差加涵蓋率，不靠 SIFT flow。
  4. 另以 OCR 比對 PDF 的文字層算 CER：有真文字，比 DocUNet 拿掃描檔的 OCR 當基準乾淨。【判讀】
  5. 要改的地方：`synth.py` 的介面目前是 3×3 矩陣，表達不了非線性變形（它的說明已經寫到），要改成回傳對應圖。

## 6. 查不到的事

1. `PaddlePaddle/UVDoc` 與 `UVDoc_safetensors` 的權重授權（Hugging Face 無法連線）。
2. DocUNet、UVDoc 基準、DIR300、SD7K、RealDAE 的資料使用條款；Doc3D 在 Hugging Face 上的官方授權欄。
3. UVDoc 訓練集用 DeepFloyd IF 生成紋理、用 DTD 當背景：這些來源的條款是否影響 MIT 權重的商用（需要法務意見；DeepFloyd IF 授權本次未讀）。
4. DDCP、FDRNet 的權重下載處；DewarpNet、PaperEdge、DocRes、GCDRNet、DocProj、DocShadow-SD7K 的權重授權（只有程式庫授權）。
5. 文件去反光：沒有附授權、可下載的模型。
6. 本次沒有跑 DocUNet 官方評測：MATLAB 不可用，而且基準資料下載站無法連線。

## 7. 給實作的建議

### 7.1 鐵律在這段怎麼落實

- 學習式模型只提供兩樣東西：「頁面外框曲線」與「粗位移場」，都只當候選。
- 最後的四邊與曲面，由演算法在原圖解析度上量出來：邊緣剖面、格線、文字行。
- 每一步都留下**對應圖**（輸出像素 → 原照片座標）與殘差，量測的位置都能回推到原照片並驗證。

### 7.2 建議管線（可商用、CPU）

1. **找紙**：先跑現有的 `find_page_quad`。成功且四邊殘差小 → 跳到第 4 步做直度檢查。
2. **候選外框**：失敗（如本例）或判斷為彎曲時，跑 UVDoc（官方 MIT 權重匯出成 ONNX，onnxruntime 推論，約 0.3 秒）。取網格的四條外框曲線當候選。
3. **外框精修與驗證**：沿每條候選曲線取法線剖面，沿用 `_refine` 的「內側→外側色差投影」找次像素邊緣點。改成擬合**平滑曲線**（三次 B-spline 或二次式），權重沿用 Tukey 雙權重。
   - 回報每邊的支持率與殘差。
   - 沒有支持的邊（被遮住、或真的超出畫面）標記出來，不要硬湊。
   - 四角由相鄰曲線相交得到。
4. **決定要不要去彎曲**：先只用透視攤平（四角取自曲線端點），量格線直度與文字行直度。
   - 中位數 < 0.1% 且最大值 < 0.3% → 不去彎曲（平面紙用學習式只會變差，見 3.2）。門檻是由本次合成結果初定，要用真實樣本校正。
5. **建位移場**（由量測決定）：
   - 約束來源：精修後的四條邊界曲線（Coons patch 當底），加上頁內的水平與垂直格線、文字行中心線（Leptonica 式：每條線擬合二次曲線，得垂直位移；直線段或欄位邊界得水平位移）。
   - 用 TPS（SciPy `RBFInterpolator`，`thin_plate_spline` 加平滑）或規則網格的雙三次 B-spline 求解。
   - UVDoc 的位移場只當**初值與正則化的先驗**，用在沒有特徵的區域。在報告裡標成「推估」，不是「量測」。
6. **捲起區的壓縮**：直度抓不到行距壓縮。要用格線的行高一致性、字高一致性（同一字級的字高應該相同）估局部縮放，或者用 UVDoc 的 grid3D 算弧長（未驗證）。估不出來就把這條區域標成低信心，交給使用者確認。
7. **一次取樣**：透視與去彎曲合成**一張**對應圖，對原照片只做一次 `cv2.remap`，避免兩次內插造成模糊。頁面長寬比沿用 Zhang–He 的估計，或尺寸候選；UVDoc 不估長寬比。
8. **驗證後才採用**：去彎曲輸出的格線直度、邊界支持率、OCR 信心都要不差於純透視攤平，才採用；否則退回純透視，並在還原度報告寫明。

### 7.3 介面與要動的檔案

- `set_dewarper(fn)`：改成 `fn(原照片, PageQuad 或 None, params) -> DewarpResult`。`DewarpResult` 至少要有：
  - `map`：H×W×2 float32，輸出像素中心 → 原照片的像素角落座標，與現有座標慣例一致
  - `boundary`：四條邊界曲線
  - `support`：各邊支持率
  - `residual_px`
  - `confidence`
  - `notes`
  - `source`：例如 "measured"、"uvdoc_prior"

  目前的接法有兩個問題：一是在**透視攤平之後**才呼叫，二是只回傳影像，所以座標無法回推。實測也顯示，攤平後再接 UVDoc 會往內裁（涵蓋率 0.79）。
- `PageQuad` 擴充：`boundary_curves`、`curved: bool`。`find_page_quad` 湊不成四條直線時，改走曲線外框，不要直接回傳 `full_image`。
- `mortise/eval/synth.py`：加入曲面變形，回傳對應圖。可參考暫存區的 `synth_curl.py`。評測指標加上「真值位置誤差 p95」與「涵蓋率」。
- `enhance`：照度圖改成只用紙白像素估計（先用 Sauvola 遮掉墨跡），平滑改用保邊濾波（例如 opencv-contrib 的 guided filter，Apache-2.0），以保留硬陰影的邊。去反光只做「過曝區偵測與標記」。
- 權重放置：UVDoc 的 ONNX 約 32 MB，隨附時要附 MIT 授權文。onnxruntime 是 MIT（PyPI 1.32.0）。torch 不需要進產品。

### 7.4 待驗證的假設

1. 直度門檻（中位數 0.1%、最大值 0.3%）在真實照片上能分開「平的紙」與「彎的紙」，誤判率要用真實樣本量。
2. 曲線外框精修在本例能補回捲起的上邊（UVDoc 外框在本例支持率 0.79～1.00，但這是 485 px 的縮圖）。要用原始解析度照片重跑。
3. 由格線、文字行建的位移場，比 UVDoc 位移場更準（目標：合成集 p95 < 5 px、平面紙不劣化）。本次未實作，未驗證。
4. 捲起區可以用行高與字高的一致性估出壓縮率；做不到就只能標低信心。
5. 本例照片可能疊有介面的辨識文字：拿到原始照片後要重測。
6. 以紙白像素加保邊濾波估照度圖，能消除硬邊陰影的亮暗帶，而且不傷筆畫（以墨跡 F1 驗證）。

實驗腳本與輸出（暫存區，可能被清除）：`/tmp/claude-0/-home-user-Mortise/23cac33b-f54e-5b7f-a414-9498035590bc/scratchpad/research-photo/`，其中 `scripts/` 有 `synth_curl.py`、`evaluate.py`、`eval2.py`、`uvdoc_run.py`、`pd_run.py`、`straight.py`、`verify_boundary.py`、`shadow_eval.py`；結果在 `out/eval.json`、`out/eval2.json`。

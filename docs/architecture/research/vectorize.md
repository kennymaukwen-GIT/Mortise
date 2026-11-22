# 查證紀錄：把點陣圖形重新描成向量路徑（描圖）

- 查證日期：2026-11-21
- 適用產品：Mortise（榫）文件反編譯 SaaS，未來可能有桌面版
- 授權底線：所有元件必須可商用；排除 GPL、AGPL 元件（另有可購買商業授權者列為「需付費」）；排除非商用授權的模型權重
- 範圍：把已經壓成像素的平面圖形（標誌、圖示、插畫、圖表）重建成 `VectorGraphic`（`mortise/artifacts/element_list.py`）。[image.md](image.md) 1.4、2.5、3.4 已查過的授權事實直接引用；本檔與它不一致的兩處（AutoTrace 函式庫的授權、VTracer 的定位）以本檔為準

## 0. 查證方式與限制

| 來源 | 狀況 |
|---|---|
| 網路搜尋 | 可用 |
| raw.githubusercontent.com、github.com（含 git 複製）、gitlab.com、pypi.org、potrace.sourceforge.net | 可直接讀取 |
| vectorizer.ai、vectormagic.com、recraft.ai、canva.com、affinity.studio、inkscape.org、helpx.adobe.com、community.adobe.com | **無法讀取**（被工作環境的網路規則擋下）。商用對手的說法全部只有搜尋摘要 |
| arxiv.org、huggingface.co、dl.acm.org、johanneskopf.de、www.visioncortex.org、docs.rs | **無法讀取**。論文與模型卡只有搜尋摘要；VTracer 的演算法改讀原始碼與版本庫內的設計文件 |

標註：**【直讀】** 親自讀到原文（LICENSE、原始碼、README、套件登錄站中繼資料、論文 PDF）；**【實測】** 在本機跑出來的數字（方法與完整數字見 2.8）；**【搜尋摘要】** 搜尋工具轉述，沒有打開該頁；**【第三方】** 評比站、論壇、轉述；**【判讀】** 依已查證事實做的推論，不是查證結果。

實測只用合成圖（已知幾何的圓、圓角矩形、細線、漸層）。**沒有用真實的標誌、截圖或掃描圖測過**，也沒有 Illustrator、Vectorizer.AI 可以對照。

## 1. 總表

| 名稱（版本） | 授權 | 可否商用 | CPU | 重點 | 弱點 | 細節 |
|---|---|---|---|---|---|---|
| **VTracer 0.6.15**（PyPI 穩定版，本機已安裝） | MIT | 可 | 可，240×240 約 5–12 毫秒，1920×1920 約 0.4 秒 | 彩色；stacked、cutout；spline、polygon | 邊界平均外擴約 0.4 px；不認基本幾何；沒有漸層與筆畫；JPEG 下碎片多 | 2.1、2.8 |
| **VTracer 1.0.0a4**（預發行） | MIT OR Apache-2.0 | 可 | 可 | 無縫 cutout、`simplify`、固定調色盤、分水嶺分群 | alpha 版；同樣沒有基本幾何、漸層、筆畫 | 2.1 |
| ImageTracer.js 1.2.6 | Unlicense | 可 | 可（JavaScript） | k-means 變體減色 | 只有直線與二次曲線；2020 後未更新 | 2.2 |
| Potrace 1.16 | GPL v2 以上；另售 Potrace Professional | 需付費 | 可 | 黑白描圖的品質標竿 | 只吃黑白 | 2.2 |
| AutoTrace 0.31.11 | **矛盾**：README 稱函式庫 LGPL，編進函式庫的核心檔卻標 GPL-2.0-or-later | **排除** | 可 | 輪廓與中心線 | — | 2.2 |
| DiffVG、LIVE | Apache-2.0 | 可 | 不實用 | 逐張最佳化 | 顯示卡上每張數十秒到數分鐘 | 2.3 |
| StarVector 1B、8B | 程式 Apache-2.0；權重 Apache-2.0（只有搜尋摘要） | 待確認 | 不實用 | 圖示、標誌、圖表 | 作者明講不適用插畫；是重畫不是量測 | 2.3 |
| OmniSVG 1.1（4B、8B） | 程式 Apache-2.0；訓練資料 CC BY-NC-SA 4.0；1.1 權重授權查不到 | 未確認 | 不可（17–26 GB 顯示記憶體） | 圖示到複雜插畫 | 同上 | 2.3 |
| Schneider 曲線擬合（Graphics Gems `FitCurves.c`） | Graphics Gems 授權，明文允許商用 | 可 | 可 | 分段三次貝茲擬合的原典 | C 程式，要自己移植 | 2.4 |
| flo_curves、kurbo（Rust） | Apache-2.0；Apache-2.0 OR MIT | 可 | 可 | 現成的貝茲擬合 | Python 綁定 kurbopy 沒有授權檔，不可用 | 2.4 |
| OpenCV 5.1.0（已是相依） | Apache-2.0 | 可 | 可 | 輪廓、多邊形近似、橢圓擬合、距離轉換、k-means | 輪廓是整數像素座標 | 2.4 |
| scikit-image 0.26.0 | BSD | 可 | 可 | marching squares 等值線、骨架化 | 目前不是相依 | 2.4 |
| libimagequant | GPL v3 或付費 | 排除 | — | — | PyPI 的 `imagequant` 自標 BSD-3-Clause，底層仍是它 | 2.4 |

## 2. 逐條事實與來源

### 2.1 VTracer

1. **0.6.15 的授權。** 中繼資料 `License-Expression: MIT`、`Requires-Python >=3.7`，2026-03-23 上傳；輪檔內有 MIT 條文的 LICENSE 與一份相依清單（CycloneDX），列 62 個 Rust 元件，授權都是 MIT、Apache-2.0、BSD、Zlib、Unlicense 一類，沒有 GPL 或 LGPL；內含 visioncortex 0.8.10、flo_curves 0.3.1。【直讀】本機輪檔、[PyPI](https://pypi.org/project/vtracer/0.6.15/)
2. **1.0.0a4 的授權。** 中繼資料 `License: MIT OR Apache-2.0`、`Requires-Python >=3.8`，2026-08-29 上傳，abi3 輪檔涵蓋 Linux（x86_64、aarch64、musl）、macOS、Windows；相依清單 67 個元件同樣沒有 GPL 類；內含 visioncortex 0.9.3（`MIT OR Apache-2.0`）、flo_curves 0.8.1。輪檔裡沒有 LICENSE 檔，版本庫根目錄的 LICENSE 是 MIT。主線最後一次提交是 2026-11-21，正式版時程查不到。【直讀】下載的輪檔、[PyPI](https://pypi.org/project/vtracer/1.0.0a4/)、[visioncortex Cargo.toml](https://github.com/visioncortex/visioncortex/blob/master/Cargo.toml)
3. **0.6.15 的 Python 介面。** 三個函式：`convert_image_to_svg_py(image_path, out_path, …)`、`convert_raw_image_to_svg(img_bytes, img_format=None, …) -> str`、`convert_pixels_to_svg(rgba_pixels, size, …) -> str`。共用參數與預設值：`colormode`（color、binary）、`hierarchical`（stacked、cutout）、`mode`（spline、polygon、none）、`filter_speckle=4`、`color_precision=6`、`layer_difference=16`、`corner_threshold=60`（度）、`length_threshold=4.0`（3.5 到 10）、`max_iterations=10`、`splice_threshold=45`（度）、`path_precision=8`。【直讀】輪檔內的 `vtracer.pyi`
4. **1.0.0a4 的 Python 介面完全不同**：`vtracer.Config(...)` 加 `convert_file`、`convert_bytes`、`convert_pixels`；多了 `clustering`（color-cluster、bw、watershed）、`simplify`、`palette`、`max_colors`、`optimize`（0 到 2）、`watershed_detail`、自適應二值化；`path_precision` 預設變成 2。升級等於重寫呼叫端。【直讀】[README](https://github.com/visioncortex/vtracer/blob/master/README.md)、【實測】
5. **管線。** 階層式色彩分群（比較顏色時丟掉低位元，`color_precision` 控制；`layer_difference` 是分層的色差門檻；去斑點在分群內做，面積門檻是 `filter_speckle` 的平方）→ 沿像素格線走出邊界 → 去階梯 → 以轉角角度門檻找角點 → 四點細分平滑（保留角點）→ 在反曲點與累積轉角超過 `splice_threshold` 處切段 → 每段做最小平方三次貝茲擬合（flo_curves，即 Schneider 演算法；visioncortex 主線把擬合容許誤差寫死為 10）。以上讀的是主線原始碼，0.6.15 內含的舊版沒有逐行比對。【直讀】[spline.rs](https://github.com/visioncortex/visioncortex/blob/master/src/path/spline.rs)、[smooth.rs](https://github.com/visioncortex/visioncortex/blob/master/src/path/smooth.rs)、[architecture.md](https://github.com/visioncortex/vtracer/blob/master/docs/design/architecture.md)
6. **stacked 與 cutout。** stacked 把每一層各自描成封閉外形，依序疊畫，不產生有洞的形狀，所以輸出精簡；原始碼註解承認相鄰形狀各自平滑後，共用邊會略為錯開。cutout 在 0.6.x 是把分群結果重新畫出再分群、每區各自描，設計文件明講會有縫；1.0 改成先建邊界圖，每條共用邊只擬合一次、兩側共用，才是真正無縫。【直讀】[config.rs](https://github.com/visioncortex/vtracer/blob/master/crates/vtracer/src/config.rs)、[mosaic.md](https://github.com/visioncortex/vtracer/blob/master/docs/design/mosaic.md)
7. **輸出能力的缺口。** 填色只有純色（原始碼 `enum Paint { Solid(Color) }`，註解寫「room for gradients later」）；沒有筆畫或中心線輸出（visioncortex 有 `skeleton.rs`，VTracer 沒有用到）；沒有任何基本幾何辨識。spline 模式連直線都輸出成三次貝茲。【直讀】同上、[skeleton.rs](https://github.com/visioncortex/visioncortex/blob/master/src/shape/skeleton.rs)、【實測】
8. **反鋸齒。** 預設的分群前端沿二值遮罩的像素格線走邊界，不用邊緣像素的混色值定位。1.0 的分水嶺前端會把反鋸齒像素貼到兩色中點的等值線。【直讀】[walker.rs](https://github.com/visioncortex/visioncortex/blob/master/src/path/walker.rs)、[CHANGELOG](https://github.com/visioncortex/vtracer/blob/master/CHANGELOG.md)
9. **官方承認的缺陷。** 細長條附近的曲線可能往旁邊甩出約 30 px，是「長期存在的缺陷」，在 visioncortex 0.9.1（VTracer 1.0.0-alpha.2）修掉。0.6.15 內含的是 0.8.10，**沒有這個修正**。我用四個細長合成形狀沒有重現，測試不充分，不能當作沒事。【直讀】[CHANGELOG](https://github.com/visioncortex/vtracer/blob/master/CHANGELOG.md)
10. **1.0 的 `simplify`** 是仿 paper.js 的 Schneider 重新擬合：角點之間的平滑段用最少的三次曲線重畫，官方說檔案約減半。【直讀】同上
11. **定位。** README 自述原本為高解析度藍圖掃描設計，刻意跳過 Potrace 的最佳多邊形搜尋，換取線性時間。【直讀】[README](https://github.com/visioncortex/vtracer/blob/master/README.md)

### 2.2 ImageTracer.js、Potrace、AutoTrace

1. **ImageTracer.js** 的流程：k-means 變體減色 → 逐色分層 → marching squares 變體取邊界 → 八方向內插 → 每段先試直線、再試二次曲線，超出誤差就對半切。作者自列的待改進：沒有三次曲線、沒有中心線、預設值「看起來可以，不是算出來的」。【直讀】[process_overview.md](https://github.com/jankovicsandras/imagetracerjs/blob/master/process_overview.md)
2. **Potrace** 的流程：邊界分解 → 去斑點（`turdsize`）→ 最佳多邊形 → 頂點調整 → 角點分析（每個頂點算 α，α ≥ `alphamax` 判為角點，預設 1；長邊相交或夾角尖銳都傾向判為角點）→ 曲線最佳化（`opttolerance` 預設 0.2）。作者寫明角點分析在解析度高時較準。授權與付費版見 image.md 第 45 至 48 條。【直讀】[potrace.pdf](https://potrace.sourceforge.net/potrace.pdf)
3. **AutoTrace 的函式庫不能當 LGPL 用。** README 寫函式庫 `libautotrace` 是 LGPL v2.1 以上，但 `Makefile.am` 列進函式庫的 `src/fit.c`（曲線擬合核心）、`src/curve.c`、`src/autotrace.c`、`src/color.c`、`src/module.c`、`src/filename.c` 檔頭都是 `SPDX-License-Identifier: GPL-2.0-or-later`（主線提交 264a6be，2026-11-21；`src` 下的 .c 與 .h 檔 80 個標 LGPL、11 個標 GPL）。依本專案規則按較嚴格的一方處理：**排除**。這修正了 image.md 3.4「需要中心線時用 libautotrace」的建議。【直讀】[fit.c](https://github.com/autotrace/autotrace/blob/master/src/fit.c)、[Makefile.am](https://github.com/autotrace/autotrace/blob/master/Makefile.am)、[README](https://github.com/autotrace/autotrace/blob/master/README.md)
4. 速度：StarVector 論文說 VTracer 與 AutoTrace 不到一秒，Potrace 約十秒。【搜尋摘要】[arXiv 2312.11556](https://arxiv.org/abs/2312.11556)

### 2.3 最佳化式與學習式方法

1. **DiffVG、LIVE**（皆 Apache-2.0，見 image.md 第 55 條）：一篇回顧在 RTX 3090 Ti 上量到 LIVE 256×256、4 層 33 秒、16 層 158 秒；DiffVG 500 次迭代 47 到 56 秒。同一篇的結論是「沒有又快又通用的全自動方法，每種都需要人介入」，機器學習方法「很慢，而且重現得不準」。【搜尋摘要】[arXiv 2306.06441](https://arxiv.org/abs/2306.06441)
2. **StarVector**（CVPR 2025）：程式 Apache-2.0；README 明寫「不適用自然影像或插畫，因為沒有用那些資料訓練；擅長圖示、標誌、技術圖、圖表」。【直讀】[README](https://github.com/joanrod/star-vector/blob/main/README.md)。權重的模型卡標 Apache 2.0。【搜尋摘要】[模型卡](https://huggingface.co/starvector/starvector-8b-im2svg)
3. **StarVector 自己的評測反而說明傳統描圖不差。** README 的 DinoScore 表（SVG-Stack、Fonts、Icons、Emoji、Diagrams）：VTracer 0.954、0.964、0.940、0.981、0.882；StarVector-8B 0.966、0.982、0.984、0.981、0.959。差距主要在圖表類。【直讀】同上。論文另說傳統方法的均方誤差接近零，但把圖拆成很多路徑、語意不清楚。【搜尋摘要】同上論文
4. **OmniSVG**：程式 Apache-2.0，訓練資料 MMSVG 為 CC BY-NC-SA 4.0；1.1 的 8B、4B 需 26、17 GB 顯示記憶體，產生 256 到 4096 個符號約 4 到 98 秒。【直讀】[README](https://github.com/OmniSVG/OmniSVG/blob/main/README.md)。舊的 3B 模型卡寫 Apache-2.0；1.1 權重的授權欄查不到。【搜尋摘要】[模型卡](https://huggingface.co/OmniSVG/OmniSVG)
5. **VectorArk**（Adobe，CVPR 2026）：以視覺語言模型輸出「圓角多邊形」表示法，自稱在文生圖的輸出上勝過 StarVector、OmniSVG、GPT-4o；補充資料拿 Illustrator 影像描圖當對照，並評 Potrace「外形破碎、抗雜訊差」、VTracer「外形最一致可用」；A100 上簡單樣本 3 到 4 秒（StarVector 8 到 16 秒、OmniSVG 4 到 6 秒）。程式、權重、授權都查不到。【搜尋摘要】[arXiv 2605.24398](https://arxiv.org/abs/2605.24398)、[專案頁](https://vectorark.github.io/)
6. InternSVG（ICLR 2026）、LLM4SVG：程式與權重的授權查不到。【搜尋摘要】[arXiv 2510.11341](https://arxiv.org/abs/2510.11341)
7. 【判讀】這一類全部不符合本次限制：都要顯示卡；輸出是模型「重畫」，座標不是量出來的；權重授權多半只有搜尋摘要。可以留作日後「語意分組、命名圖層」的參考，不進幾何管線。

### 2.4 可以拿來自建的可商用積木

1. **Schneider 的原始程式可商用。** Graphics Gems 的授權原文：程式受著作權保護，不能把程式碼本身拿去轉賣，但「可用於任何程式、產品或函式庫，不論商業與否」，不要求署名。`FitCurves.c` 的步驟：弦長參數化 → 端點切線固定、最小平方解兩個控制點距離 → Newton–Raphson 重新參數化 → 仍超出誤差就在最大誤差點切開遞迴。【直讀】[LICENSE.md](https://github.com/erich666/GraphicsGems/blob/master/LICENSE.md)、[FitCurves.c](https://github.com/erich666/GraphicsGems/blob/master/gems/FitCurves.c)
2. flo_curves（Apache-2.0）的擬合註明「演算法出自 Schneider，Graphics Gems」。kurbo（Apache-2.0 OR MIT）的 `fit.rs` 是另一種做法：解四次方程式，讓有號面積與一階矩和來源曲線相符。kurbopy 0.13.1 在 PyPI 沒有授權欄，版本庫也沒有 LICENSE 檔，**不可用**。【直讀】[flo_curves fit.rs](https://github.com/Logicalshift/flo_curves/blob/master/src/bezier/fit.rs)、[kurbo fit.rs](https://github.com/linebender/kurbo/blob/main/kurbo/src/fit.rs)、[kurbopy](https://pypi.org/project/kurbopy/)
3. **OpenCV** 標頭檔（4.x 分支）註明的出處：`findContours` 用 Suzuki 1985；`approxPolyDP` 是 Douglas–Peucker；`fitEllipse` 用 Fitzgibbon 1995；`fitEllipseAMS` 用 Taubin 1991；另有 `fitEllipseDirect`。本機 5.1.0 無頭版有這些函式，也有 `distanceTransform`、`kmeans`、`pyrMeanShiftFiltering`、`edgePreservingFilter`；**沒有 `ximgproc`**（細化在 contrib 版）。【直讀】[imgproc.hpp](https://github.com/opencv/opencv/blob/4.x/modules/imgproc/include/opencv2/imgproc.hpp)、【實測】
4. scikit-image 0.26.0（BSD）：`find_contours` 是 marching squares；`skeletonize` 用 Zhang 1984 的細化（二維預設）或 Lee 1994。本機沒裝。【直讀】[find_contours](https://github.com/scikit-image/scikit-image/blob/v0.26.0/src/skimage/measure/_find_contours.py)、[skeletonize](https://github.com/scikit-image/scikit-image/blob/v0.26.0/src/skimage/morphology/_skeletonize.py)、[PyPI](https://pypi.org/project/scikit-image/)
5. **減色的授權陷阱。** Pillow 的 `quantize` 有 MEDIANCUT、MAXCOVERAGE、FASTOCTREE、LIBIMAGEQUANT 四種；本機 Pillow 12.3.0 沒有編入 libimagequant。libimagequant 是 GPL v3 以上或付費商業授權；PyPI 的 `imagequant` 1.1.6 自標 BSD-3-Clause，那只是綁定層。【實測】、【直讀】[libimagequant README](https://github.com/ImageOptim/libimagequant/blob/main/README.md)、[PyPI](https://pypi.org/project/imagequant/)
6. 其他：`simplification` 2.0.0（BlueOak-1.0.0，RDP 與 Visvalingam–Whyatt）；`svgpathtools` 1.8.0（MIT，可解析 SVG 路徑）。【直讀】[PyPI](https://pypi.org/project/simplification/)、[PyPI](https://pypi.org/project/svgpathtools/)
7. 自建評測集可用的向量圖庫：Fluent Emoji（MIT）、Material Design Icons（Apache-2.0）、Lucide（ISC）、Tabler Icons（MIT）、Noto Emoji 的圖檔（Apache-2.0）、Twemoji 圖檔（CC BY 4.0，要署名）。OpenMoji 是 CC BY-SA 4.0，不要放進版本庫。【直讀】[Fluent](https://github.com/microsoft/fluentui-emoji/blob/main/LICENSE)、[Material](https://github.com/google/material-design-icons/blob/master/LICENSE)、[Lucide](https://github.com/lucide-icons/lucide/blob/main/LICENSE)、[Tabler](https://github.com/tabler/tabler-icons/blob/main/LICENSE)、[Noto](https://github.com/googlefonts/noto-emoji/blob/main/README.md)、[Twemoji](https://github.com/jdecked/twemoji/blob/main/LICENSE-GRAPHICS)、[OpenMoji](https://github.com/hfg-gmuend/openmoji/blob/master/LICENSE.txt)

### 2.5 關鍵技術與出處

1. **反鋸齒邊緣的次像素定位。** 邊緣像素的顏色是兩側顏色依覆蓋率混合，邊界位置可以由混色比例反推。出處：Trujillo-Pino 等，〈Accurate subpixel edge location based on partial area effect〉，Image and Vision Computing 31 (2013) 72–90，可求邊緣的位置、方向、曲率、對比。【搜尋摘要】[ULPGC](https://accedacris.ulpgc.es/handle/10553/43474)。合成圖上，取覆蓋率 0.5 的等值線（相鄰像素中心之間線性內插），邊界點的均方根誤差 0.05 到 0.08 px，連 JPEG 品質 50 也是；VTracer 是 0.3 到 0.6 px。【實測】
2. **混色空間會造成系統偏差。** 來源圖在線性光下混色、卻用 sRGB 值估覆蓋率（或相反），深藍對白的圓半徑偏 0.12 到 0.16 px。【實測】
3. **JPEG 下的色彩量化。** Vector Magic 的說明：不要先把輸入色階化，因為邊緣的混色帶有位置資訊；改成指定輸出要用的顏色；邊緣過渡寬於一個像素的圖算是模糊。【搜尋摘要】[Vector Magic 技巧](https://vectormagic.com/support/tutorials/tips)。VTracer 1.0 的固定調色盤在 OKLab 找最近色。【直讀】[README](https://github.com/visioncortex/vtracer/blob/master/README.md)。只有兩色的圓存成 JPEG 品質 90、75、50 後，VTracer 預設值分別輸出 17、41、63 條路徑。【實測】
4. **分層與遮擋補全。** Law 與 Kang，〈Image Vectorization with Depth: convexified shape layers with depth ordering〉（arXiv 2409.06648，後刊於 SIAM）：從減色後的圖出發，以「補全後的凸度與面積」而不是 T 形交點來排深度，被遮住的部分用 Euler elastica 曲率修補補成凸形，再擬合貝茲、依深度疊放；不需訓練。【搜尋摘要】[arXiv](https://arxiv.org/abs/2409.06648)
5. **角點。** Potrace 用 α 參數（2.2 第 2 條）；VTracer 用角度門檻。Hoshyari 等（SIGGRAPH 2018）用學出來的角點判別加上簡潔性與連續性；Dominici 等的 PolyFit（SIGGRAPH 2020）先擬合粗多邊形，再換成曲線基元。兩者是否釋出程式、授權為何，查不到。【搜尋摘要】[Hoshyari](https://www.cs.ubc.ca/labs/imager/tr/2018/PerceptionDrivenVectorization/)、[PolyFit](https://www.cs.ubc.ca/labs/imager/tr/2020/ClipArtVectorization/)
6. **貝茲擬合。** Schneider，〈An Algorithm for Automatically Fitting Digitized Curves〉，Graphics Gems（Academic Press，1990）。【直讀】見 2.4 第 1 條
7. **基本幾何與規則化。** 橢圓擬合有 OpenCV 現成函式（2.4 第 3 條）。圓角矩形擬合、規則化（對齊、等半徑、對稱）的可商用現成函式庫：**查不到**，要自己寫。Illustrator 與 Vectorizer.AI 都把這一項當賣點（2.6）。
8. **漸層。** Du 等，〈Image vectorization and editing via linear gradient layer decomposition〉（SIGGRAPH 2023，ACM TOG 42(4)）：把分割好的圖分解成疊放的線性漸層層，偏好半透明的上層；程式在 GitHub，授權沒查。更早的是 Favreau 等的 Photo2ClipArt（SIGGRAPH Asia 2017）。【搜尋摘要】[專案頁](https://cragl.cs.gmu.edu/gradientlayers/)。VTracer 預設值會把一塊平滑的線性漸層輸出成單一平均色。【實測】
9. **細線還原成筆畫。** 線稿向量化的三篇代表作：Noris 等 2013（只適用乾淨線稿）、Favreau 等 2016（在保真與簡潔之間做整體最佳化）、Bessmeltsev 與 Solomon 2019（用方向場處理交叉點；作者稱前兩者分別怕雜訊、會偏離原圖）。【搜尋摘要】[arXiv 1801.01922](https://arxiv.org/abs/1801.01922)、[HAL](https://hal.archives-ouvertes.fr/hal-01309271)。線寬可以由覆蓋率總和除以長度得到：1.6 px 的線在 PNG 上算出 1.600，JPEG 品質 75 算出 1.89（雜訊被截成正值，高估 18%）。【實測】
10. **像素畫。** Kopf 與 Lischinski，〈Depixelizing Pixel Art〉，ACM TOG 30(4)，SIGGRAPH 2011：建相似圖（像素為節點）、用四條經驗法則消掉交叉的對角連線、依結果重塑像素格、沿格邊擬合 B-spline 再最佳化控制點。四條法則的內容沒有查到原文。實作 libdepixelize 只處理「沒有混色、沒有反鋸齒」的像素畫，Launchpad 頁列 GPL 與 LGPL 多種授權，現行授權沒有直讀。【搜尋摘要】[論文頁](https://johanneskopf.de/publications/pixelart/)、[Launchpad](https://launchpad.net/libdepixelize)
11. **Vector Magic。** 官網自述：次像素精度、描出邊緣的精確位置、自動選擇節點數並放在好的位置。公司是 Cedar Lake Ventures（2013 年 12 月由 Vector Magic, Inc. 改名），**Vectorizer.AI 是同一家公司的產品**。起源是 Stanford 人工智慧實驗室 James Diebel 與 Jacob Norda 的研究計畫；Diebel 的博士論文〈Bayesian Image Vectorization: the probabilistic inversion of vector image rasterization〉（Stanford，2008）常被引用，但它與產品的關係沒有查到直接證據。【搜尋摘要】[官網](https://www.vectormagic.com/)、[關於](https://vectorizer.ai/about)；【第三方】[cgpress](https://cgpress.org/archives/a_kind_of_magic.html)
12. **Vectorizer.AI。** 官網自述：深度學習網路加傳統演算法；「完整形狀擬合」，能擬合就用整個幾何形狀而不是只用貝茲曲線；偵測鏡射與旋轉對稱；次像素精度，依反鋸齒像素值放邊界，能抓出不到一像素寬的特徵；曲線種類有直線、圓弧、橢圓弧、二次與三次貝茲。**具體做法沒有公開。**【搜尋摘要】[官網](https://vectorizer.ai/)

### 2.6 商用對手

全部是【搜尋摘要】或【第三方】；官方頁面都打不開。

| 產品 | 宣稱的能力 | 輸出 | 價格 | 來源 |
|---|---|---|---|---|
| Vectorizer.AI | 見 2.5 第 12 條；輸入上限 3 百萬像素、30 MB，接受含透明的 32 位元輸入；輸出選項有填色形狀、描邊形狀、每條邊只描一次（給雷射與割字）、形狀堆疊方式。文字、漸層如何處理查不到 | SVG、PDF、EPS、DXF、PNG | 網頁版約每月 9.99 美元；API 每月 9.99 美元 50 張起（第三方數字） | [官網](https://vectorizer.ai/)、[輸出選項](https://vectorizer.ai/api/outputOptions)、[定價](https://www.vectorizer.ai/pricing) |
| Vector Magic | 見 2.5 第 11 條；可指定輸出顏色 | 線上版的格式沒查到；桌面版另有 AI、DXF 匯出與批次處理 | 線上版每月 5.49 美元、桌面版一次 295 美元（Capterra 的數字，官方定價頁未能開啟） | [Capterra](https://www.capterra.com/p/178297/Vector-Magic/) |
| Illustrator 影像描圖 | 29.0 版（2024 年 10 月）的 Image Trace 2.0：偵測線性漸層；圓、正方形、矩形描成可編輯的原生形狀；自動分組；透明區不再描成白色；錨點較少。論壇有人回報線稿比舊版差 | Illustrator 原生物件 | 單一程式約每月 22.99 美元（第三方數字一致） | [Adobe 論壇公告](https://community.adobe.com/t5/illustrator-discussions/image-trace-2-0-trace-images-with-more-accuracy-and-control/td-p/14914903) |
| Adobe Express 轉 SVG | 免費快速動作；上限 40 MB、解析度「4k × 4k」；收 JPG、PNG。做法未公開 | SVG | 免費 | [功能頁](https://www.adobe.com/express/feature/image/convert/svg)、[檔案需求](https://helpx.adobe.com/express/kb/faq-quick-actions.html) |
| Recraft | 向量化端點；`shape_stacking` 可選階層疊放或互不重疊 | SVG | API 每次 0.01 美元 | [定價](https://www.recraft.ai/docs/api-reference/pricing.md)、[端點文件](https://www.recraft.ai/docs/api-reference/tools/vectorize.md) |
| Canva、Affinity | Canva 的向量化功能頁是導向市集應用程式 Vectorify；Affinity（免費）新增影像描圖，有即時預覽，結果是可編輯路徑 | SVG；Affinity 原生物件 | Vectorify 查不到；Affinity 免費 | [Canva](https://www.canva.com/features/vectorizer/)、[Affinity](https://www.affinity.studio/features/image-trace) |
| Inkscape 1.4.4 | 描圖引擎是 Potrace、AutoTrace（含中心線）、libdepixelize（像素畫），原始碼目錄可證；論壇有 1.2.2 版中心線描圖當掉的回報（搜尋摘要） | SVG | 免費；整體以 GPL v3 以上散布 | 【直讀】[src/trace](https://gitlab.com/inkscape/inkscape/-/tree/master/src/trace)、[COPYING](https://gitlab.com/inkscape/inkscape/-/blob/master/COPYING) |

### 2.7 評測

1. **SVG-Bench**（StarVector）：10 個資料集、3 種任務；README 的成績表用 DinoScore。【直讀】[README](https://github.com/joanrod/star-vector/blob/main/README.md)。論文另報 MSE，並說 MSE 常與視覺品質不一致。【搜尋摘要】[arXiv 2312.11556](https://arxiv.org/abs/2312.11556)
2. **MMSVGBench**（OmniSVG）：純合成，圖示與插畫各 150 張影像轉 SVG 樣本，影像由 GPT-4o 產生。【直讀】[README](https://github.com/OmniSVG/OmniSVG/blob/main/README.md)
3. SArena（InternSVG，約 3.1 萬筆）、SVGenius（2,377 題、18 種指標）、UniSVG（CLIP 佔 60%，SSIM 與 LPIPS 各 20%）。2026 年的論文沿用 LPIPS、SSIM、DINO、MSE 四項。【搜尋摘要】[arXiv 2510.11341](https://arxiv.org/abs/2510.11341)、[arXiv 2506.03139](https://arxiv.org/abs/2506.03139)、[arXiv 2508.07766](https://arxiv.org/abs/2508.07766)
4. **vtracer-bench**（VTracer 版本庫內，1.0.0-alpha.3 起）：比較原圖與重畫圖，三個分項取加權幾何平均：PSNR、多尺度 SSIM、「成塊遺漏」（色差超過門檻的像素先做開運算去掉沿邊的細絲，再分群，以各群質量的平方和計分，所以一整塊漏掉遠比散落的雜點嚴重）。作者的經驗值：0.95 以上肉眼難分，0.90 以上算忠實，0.80 以下有明顯錯誤；原圖存成 JPEG 品質 75 再比自己得 0.92。【直讀】[README](https://github.com/visioncortex/vtracer/blob/master/crates/vtracer-bench/README.md)
5. 回顧論文的兩個比較準則：與原圖的相似度、結果的簡潔度（形狀數量）。【搜尋摘要】[arXiv 2306.06441](https://arxiv.org/abs/2306.06441)
6. 【判讀】上述公開基準都只比「重畫後像不像」，沒有一個拿已知的幾何當標準答案去量邊界位置、節點數、基本圖形有沒有認對。Mortise 要的是後者，必須自建（見 3.3）。

### 2.8 實測紀錄

條件：240×240，藍（30, 90, 200）壓白底，16×16 超取樣算覆蓋率，在 sRGB 值上混色；誤差是輸出路徑上的取樣點到已知幾何的距離。腳本在暫存目錄 `/tmp/claude-0/-home-user-Mortise/23cac33b-f54e-5b7f-a414-9498035590bc/scratchpad/research/exp/`（會被清掉，數字以本表為準）。

| 項目 | 結果 |
|---|---|
| 圓（半徑 80.3），VTracer 0.6.15 預設 | 12 段三次曲線；最大誤差 0.89 px、均方根 0.48、平均偏外 +0.43。1.0.0a4 預設值結果相同 |
| 同上，先把輸入硬切成二值再描 | 偏外 +0.41：外擴來自平滑步驟，不是反鋸齒邊緣造成 |
| 同上，polygon 模式 | 32 段直線；最大 0.85、均方根 0.39、偏外 +0.32 |
| 圓，1.0.0a4 cutout | 80 段；最大 0.84、均方根 0.28、偏移 0.00（不再外擴，但節點多） |
| 圓，1.0.0a4 `simplify=1.0` | 4 段；最大 1.14、均方根 0.51 |
| 圓角矩形（160.3×110.7，圓角 18.4），0.6.15 預設 | 46 段三次曲線、0 段直線（理想是 4 直線加 4 圓弧）；最大 0.94、均方根 0.62、偏外 +0.59 |
| 與像素對齊的正方形 | 四個角精確；四條邊各輸出成一段控制點共線的三次曲線 |
| 1.6 px 細線 | 輸出成 36 段曲線圍成的填色外形，不是筆畫 |
| 線性漸層矩形（160 px 寬，藍到黃） | 預設值：單一填色 #828278。`color_precision=8, layer_difference=4`：39 條色帶 |
| 圓存成 JPEG 品質 90、75、50 | 預設值輸出 17、41、63 條路徑。品質 75 加 `filter_speckle=8, color_precision=4` 回到 2 條。1.0.0a4 指定兩色調色盤仍有 21 條 |
| 先減成純色塊的圖（五色，其中兩色每通道只差 8）交給 0.6.15 | 預設值就輸出剛好五色五條路徑 |
| 速度 | 240×240：5 到 12 毫秒；1920×1920 純色塊圖：0.4 秒。`convert_pixels_to_svg` 要先轉成 Python 元組串列，24 萬像素就花 0.1 秒，應改用 `convert_raw_image_to_svg` 傳 PNG 位元組 |
| 自己量：覆蓋率 0.5 等值線 | 圓的邊界點：PNG 最大 0.11、均方根 0.05；JPEG 品質 75 最大 0.17、均方根 0.06；品質 50 最大 0.30、均方根 0.075。圓角矩形：PNG 最大 0.10、均方根 0.07；JPEG 品質 75 最大 0.22、均方根 0.08 |
| 自己量：等值線點做最小平方圓擬合 | PNG 與 JPEG 品質 90、75、50：圓心誤差不超過 0.011 px、半徑誤差不超過 0.005 px |
| 自己量：由覆蓋率總和反推半徑 | PNG +0.0002 px；JPEG 品質 75 +0.07、品質 50 +0.09（雜訊截斷造成正偏差） |

## 3. 給實作的建議

### 3.1 總原則

- **自建量測管線為主，VTracer 只當備援與對照組。** 理由是實測：VTracer 的邊界誤差比「覆蓋率等值線」大五到十倍，圓與圓角矩形認不出來，漸層變成單色，細線變成外形。這四項正是商用對手的賣點（2.6），也是 `VectorPath` 已經留好欄位的東西（`primitive`、`gradient`、`stroke_width`、`fit_error`）。
- **不採用**學習式模型（2.3 第 7 條）、AutoTrace（2.2 第 3 條）、Potrace 與移植版、libimagequant。
- 門檻一律放進 `mortise/config/default.toml` 已留好的區段，名稱以 `vector_` 開頭，並以量到的雜訊為單位，不寫死像素值。
- **不需要新增相依**就能做完：NumPy、SciPy、OpenCV 已足夠；marching squares、Schneider 擬合各約一兩百行，自己寫（Schneider 的 C 原始程式可商用，可照著移植）。

### 3.2 管線

1. **決定混色空間與兩側顏色。** 覆蓋率 α 由像素色在「區域色 A 到區域色 B」連線上的投影求得。先假設在 sRGB 值上混色（待驗證假設 1）。
2. **調色盤由內部像素量出來，不由邊緣像素。** 只取鄰域平坦的像素（局部梯度低於量到的雜訊）在 OKLab 分群，色差小於雜訊門檻的群合併；邊緣像素一律解釋成相鄰兩色的混合，不得成為新顏色。這一步同時擋掉 JPEG 的碎片（2.8：VTracer 在這裡輸出 41 條路徑）。雜訊大小由平坦區的色彩標準差量得，後面每個門檻都以它為單位。
3. **區域與拓樸。** 內部像素貼最近的調色盤色，連通成區域；小於門檻的區域併入共用邊最長的鄰居。建邊界圖：三區交會處是節點，兩節點之間是一條共用邊，每條邊只處理一次（做法照 VTracer 的 mosaic.md，那份文件把節點規則、棋盤格情形、不變量都寫清楚了）。
4. **次像素邊界。** 每條共用邊在兩側顏色的 α 場上取 0.5 等值線，相鄰像素中心之間線性內插。預期誤差均方根 0.05 到 0.08 px。節點位置由相交的邊外插求交，不直接取格點。
5. **角點。** 在等值線上以固定弧長視窗（初始值前後各 3 px）算轉角，超過門檻為候選；候選兩側各擬合一段直線或曲線，交點才是角點座標（反鋸齒會把尖角磨圓，直接取等值線上的點會內縮）。
6. **先試基本幾何，由簡到繁**（沿用 03-recognize.md 3B.11 的順序）：線、矩形、圓、橢圓、圓角矩形、多邊形。用等值線點做最小平方擬合，接受條件是最大偏差在容許值內（初始值 0.3 px，待校準）。接受後寫 `primitive`，`path` 由參數重新產生（圓用四段三次曲線）。
7. **規則化只在量測誤差範圍內做。** 接近水平垂直的邊扶正、相近的圓角半徑統一、同心、等寬、鏡射對稱（把輪廓對候選軸翻轉後量偏差）。每一項的吸附量都不得超過該參數的量測不確定度，否則保留原值並在 `notes` 註明。
8. **其餘輪廓做貝茲擬合。** 角點之間的每一段：先試直線，再試圓弧，最後才是 Schneider。容許誤差與雜訊掛鉤（初始值 0.2 px）。直線一律輸出直線段，不輸出共線控制點的曲線。共用邊擬合一次，兩側共用。
9. **細線輸出成筆畫。** 區域沿骨架的寬度（距離轉換的兩倍）小而穩定時，改輸出中心線加 `stroke_width`。中心線取兩條次像素邊界的中線，不取骨架像素。線寬用覆蓋率總和除以長度，但要先扣掉雜訊底（2.8：不扣會高估 18%）。小於 2 px 的線，顏色與寬度無法分離，這是既有的已知限制（`regions.py` 模組說明），照實回報低信心。
10. **漸層。** 區域內部顏色不平坦時，在線性光下對每個通道做平面擬合；三個通道的梯度方向一致且殘差在雜訊內，寫成兩個色標的線性漸層。不合再試放射；再不合，沿主方向取樣、以折線簡化求多個色標；仍不合就不向量化這一區。
11. **圖層順序。** 預設輸出「疊放」：被完全包住的區域在上，外圍區域補成完整外形墊在下面（只在補完的部分是直線或圓弧的自然延伸時才補）。不滿足就用互不重疊的拼圖，靠共用邊保證無縫。
12. **重畫驗證，全部寫進輸出欄位。** 把結果用同一個混色空間、4 倍超取樣畫回點陣（貝茲攤平後用 `cv2.fillPoly`），算 `render_error`（平均色差）、`fit_error`（邊界平均距離），再加一項「成塊遺漏」（借 vtracer-bench 的做法）。不過關的區域依序退回：放寬成更多貝茲段 → 交給 VTracer → 保留點陣圖。
13. **VTracer 當備援時的用法。** 餵它第 2、3 步產生的純色塊圖（不是原圖），用 `convert_raw_image_to_svg` 傳 PNG 位元組；實測這樣顏色與路徑數會剛好對上。先留在 0.6.15（已安裝、MIT、介面穩定）；1.0 的無縫 cutout 與 `simplify` 值得要，但介面全改而且還在 alpha，等正式版。0.6.15 有細長條外甩的已知缺陷，所以它的輸出也必須過第 12 步。
14. **像素畫另外處理。** 偵測到最近鄰整數倍放大的小圖（顏色塊邊長一致、沒有混色邊緣）時，先還原到原生格點，直接輸出格線對齊的多邊形。不做 Kopf 與 Lischinski 那種平滑：那是美化不是量測，而且 libdepixelize 的授權沒有確認。

### 3.3 評測怎麼做

- 自建「來回」評測集：拿授權乾淨的向量圖庫（2.4 第 7 條）與自己產生的基本幾何，畫成點陣（不同尺寸、兩種混色空間、PNG 與 JPEG 品質 90、75、50、縮放後再存），描回來，與原始幾何比。
- 指標：邊界距離的均方根與最大值（px）；基本圖形的辨識正確率與參數誤差；節點數相對於原圖的比例；重畫色差；成塊遺漏；每百萬像素秒數。VTracer 0.6.15 預設值當基準線，2.8 的數字就是起點。
- 公開基準（SVG-Bench、MMSVGBench）只當旁證，不當驗收標準。

### 3.4 待驗證的假設

1. 來源圖多半在 sRGB 值上混色。錯的話邊界偏 0.12 到 0.16 px；可以兩種都算，取重畫誤差小的那個。
2. 縮放過的圖（邊緣過渡寬於一個像素、帶過衝）上，0.5 等值線仍然準。沒測。
3. JPEG 4:2:0 會把色度邊緣抹寬；兩側亮度有差時應該只用亮度定位。沒測。
4. 0.2、0.3 px 這些容許值與 3 px 的角點視窗只是初始值，要用 3.3 的評測集校準，並改成以量到的雜訊為單位。
5. 純 NumPy 的實作在 2 到 4 百萬像素的圖上夠快。沒量過；量不過先把熱點改寫成 OpenCV 與 NumPy 的整批運算，再不行才考慮編譯擴充。
6. 規則化不會把手繪風格的標誌「修壞」。第 7 步的不確定度閘門是否足夠，要拿真實標誌看。
7. 補完被遮住的形狀只做直線與圓弧延伸就夠用。Law 與 Kang 的做法（2.5 第 4 條）是日後的升級方向。
8. 細線改筆畫的寬度上限。沒有依據，先以 6 px 試，照評測結果調整。

## 4. 查不到的事

1. **Vectorizer.AI 與 Vector Magic 的實際演算法。** 只有行銷用語；Diebel 論文與產品的關係沒有直接證據。Vectorizer.AI 對文字、漸層的處理也查不到。
2. **所有商用對手的官方定價與完整選項**：官方頁面都打不開（要在工作環境的網路設定放行 vectorizer.ai、vectormagic.com、recraft.ai、canva.com、helpx.adobe.com 才能直讀）。表中價格都是第三方數字。
3. **權重授權**：StarVector（只有搜尋摘要說 Apache 2.0）、OmniSVG 1.1、InternSVG、LLM4SVG；VectorArk 是否釋出。要放行 huggingface.co 才能直讀。
4. **論文細節**：Kopf 與 Lischinski 的四條法則、Hoshyari 與 PolyFit 的規則化做法與程式授權、Du 等的程式授權、Trujillo-Pino 的公式。要放行 arxiv.org、dl.acm.org、johanneskopf.de。
5. **VTracer 官方的演算法說明頁**（www.visioncortex.org/vtracer-docs）讀不到，2.1 的描述全部來自原始碼與版本庫內文件；1.0 正式版時程。
6. **Illustrator 影像描圖面板的完整選項名稱與現行版本的行為**；Image Trace 2.0 如何偵測形狀與漸層。
7. **有幾何標準答案的公開描圖基準**：沒有找到。
8. **學習式模型在 CPU 上的實際速度**：沒有任何來源給數字，所有數字都是顯示卡上的。
9. **Potrace Professional 的價格**（image.md 已記）；libdepixelize 的現行授權；Canva Vectorify 的價格。
10. **任何方法在真實輸入（截圖裡的標誌、掃描的圖表、手機拍的海報）上的表現**：本次實測全是合成圖。

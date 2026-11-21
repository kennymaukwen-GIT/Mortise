# 查證紀錄：影像處理類模型與演算法

- 查證日期：2026-11-20
- 適用產品：Mortise（榫）文件反編譯 SaaS，未來可能有桌面版
- 授權底線：所有元件必須可商用；排除 GPL、AGPL 元件（另有可購買商業授權者列為「需付費」）；排除非商用授權的模型權重
- 範圍：(1) 文件攤平與去彎曲；(2) 去反光、去陰影、影像增強；(3) 背景修補；(4) 點陣轉向量；(5) 文字筆畫分割與文字擦除；外加 OpenCV 本身的授權

## 0. 查證方式與限制

| 來源 | 狀況 |
|---|---|
| 網路搜尋 | 可用 |
| raw.githubusercontent.com | 可直接讀取（LICENSE、README、原始碼與標頭檔） |
| github.com 網頁 | 可透過網頁讀取工具讀取（Releases、Issues、專案首頁） |
| pypi.org、registry.npmjs.org | 可直接讀取（版本、上傳日期、授權欄位） |
| potrace.sourceforge.net | 可直接讀取 |
| huggingface.co | **無法直接讀取**（被工作環境的網路規則擋下）。模型卡內容只能靠搜尋摘要 |
| opencv.org、docs.opencv.org | **無法直接讀取**。改讀 GitHub 上各分支的 LICENSE 與標頭檔 |
| arxiv.org、places2.csail.mit.edu、bfl.ai、igl.ethz.ch、crates.io | **無法直接讀取** |

下文每條事實後面標註查證方式：

- **【直讀】** 直接讀到官方來源的原文（LICENSE、README、原始碼、套件庫中繼資料）
- **【搜尋摘要】** 搜尋工具轉述了官方頁面的內容，但我沒有親自打開該頁
- **【判讀】** 依據已查證的事實做的推論，不是查證結果

讀這份紀錄前要先知道的三件事：

1. **授權有三層**：程式碼授權、權重授權、訓練資料的使用條款。學術專案常常只有第一層寫清楚。權重放在 Google Drive 或 OneDrive、沒有另立授權的情況，下表寫「未另立」，代表**沒有明文**，不代表可以放心用。
2. **LICENSE 檔與 README 互相矛盾的情況不少**（例如 LICENSE 是 MIT，README 卻寫僅限非商業研究）。遇到這種情況一律標「矛盾」，並按較嚴格的一方處理。
3. **品質數字全部是作者自己公布的**，用的是學術基準資料集（DocUNet、Places2、HierText 等）。沒有找到任何一份針對「海報照片」或「繁體中文文件照片」的獨立評測。

## 1. 總表

「可否商用」指用在收費的線上服務；「可否隨桌面版散布」指把程式與權重打包進安裝檔交給使用者。

### 1.0 OpenCV

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **OpenCV 主程式庫（4.5.0 起；現行 5.1.0 與 4.14.0）** | 所有傳統影像處理的基礎 | Apache-2.0（4.4.0 以前是 BSD 三條款） | 不適用 | 可 | 可，需附授權文 | 透視校正、重映射、輪廓、形態學、門檻化、CLAHE、去雜訊、內建修補（Telea、Navier-Stokes） | OpenCV 5 把部分函式搬到新的 `geometry` 模組（C++ 標頭位置改變） | 是 | [LICENSE 4.x](https://github.com/opencv/opencv/blob/4.x/LICENSE)、[LICENSE 5.x](https://github.com/opencv/opencv/blob/5.x/LICENSE)、[Releases](https://github.com/opencv/opencv/releases) |
| **opencv_contrib** | 延伸模組（xphoto、ximgproc、text、intensity_transform） | Apache-2.0；個別檔案標頭仍是 BSD 三條款 | 不適用 | 可 | 可 | 白平衡、Shiftmap 與 FSR 修補、Sauvola 等二值化、細線化、筆畫寬度轉換 | `xfeatures2d` 含非自由演算法（SURF），官方 Python 套件不收 | 是 | [LICENSE](https://github.com/opencv/opencv_contrib/blob/4.x/LICENSE)、[模組清單](https://github.com/opencv/opencv_contrib/blob/4.x/modules/README.md) |
| **opencv-python 系列套件（5.1.0.95）** | Python 預編譯套件 | 打包腳本 MIT；OpenCV 本體 Apache-2.0 | 不適用 | 可 | 可，但要處理內附的 LGPL 元件 | 四種套件：一般、contrib、headless、contrib-headless | **所有套件內附 FFmpeg（LGPLv2.1）；非 headless 的 Linux 套件內附 Qt 5（LGPLv3）** | 是 | [README](https://github.com/opencv/opencv-python/blob/5.x/README.md)、[PyPI](https://pypi.org/project/opencv-python-headless/) |

### 1.1 文件攤平與去彎曲

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **OpenCV 四角透視校正** | 平面文件的歪斜與透視校正 | Apache-2.0 | 不適用 | 可 | 可 | 找輪廓、多邊形近似、求透視矩陣、重新取樣 | 只能處理平面；彎曲、摺痕無效；邊界不清楚時找不到四角 | 是 | [imgproc.hpp 4.x](https://github.com/opencv/opencv/blob/4.x/modules/imgproc/include/opencv2/imgproc.hpp) |
| **DocAligner 1.1.1** | 用模型找文件四個角 | Apache-2.0 | 未另立（隨套件） | 可 | 可（權重授權無明文） | 熱圖迴歸找四角；ONNX Runtime 推論 | 只輸出四個角點，等於只做透視校正 | 部分 | [LICENSE](https://github.com/DocsaidLab/DocAligner/blob/main/LICENSE)、[PyPI](https://pypi.org/project/docaligner-docsaid/) |
| **UVDoc（原始版）** | 彎曲頁面攤平 | MIT | MIT（權重就在程式庫內，`model/best_model.pkl`，約 32 MB） | 可 | 可 | 輸入縮到 488×712，預測 45×31 的網格，再用雙線性取樣套回原圖；作者自報在 UVDoc 基準 MS-SSIM 0.785，高於 DocGeoNet 0.706、DocTr 0.697 | 訓練用到 Doc3D 資料集，其使用條款未查到；網格粗，細部摺痕可能修不平 | 是（訓練資料條款未查到） | [LICENSE](https://github.com/tanguymagne/UVDoc/blob/main/LICENSE)、[README](https://github.com/tanguymagne/UVDoc/blob/main/README.md)、[utils.py](https://github.com/tanguymagne/UVDoc/blob/main/utils.py) |
| **PaddleOCR 的 UVDoc 模組（文件前處理管線）** | 同上，與 OCR 管線共用推論環境 | Apache-2.0 | 模型卡授權欄未能讀到（PaddleOCR 專案整體為 Apache-2.0） | 可 | 可（權重授權待補） | DocUNet 基準字元錯誤率 0.179；模型 30.3 MB；T4 上 19.05 毫秒；同管線另有四方向分類器（準確率 99.06%，7 MB） | 官方文件註明此模組不支援微調，只能推論 | 部分 | [模組文件](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/module_usage/text_image_unwarping.en.md)、[管線文件](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/doc_preprocessor.en.md) |
| **DocRes** | 一個模型做五件事：攤平、去陰影、外觀增強、去模糊、二值化 | MIT | 未另立（OneDrive 下載） | 可（程式碼） | 權重授權無明文 | 以任務提示切換；另需 MBD 模型切出文件範圍 | 權重與多個訓練資料集的條款未查到；去陰影與外觀增強在推論時把長邊上限設為 1600 | 部分 | [LICENSE](https://github.com/ZZZHANG-jx/DocRes/blob/master/LICENSE)、[README](https://github.com/ZZZHANG-jx/DocRes/blob/master/README.md)、[inference.py](https://github.com/ZZZHANG-jx/DocRes/blob/master/inference.py) |
| **page-dewarp 0.3.4** | 不用模型的書頁攤平 | MIT | 不適用（最佳化法，無權重） | 可 | 可 | 以三次曲面模型擬合頁面；可選 JAX 加速 | 作法依賴頁面上的文字行（細節在其文件站，未直讀）；海報這類文字稀少的畫面預期不適用【判讀】 | 是（授權） | [LICENSE](https://github.com/lmmx/page-dewarp/blob/master/LICENSE)、[PyPI](https://pypi.org/project/page-dewarp/) |
| **DewarpNet / PaperEdge** | 彎曲頁面攤平（較早的方法） | MIT / MIT | 未另立（Google Drive 下載） | 可（程式碼） | 權重授權無明文 | 學術基準上的基線方法 | 在 UVDoc README 的比較表中，DewarpNet 的數字低於 UVDoc；PaperEdge 不在該表內，未查到比較 | 部分 | [DewarpNet](https://github.com/cvlab-stonybrook/DewarpNet)、[PaperEdge](https://github.com/cvlab-stonybrook/PaperEdge) |
| **DocTr、DocTr++、DocGeoNet、DocScanner** | 彎曲頁面攤平 | **自訂非商用授權**（Hao Feng，2024） | 同左 | **不可**（商用須取得作者書面許可） | **不可** | — | 授權第 2.2 條：未經作者事先書面許可，嚴禁任何商業使用 | 是 | [DocTr LICENSE.md](https://github.com/fh2019ustc/DocTr/blob/main/LICENSE.md)、[DocTr-Plus](https://github.com/fh2019ustc/DocTr-Plus/blob/master/LICENSE.md)、[DocGeoNet](https://github.com/fh2019ustc/DocGeoNet/blob/main/LICENSE.md)、[DocScanner](https://github.com/fh2019ustc/DocScanner/blob/main/LICENSE.md) |
| **DvD（擴散式攤平，2025）** | 彎曲頁面攤平 | **AGPL-3.0** | 未查 | 排除 | 排除 | — | AGPL | 是 | [LICENSE](https://github.com/hanquansanren/DvD/blob/master/LICENSE) |
| **AADD（AAAI 2026）、DRCCBI（2025）** | 彎曲頁面攤平 | AADD：README 徽章寫 Apache 2.0，但找不到 LICENSE 檔，程式碼尚未釋出；DRCCBI：沒有 LICENSE 檔 | 未釋出或未查 | 目前不可用 | 目前不可用 | — | 沒有授權檔的程式碼預設不能用 | 是（現況） | [AADD](https://github.com/chaoyunwang/aadd)、[DRCCBI](https://github.com/HorizonParadox/DRCCBI) |

### 1.2 去反光、去陰影、影像增強

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **OpenCV 傳統作法** | 去陰影、亮度均勻化、對比、白平衡、去雜訊 | Apache-2.0 | 不適用 | 可 | 可 | 形態學加中值模糊估背景、CLAHE、自適應門檻、非局部平均去雜訊；contrib 有白平衡（SimpleWB、GrayworldWB、LearningBasedWB）與對比調整（gamma、BIMEF） | 反光把像素打到全白時，資訊已經不存在，任何傳統法都救不回來【判讀】 | 是（函式存在） | [imgproc.hpp](https://github.com/opencv/opencv/blob/4.x/modules/imgproc/include/opencv2/imgproc.hpp)、[photo.hpp](https://github.com/opencv/opencv/blob/4.x/modules/photo/include/opencv2/photo.hpp)、[white_balance.hpp](https://github.com/opencv/opencv_contrib/blob/4.x/modules/xphoto/include/opencv2/xphoto/white_balance.hpp) |
| **DocRes（去陰影、外觀增強、去模糊）** | 困難案例的模型式增強 | MIT | 未另立 | 可（程式碼） | 權重授權無明文 | 見 1.1 | 見 1.1 | 部分 | 見 1.1 |
| **DocShadow-SD7K（ICCV 2023）** | 高解析度文件去陰影 | MIT | 未另立 | 可（程式碼） | 權重授權無明文 | 搭配 2K 解析度的 SD7K 資料集 | SD7K 資料集條款未查到 | 部分 | [LICENSE](https://github.com/CXH-Research/DocShadow-SD7K/blob/main/LICENSE) |
| **DocDiff** | 文件去模糊、去雜訊、二值化、去浮水印與印章 | MIT | 未另立 | 可（程式碼） | 權重授權無明文 | README 自述為輕量的殘差預測擴散模型 | 權重授權與訓練資料條款未查 | 部分 | [LICENSE](https://github.com/Royalvice/DocDiff/blob/main/LICENSE)、[README](https://github.com/Royalvice/DocDiff/blob/main/README.md) |
| **Real-ESRGAN、NAFNet、Restormer、SwinIR** | 通用超解析與復原 | BSD-3-Clause、MIT、MIT、Apache-2.0 | 未另立 | 可（程式碼） | 權重授權無明文 | 通用影像放大、去雜訊、去模糊 | 生成式放大可能改掉小字的筆畫，不應用在要辨識的文字區【判讀】 | 是（程式碼授權） | [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN/blob/master/LICENSE)、[NAFNet](https://github.com/megvii-research/NAFNet/blob/main/LICENSE)、[Restormer](https://github.com/swz30/Restormer/blob/main/LICENSE.md)、[SwinIR](https://github.com/JingyunLiang/SwinIR/blob/main/LICENSE) |
| **文件去反光模型（DocHR14K 與 L2HRNet、SHDocs、TSHRNet）** | 去除文件上的鏡面反光 | **未查到** | **未查到** | 未知 | 未知 | 只確認論文存在 | 沒有找到任何一個附明確授權、可下載權重的文件去反光模型 | 否 | [DocHR14K 論文](https://arxiv.org/abs/2504.14238)、[SHDocs](https://neurips.cc/virtual/2024/poster/97715)、[TSHRNet 論文](https://arxiv.org/pdf/2108.06881) |

### 1.3 背景修補（擦掉文字後補回底圖）

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **OpenCV `inpaint`（Telea、Navier-Stokes）** | 小面積、平滑底色的修補 | Apache-2.0 | 不適用 | 可 | 可 | 不需模型、CPU 即時；4.x 與 5.x 都在主程式庫的 photo 模組 | 大面積或有紋理的底會糊成一片【判讀，業界共識，未附官方來源】 | 是 | [photo.hpp 4.x](https://github.com/opencv/opencv/blob/4.x/modules/photo/include/opencv2/photo.hpp)、[photo.hpp 5.x](https://github.com/opencv/opencv/blob/5.x/modules/photo/include/opencv2/photo.hpp) |
| **OpenCV contrib `xphoto::inpaint`（Shiftmap、FSR）** | 以樣本搬移或頻率選擇重建做修補 | Apache-2.0（檔案標頭 BSD 三條款） | 不適用 | 可 | 可 | 三種模式：SHIFTMAP、FSR_BEST、FSR_FAST | 需要 contrib 套件；品質未查到評測 | 是 | [inpainting.hpp](https://github.com/opencv/opencv_contrib/blob/4.x/modules/xphoto/include/opencv2/xphoto/inpainting.hpp) |
| **LaMa（big-lama）** | 通用大面積修補，業界最常用 | Apache-2.0 | **沒有明文**。官方 README 只給下載連結；2023 年協作者回覆「I'm quite sure that repo says apache 2.0」；2026 年 8 至 9 月新增四則詢問權重授權的議題，其中已打開查看的議題 366 沒有維護者回覆 | 可（程式碼）；權重需法務判斷 | 權重需法務判斷 | 訓練解析度 256×256，作者稱可泛化到約 2K；有高解析度精修選項；對週期性紋理的補全表現好 | 權重授權無明文；訓練資料為 Places，其條款見 2.4 節；OpenCV 官方模型庫以 Apache 授權收錄了 ONNX 版，可作旁證但不是原作者聲明 | 是（現況） | [LICENSE](https://github.com/advimman/lama/blob/main/LICENSE)、[README](https://github.com/advimman/lama/blob/main/README.md)、[議題 230](https://github.com/advimman/lama/issues/230)、[議題 366](https://github.com/advimman/lama/issues/366)、[OpenCV Zoo](https://github.com/opencv/opencv_zoo/tree/main/models/inpainting_lama) |
| **MI-GAN（ICCV 2023，Picsart）** | 輕量修補，為行動裝置設計 | MIT | **MIT（另有 LICENSE-WEIGHTS 檔明文授權）** | 可 | 可 | 256 與 512 兩種模型；官方提供轉 ONNX 的腳本，整條管線打包成單一 ONNX | 512 解析度，大圖要切塊或縮放；同樣以 Places2 訓練；在文件底圖上的品質未查到 | 是 | [LICENSE](https://github.com/Picsart-AI-Research/MI-GAN/blob/main/LICENSE)、[LICENSE-WEIGHTS](https://github.com/Picsart-AI-Research/MI-GAN/blob/main/LICENSE-WEIGHTS)、[README](https://github.com/Picsart-AI-Research/MI-GAN/blob/main/README.md) |
| **IOPaint（原 lama-cleaner）** | 把多種修補模型包成一個工具 | Apache-2.0 | 依各模型 | 可（程式碼） | 依各模型 | 內含 LaMa、MI-GAN、擴散模型等的推論包裝 | **原始庫已於 2025-08-13 封存**，最後一版 1.6.0（2025-03-18）；只能當參考實作 | 是 | [GitHub](https://github.com/Sanster/IOPaint)、[PyPI](https://pypi.org/project/iopaint/) |
| **MAT、DeepFill v2** | 修補 | **CC BY-NC 4.0** | 同左 | **不可** | **不可** | — | 非商用 | 是 | [MAT LICENSE](https://github.com/fenglinglwb/MAT/blob/main/LICENSE)、[DeepFill v2 LICENSE](https://github.com/JiahuiYu/generative_inpainting/blob/master/LICENSE) |
| **ZITS、AOT-GAN** | 修補 | Apache-2.0 | 未另立 | 可（程式碼） | 權重授權無明文 | — | 未細查 | 部分 | [ZITS](https://github.com/DQiaole/ZITS_inpainting/blob/main/LICENSE)、[AOT-GAN](https://github.com/researchmm/AOT-GAN-for-Inpainting/blob/master/LICENSE) |
| **Stable Diffusion 修補模型（1.x、2、XL）** | 擴散式修補 | 程式碼 MIT（Stability 的庫） | 1.x：CreativeML Open RAIL-M；2 與 XL：CreativeML Open RAIL++-M | 可，但授權內的使用限制條款必須轉嫁給使用者 | 可，同樣要轉嫁使用限制條款 | 可依文字提示補出複雜內容 | 會重新生成像素，可能補出假文字或不存在的細節；需要 GPU；1.x 修補模型的原始發布庫已停用，現為鏡像 | 是（授權全文）；模型卡為搜尋摘要 | [SD 1.x 授權全文](https://github.com/CompVis/stable-diffusion/blob/main/LICENSE)、[SDXL 授權全文](https://github.com/Stability-AI/generative-models/blob/main/model_licenses/LICENSE-SDXL1.0)、[SDXL 修補模型卡](https://huggingface.co/diffusers/stable-diffusion-xl-1.0-inpainting-0.1) |
| **FLUX.1 Fill [dev]** | 擴散式修補與外擴 | Apache-2.0（推論程式） | **FLUX.1-dev 非商用授權** | **需付費**（可向 Black Forest Labs 購買商業授權） | **需付費** | 官方的修補專用模型 | 價格未查到 | 是 | [README](https://github.com/black-forest-labs/flux/blob/main/README.md) |
| **FLUX.2 [klein] 4B** | 擴散式影像編輯 | Apache-2.0 | Apache-2.0 | 可 | 可 | 文字生圖與影像編輯；約 8 GB 顯示記憶體可跑；2026-01-15 釋出 | 是編輯模型，不是遮罩式修補；9B 版本與 FLUX.2 [dev] 為非商用授權 | 是 | [README](https://github.com/black-forest-labs/flux2/blob/main/README.md) |
| **Qwen-Image-Edit、Qwen-Image-Layered** | 擴散式影像編輯；把影像分解成多個 RGBA 圖層 | Apache-2.0 | Apache-2.0（Edit 見 README；Layered 的模型卡為搜尋摘要） | 可 | 可 | Layered 可直接輸出圖層並匯出 PPTX、PSD；Edit 為 20B 參數的基礎模型家族 | 模型極大；Layered 建議解析度只有 640；會重新生成像素 | 是（Layered 模型卡為搜尋摘要） | [Qwen-Image](https://github.com/QwenLM/Qwen-Image)、[Qwen-Image-Layered](https://github.com/QwenLM/Qwen-Image-Layered) |
| **PowerPaint、BrushNet** | 以 Stable Diffusion 為底的修補 | MIT、Apache-2.0（第三方元件除外） | 未查 | 可（程式碼） | 未查 | — | 權重授權未查 | 部分 | [PowerPaint](https://github.com/open-mmlab/PowerPaint/blob/main/LICENSE)、[BrushNet](https://github.com/TencentARC/BrushNet/blob/main/LICENSE) |

### 1.4 點陣轉向量

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **VTracer（穩定版 0.6.15；1.0.0-alpha.4）** | 彩色點陣圖轉 SVG | MIT（npm 套件標 MIT OR Apache-2.0） | 不適用 | 可 | 可 | 直接處理彩色影像；預設組 bw、poster、photo；擬合模式 pixel、polygon、spline；1.0 新增自適應門檻（給光線不均的掃描）、曲線簡化、固定調色盤、無縫拼接；Rust、Python、Node（WebAssembly）三種介面 | 只輸出 SVG；README 沒有提到中心線描圖；1.0 還在 alpha | 是 | [LICENSE](https://github.com/visioncortex/vtracer/blob/master/LICENSE)、[README](https://github.com/visioncortex/vtracer/blob/master/README.md)、[PyPI](https://pypi.org/project/vtracer/)、[npm](https://registry.npmjs.org/@visioncortex/vtracer) |
| **Potrace 1.16** | 黑白點陣圖轉向量，品質標竿 | **GPL v2 或更新版** | 不適用 | **需付費**（Potrace Professional，向 Icosasoft 購買） | **需付費** | 輸出 SVG、PDF、EPS、DXF 等；曲線平滑 | 只吃黑白點陣圖，彩色與灰階要先用附帶的 mkbitmap 前處理；最後一版是 2019-09-17；商業授權價格未公開 | 是 | [官方頁](https://potrace.sourceforge.net/) |
| **Potrace 的各語言移植版** | 同上 | 幾乎都是 GPL（potracer、pypotrace、npm 的 potrace、esm-potrace-wasm、oslllo-potrace、SVGcode） | 不適用 | 排除 | 排除 | — | **`ts-potrace` 在 npm 標 MIT，但 Potrace 作者明文要求移植版必須遵守 GPL，兩者矛盾，不可採用** | 是 | [官方頁的移植說明](https://potrace.sourceforge.net/)、[npm potrace](https://registry.npmjs.org/potrace)、[ts-potrace](https://registry.npmjs.org/ts-potrace) |
| **AutoTrace 0.31.11** | 點陣圖轉向量，支援中心線 | 主程式 **GPL v2 或更新版**；函式庫 `libautotrace` 為 **LGPL v2.1 或更新版** | 不適用 | 主程式排除；函式庫可用但有 LGPL 義務 | 函式庫可，須動態連結並允許使用者替換 | **輪廓與中心線描圖**、減色、去斑點；輸出 SVG、EPS、PDF、DXF | 需要 GLib；Python 綁定 pyautotrace 標 GPL-2.0-or-later | 是 | [README](https://github.com/autotrace/autotrace/blob/master/README.md)、[Releases](https://github.com/autotrace/autotrace/releases) |
| **ImageTracer.js 1.2.6** | 純 JavaScript 的彩色描圖 | Unlicense（公有領域） | 不適用 | 可 | 可 | 瀏覽器與 Node 都能跑 | 最後一版是 2020-05-18 | 是 | [LICENSE](https://github.com/jankovicsandras/imagetracerjs/blob/master/LICENSE)、[npm](https://registry.npmjs.org/imagetracerjs) |
| **自建管線：OpenCV 輪廓加曲線擬合（例如 kurbo）** | 把簡單圖形還原成原生形狀 | Apache-2.0；kurbo 為 Apache-2.0 OR MIT | 不適用 | 可 | 可 | 可以針對矩形、圓、直線做形狀辨識，輸出成簡報軟體的原生圖形而不是一堆貝茲曲線【判讀】 | 要自己寫 | 是（授權） | [kurbo](https://github.com/linebender/kurbo)、[geometry/2d.hpp](https://github.com/opencv/opencv/blob/5.x/modules/geometry/include/opencv2/geometry/2d.hpp) |
| **DiffVG、LIVE** | 以可微分算繪做最佳化式向量化 | Apache-2.0 | 不適用或未查 | 可 | 可 | 研究用途 | 逐張最佳化，速度慢【判讀】 | 是（授權） | [DiffVG](https://github.com/BachiLi/diffvg/blob/master/LICENSE)、[LIVE](https://github.com/Picsart-AI-Research/LIVE-Layerwise-Image-Vectorization/blob/main/LICENSE) |
| **StarVector、OmniSVG** | 用視覺語言模型直接生成 SVG 程式碼 | Apache-2.0 | 未查（模型卡無法讀取）；OmniSVG 的訓練資料 MMSVG 為 **CC BY-NC-SA 4.0** | 程式碼可；權重未確認 | 未確認 | 圖示、標誌、插畫 | OmniSVG 需要 17 至 26 GB 顯示記憶體；輸出是模型「重畫」的結果，不保證與原圖一致【判讀】 | 部分 | [StarVector](https://github.com/joanrod/star-vector)、[OmniSVG](https://github.com/OmniSVG/OmniSVG) |

### 1.5 文字筆畫分割與文字擦除

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **傳統二值化（OpenCV ximgproc、Doxa、scikit-image）** | 在 OCR 文字框內求筆畫遮罩 | Apache-2.0、CC0、BSD-3-Clause | 不適用 | 可 | 可 | Niblack、Sauvola、Wolf、NICK 等局部門檻法；細線化 | 字壓在複雜照片上、或字色與底色接近時會失敗【判讀】 | 是（授權與函式） | [ximgproc.hpp](https://github.com/opencv/opencv_contrib/blob/4.x/modules/ximgproc/include/opencv2/ximgproc.hpp)、[Doxa](https://github.com/brandonmpetty/Doxa/blob/master/LICENSE)、[scikit-image](https://github.com/scikit-image/scikit-image/blob/main/LICENSE.txt) |
| **Hi-SAM（TPAMI）** | 筆畫級文字分割，另可分出詞、行、段落 | Apache-2.0 | 未另立（OneDrive 下載）。三組權重分別用 Total-Text、**TextSeg（僅限學術）**、HierText（標註為 CC BY-SA 4.0）訓練 | 程式碼可；只有 HierText 版的權重有可能用，且需法務判斷 | 需法務判斷 | 作者自報 HierText 筆畫 F 值 79.78 至 83.36；有滑動視窗模式處理小字 | 需另外下載 SAM 的影像編碼器權重；TextSeg 版權重不可商用 | 是（現況） | [LICENSE](https://github.com/ymy-k/Hi-SAM/blob/main/LICENSE)、[README](https://github.com/ymy-k/Hi-SAM/blob/main/README.md)、[HierText](https://github.com/google-research-datasets/hiertext) |
| **SAM、SAM 2** | 通用分割，可當自訓筆畫分割的基底 | Apache-2.0 | 隨專案 | 可 | 可 | 提示式分割 | 不是為筆畫設計；SAM 3 改用自訂的 SAM License | 是 | [SAM](https://github.com/facebookresearch/segment-anything/blob/main/LICENSE)、[SAM 2](https://github.com/facebookresearch/sam2/blob/main/LICENSE)、[SAM 3](https://github.com/facebookresearch/sam3/blob/main/LICENSE) |
| **EraseNet、CTRNet** | 場景文字擦除 | LICENSE 檔是 MIT，但 README 寫「商業使用請聯絡金連文教授」【矛盾】 | 以 SCUT-EnsText 訓練 | **不可**（未取得許可前） | **不可** | — | SCUT-EnsText 僅限非商業研究 | 是 | [EraseNet](https://github.com/lcy0604/EraseNet)、[CTRNet](https://github.com/lcy0604/CTRNet)、[SCUT-EnsText](https://github.com/HCIILAB/SCUT-EnsText) |
| **ViTEraser** | 場景文字擦除 | LICENSE 檔是 MIT，但 README 寫「只能用於非商業研究」【矛盾】 | 同左 | **不可** | **不可** | — | 同上 | 是 | [README](https://github.com/shannanyinxiang/ViTEraser/blob/main/README.md) |
| **GaRNet（NAVER）** | 場景文字擦除 | Apache-2.0 | 以合成資料加 SCUT-EnsText 訓練；README 自己提醒該資料集僅限非商業研究 | 程式碼可；**預訓練權重不可** | **不可**（權重） | 架構可拿來自己訓練 | 要自備可商用的訓練資料 | 是 | [README](https://github.com/naver/garnet/blob/master/README.md) |
| **DeepEraser** | 通用文字擦除（需給遮罩） | MIT | 未另立；以 SCUT-EnsText 等訓練 | 程式碼可；權重有風險 | 權重有風險 | 給遮罩擦文字 | 同上 | 是 | [LICENSE](https://github.com/fh2019ustc/DeepEraser/blob/master/LICENSE)、[README](https://github.com/fh2019ustc/DeepEraser/blob/master/README.md) |
| **TextSeg / TexRNet** | 文字分割資料集與模型 | 沒有 LICENSE 檔 | 資料集僅限學術 | **不可** | **不可** | — | README：資料集不得用於任何商業專案與研究 | 是 | [README](https://github.com/SHI-Labs/Rethinking-Text-Segmentation/blob/main/README.md) |
| **DocEnTR** | 文件二值化模型 | **CC BY-NC 4.0** | 同左 | **不可** | **不可** | — | 非商用 | 是 | [LICENSE](https://github.com/dali92002/DocEnTR/blob/main/LICENSE) |
| **manga-image-translator、comic-text-detector、BallonsTranslator** | 漫畫文字偵測、擦除、重排 | **GPL-3.0** | 未查 | 排除 | 排除 | 流程與本產品相似，可當設計參考 | GPL | 是 | [manga-image-translator](https://github.com/zyddnys/manga-image-translator/blob/main/LICENSE)、[comic-text-detector](https://github.com/dmMaze/comic-text-detector/blob/master/LICENSE) |
| **UPOCR、TextCtrl、DiffUTE、PSSTRNet、FETNet** | 文字擦除或編輯 | **沒有 LICENSE 檔** | — | 預設不可用 | 預設不可用 | — | 沒有授權就沒有使用權 | 是（現況） | [UPOCR](https://github.com/shannanyinxiang/UPOCR)、[TextCtrl](https://github.com/weichaozeng/TextCtrl)、[DiffUTE](https://github.com/Onkarsus13/DiffUTE) |
| **SauvolaNet、kraken、AnyText** | 學習式二值化；OCR 工具內的二值化；文字生成與編輯 | MIT、Apache-2.0、Apache-2.0 | 未查 | 可（程式碼） | 未查 | — | 只確認程式碼授權 | 部分 | [SauvolaNet](https://github.com/Leedeng/SauvolaNet/blob/main/LICENSE)、[kraken](https://github.com/mittagessen/kraken/blob/main/LICENSE)、[AnyText](https://github.com/tyxsspa/AnyText/blob/main/LICENSE) |

## 2. 逐條事實與來源

### 2.1 OpenCV

1. OpenCV 的 `4.x`、`5.x`、`master` 三個分支的 LICENSE 檔都是 Apache License 2.0。【直讀】[4.x](https://github.com/opencv/opencv/blob/4.x/LICENSE)、[5.x](https://github.com/opencv/opencv/blob/5.x/LICENSE)
2. OpenCV 官網授權頁說明：4.5.0 及之後為 Apache 2；4.4.0 及之前（含所有 3.x、2.x）為 BSD 三條款。【搜尋摘要】[opencv.org 授權頁](https://opencv.org/?p=13)、[4.5.0 發布公告](https://opencv.org/opencv-4-5-0/)
3. GitHub Releases 頁面顯示的最新版本依序為 5.1.0（11 月 6 日）、4.14.0（7 月 19 日）、5.0.0（6 月 6 日）。頁面不顯示年份；PyPI 上 `opencv-python-headless` 的對應版本上傳日為 5.1.0.95（2026-11-17）、4.14.0.94（2026-07-29）、5.0.0.93（2026-07-02），可確認是 2026 年。【直讀】[Releases](https://github.com/opencv/opencv/releases)、[PyPI](https://pypi.org/project/opencv-python-headless/)
4. opencv_contrib 的 LICENSE 檔是 Apache-2.0；但個別檔案的標頭仍寫 BSD 三條款（例如 `xphoto/inpainting.hpp`）。兩者都是寬鬆授權，散布時兩種授權文都要保留。【直讀】[LICENSE](https://github.com/opencv/opencv_contrib/blob/4.x/LICENSE)、[inpainting.hpp](https://github.com/opencv/opencv_contrib/blob/4.x/modules/xphoto/include/opencv2/xphoto/inpainting.hpp)
5. opencv-python 的 README「Licensing」一節原文重點：打包腳本為 MIT；OpenCV 本體為 Apache 2；**所有套件都內附 FFmpeg（LGPLv2.1）；非 headless 的 Linux 套件內附 Qt 5（LGPLv3）**；完整清單在 `LICENSE-3RD-PARTY.txt`。【直讀】[README](https://github.com/opencv/opencv-python/blob/5.x/README.md)
6. 同一份 README：非自由演算法（如 SURF）因專利與授權問題不包含在預編譯套件內；SIFT 因專利到期，自 4.3.0 與 3.4.10 起已包含。【直讀】同上
7. 主程式庫 photo 模組的修補函式 `cv::inpaint` 有兩種方法：`INPAINT_NS`（Navier-Stokes）與 `INPAINT_TELEA`（Telea 2004）。4.x 與 5.x 都有。同模組另有非局部平均去雜訊、無縫複製等。【直讀】[photo.hpp 4.x](https://github.com/opencv/opencv/blob/4.x/modules/photo/include/opencv2/photo.hpp)、[photo.hpp 5.x](https://github.com/opencv/opencv/blob/5.x/modules/photo/include/opencv2/photo.hpp)
8. contrib 的 `xphoto::inpaint` 有三種方法：`INPAINT_SHIFTMAP`、`INPAINT_FSR_BEST`、`INPAINT_FSR_FAST`。【直讀】[inpainting.hpp](https://github.com/opencv/opencv_contrib/blob/4.x/modules/xphoto/include/opencv2/xphoto/inpainting.hpp)
9. contrib 的 `ximgproc::niBlackThreshold` 支援 Niblack、Sauvola、Wolf、NICK 四種局部二值化；`ximgproc::thinning` 支援 Zhang-Suen 與 Guo-Hall 細線化。contrib 的 text 模組有筆畫寬度轉換 `detectTextSWT`。【直讀】[ximgproc.hpp](https://github.com/opencv/opencv_contrib/blob/4.x/modules/ximgproc/include/opencv2/ximgproc.hpp)、[swt_text_detection.hpp](https://github.com/opencv/opencv_contrib/blob/4.x/modules/text/include/opencv2/text/swt_text_detection.hpp)
10. **OpenCV 5 的模組重組**：在 4.x，`approxPolyDP` 與 `getPerspectiveTransform` 宣告在 `imgproc.hpp`；在 5.x 分支，這兩個函式宣告在新的 `geometry` 模組（`opencv2/geometry/2d.hpp`），`imgproc.hpp` 裡已經沒有。`findContours`、`warpPerspective`、`remap`、`createCLAHE`、`adaptiveThreshold` 在 5.x 仍在 `imgproc.hpp`。Python 介面是否受影響未驗證。【直讀】[imgproc.hpp 5.x](https://github.com/opencv/opencv/blob/5.x/modules/imgproc/include/opencv2/imgproc.hpp)、[geometry/2d.hpp](https://github.com/opencv/opencv/blob/5.x/modules/geometry/include/opencv2/geometry/2d.hpp)
11. OpenCV 官方模型庫（opencv_zoo）收錄了 `inpainting_lama`：需要 OpenCV 5.0.0 以上；模型來源是 Hugging Face 上的 `Carve/LaMa-ONNX`；該目錄下所有檔案以 Apache License 授權。【直讀】[README](https://github.com/opencv/opencv_zoo/blob/main/models/inpainting_lama/README.md)

### 2.2 文件攤平與去彎曲

12. UVDoc 原始庫：MIT（2023，Tanguy Magne）。README 寫明模型就在庫內 `model/best_model.pkl`（檔案 32,158,393 位元組）。程式碼中輸入尺寸為 488×712，網格為 45×31，並以雙線性取樣把網格套回原尺寸影像。【直讀】[LICENSE](https://github.com/tanguymagne/UVDoc/blob/main/LICENSE)、[README](https://github.com/tanguymagne/UVDoc/blob/main/README.md)、[utils.py](https://github.com/tanguymagne/UVDoc/blob/main/utils.py)
13. UVDoc README 的勘誤表（作者自報，UVDoc 基準）：UVDoc MS-SSIM 0.785、AD 0.119；DocGeoNet 0.706、0.168；DocTr 0.697、0.160；DewarpNet 0.589、0.193。【直讀】同上
14. UVDoc 訓練用了 UVDoc 資料集與 Doc3D 資料集。兩個資料集的 GitHub 庫 LICENSE 檔都是 MIT，但那是下載腳本與程式碼的授權；Doc3D 影像本身的使用條款在 Hugging Face 資料集卡，未能讀到。【直讀】[UVDoc-Dataset](https://github.com/tanguymagne/UVDoc-Dataset)、[doc3D-dataset](https://github.com/cvlab-stonybrook/doc3D-dataset)
15. PaddleOCR 3.x 的「文字影像校正模組」只有一個模型 UVDoc：DocUNet 基準字元錯誤率 0.179；模型 30.3 MB；T4 顯示卡推論 19.05 毫秒；處理器高效能模式 869.82 毫秒。文件原文註明此模組目前不支援微調訓練，只支援推論。【直讀】[模組文件](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/module_usage/text_image_unwarping.en.md)
16. PaddleOCR 的「文件影像前處理管線」把方向分類（`PP-LCNet_x1_0_doc_ori`，0、90、180、270 度四類，準確率 99.06%，7 MB）與 UVDoc 校正串在一起，兩者都可個別開關。【直讀】[管線文件](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/doc_preprocessor.en.md)、[方向分類文件](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/module_usage/doc_img_orientation_classification.en.md)
17. PaddleOCR 程式庫為 Apache-2.0。Hugging Face 上 `PaddlePaddle/UVDoc` 模型卡的授權欄，兩次搜尋都沒有讀到。【直讀】[LICENSE](https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE)；【搜尋摘要，未確認】[模型卡](https://huggingface.co/PaddlePaddle/UVDoc)
18. DocRes：MIT（2024，Jiaxin Zhang）。推論支援 dewarping、deshadowing、appearance、deblurring、binarization、end2end 六種任務。權重 `docres.pkl` 與 `mbd.pkl` 放在 OneDrive，README 沒有另立權重授權。訓練資料包含 Doc3D（攤平）、RDD 與 FSDSRD（去陰影）、DIBCO 等（二值化）。【直讀】[LICENSE](https://github.com/ZZZHANG-jx/DocRes/blob/master/LICENSE)、[README](https://github.com/ZZZHANG-jx/DocRes/blob/master/README.md)、[data/README.md](https://github.com/ZZZHANG-jx/DocRes/blob/master/data/README.md)
19. DocAligner：Apache-2.0；PyPI 套件 `docaligner-docsaid` 1.1.1（2026-01-13）。README 說明輸出是文件的四個角點，模型為熱圖迴歸，以 ONNX Runtime 推論。【直讀】[LICENSE](https://github.com/DocsaidLab/DocAligner/blob/main/LICENSE)、[README](https://github.com/DocsaidLab/DocAligner/blob/main/README.md)、[PyPI](https://pypi.org/project/docaligner-docsaid/)
20. page-dewarp：MIT（原作者 Matt Zucker，2016）；PyPI 0.3.4（2026-05-12）。README 自述為「使用三次曲面模型的文件影像攤平函式庫」，相依 NumPy、SciPy、SymPy、OpenCV。【直讀】[LICENSE](https://github.com/lmmx/page-dewarp/blob/master/LICENSE)、[README](https://github.com/lmmx/page-dewarp/blob/master/README.md)
21. DewarpNet 與 PaperEdge：LICENSE 都是 MIT（石溪大學電腦視覺實驗室）；權重都放在 Google Drive，沒有另立授權。【直讀】[DewarpNet](https://github.com/cvlab-stonybrook/DewarpNet)、[PaperEdge](https://github.com/cvlab-stonybrook/PaperEdge)
22. DocTr、DocTr++、DocGeoNet、DocScanner 四個庫用的是同一份自訂授權（Hao Feng，2024）：非商業使用免費；第 2.2 條「未經作者事先書面明示許可，嚴禁任何商業使用」；商用須另簽商業授權協議。【直讀】[DocTr LICENSE.md](https://github.com/fh2019ustc/DocTr/blob/main/LICENSE.md)
23. DvD（以座標擴散做攤平，2025）的 LICENSE 是 AGPL-3.0。【直讀】[LICENSE](https://github.com/hanquansanren/DvD/blob/master/LICENSE)
24. AADD（AAAI 2026 口頭報告）：README 徽章寫 Apache 2.0 並連到 LICENSE，但庫內找不到 LICENSE 檔；README 寫「完整程式碼與模型將在定稿後釋出」。【直讀】[README](https://github.com/chaoyunwang/aadd/blob/main/README.md)

### 2.3 去反光、去陰影、影像增強

25. DocRes 的推論程式中，去陰影與外觀增強兩個任務把 `MAX_SIZE` 設為 1600：影像先縮到 1600 處理，再把求得的陰影圖放大套回原圖。【直讀】[inference.py](https://github.com/ZZZHANG-jx/DocRes/blob/master/inference.py)
26. DocShadow-SD7K（ICCV 2023，澳門大學）：MIT。SD7K 是 2K 解析度的文件陰影資料集，用了 30 種以上的遮擋物與 350 份以上的文件。資料集本身的授權未查到。【直讀】[LICENSE](https://github.com/CXH-Research/DocShadow-SD7K/blob/main/LICENSE)、[README](https://github.com/CXH-Research/DocShadow-SD7K/blob/main/README.md)
27. Real-ESRGAN 為 BSD-3-Clause；NAFNet 為 MIT；Restormer 為 MIT；SwinIR 為 Apache-2.0。【直讀】連結見總表
28. opencv_contrib 的 xphoto 模組有三個白平衡類別（`SimpleWB`、`GrayworldWB`、`LearningBasedWB`）；intensity_transform 模組有 `gammaCorrection`、`autoscaling`、`contrastStretching`、`logTransform`、`BIMEF`。【直讀】[white_balance.hpp](https://github.com/opencv/opencv_contrib/blob/4.x/modules/xphoto/include/opencv2/xphoto/white_balance.hpp)、[intensity_transform.hpp](https://github.com/opencv/opencv_contrib/blob/4.x/modules/intensity_transform/include/opencv2/intensity_transform.hpp)
29. 文件去反光：搜尋找到三份研究（DocHR14K 與 L2HRNet，2025；SHDocs，NeurIPS 2024；文字感知的反光去除，2021），但沒有找到任何一個的程式庫授權或可下載權重的確認。【搜尋摘要】[DocHR14K](https://arxiv.org/abs/2504.14238)、[SHDocs](https://neurips.cc/virtual/2024/poster/97715)、[文字感知反光去除](https://arxiv.org/pdf/2108.06881)

### 2.4 背景修補

30. LaMa 程式庫：Apache-2.0。README 寫明「在 256×256 訓練，可泛化到約 2K 解析度」；最佳模型 big-lama 以 Places2 與 Places Challenge 訓練；提供高解析度精修（`refine=True`）。README 的下載方式是 Hugging Face 上第三方上傳的 `smartywu/big-lama` 與作者的 Google Drive（原本的 Yandex 連結已失效）。README 沒有任何關於權重授權的文字。【直讀】[LICENSE](https://github.com/advimman/lama/blob/main/LICENSE)、[README](https://github.com/advimman/lama/blob/main/README.md)
31. **LaMa 權重授權的官方說法只有一句**：2023 年 Inpaint Anything 的作者開議題問「權重的授權是否與程式庫相同（Apache 2.0）」，LaMa 協作者 senya-ashukha 回覆「I'm quite sure that repo says apache 2.0 ;-)」並關閉議題。這句話指向程式庫的授權，沒有明說權重適用。【直讀】[議題 230](https://github.com/advimman/lama/issues/230)
32. 2026 年 8 月 26 日至 9 月 8 日，LaMa 程式庫新增四則要求把權重授權寫清楚的議題（365、366、367、368；367 與 368 標題相同，367 已關閉）。我打開了議題 366：有多位商業使用者跟進詢問（付費 macOS 應用程式、隨需印刷服務等），截至查證日該頁沒有任何維護者的回覆，議題仍為開啟狀態。365 與 368 只看到列表，沒有逐一打開；列表顯示 365 沒有留言。【直讀】[議題列表](https://github.com/advimman/lama/issues?q=license+weights)、[議題 366](https://github.com/advimman/lama/issues/366)
33. Hugging Face 上的 big-lama 鏡像（`smartywu/big-lama` 等）與 ONNX 轉檔（`Carve/LaMa-ONNX`，`lama_fp32.onnx` 約 208 MB）都標 apache-2.0，但都是第三方上傳。【搜尋摘要】[smartywu/big-lama](https://huggingface.co/smartywu/big-lama)、[Carve/LaMa-ONNX](https://huggingface.co/Carve/LaMa-ONNX)
34. Places 資料集：GitHub 庫的 LICENSE 是 MIT；README 寫「預訓練的 Places-CNN 模型可依創用 CC 姓名標示（CC BY）使用……所有影像的著作權屬於影像所有人」。官網說明資料庫與預訓練模型是為「學術研究與教育目的」提供。資料集的完整使用條款未能讀到。【直讀】[places365 README](https://github.com/CSAILVision/places365/blob/master/README.md)；【搜尋摘要】[官網](http://places2.csail.mit.edu)
35. MI-GAN：程式碼 MIT；**另有 `LICENSE-WEIGHTS` 檔，內容是 MIT 授權全文**，README 在下載權重的句子裡直接連到它。預訓練模型有 `migan_512_places2`、`migan_256_places2`、`migan_256_ffhq`。官方提供 `scripts/create_onnx_pipeline.py` 把整條管線轉成 ONNX，輸入是 uint8 的 RGB 影像與灰階遮罩。【直讀】[LICENSE-WEIGHTS](https://github.com/Picsart-AI-Research/MI-GAN/blob/main/LICENSE-WEIGHTS)、[README](https://github.com/Picsart-AI-Research/MI-GAN/blob/main/README.md)
36. IOPaint：Apache-2.0；原始庫頁面顯示「This repository was archived by the owner on Aug 13, 2025」；PyPI 最後一版 1.6.0（2025-03-18）。`simple-lama-inpainting`（另一個 LaMa 包裝）的程式庫也是 Apache-2.0，PyPI 最後一版 0.1.2（2023-07-28）。【直讀】[IOPaint](https://github.com/Sanster/IOPaint)、[PyPI](https://pypi.org/project/iopaint/)、[simple-lama-inpainting](https://github.com/enesmsahin/simple-lama-inpainting)
37. MAT 與 DeepFill v2（`JiahuiYu/generative_inpainting`）的 LICENSE 都是 CC BY-NC 4.0。【直讀】[MAT](https://github.com/fenglinglwb/MAT/blob/main/LICENSE)、[DeepFill v2](https://github.com/JiahuiYu/generative_inpainting/blob/master/LICENSE)
38. CreativeML Open RAIL-M 授權全文（Stable Diffusion 1.x）：第 2 條授予重製、散布、再授權的著作權授權；第 4 條明文允許「為第三方遠端存取（例如軟體即服務）而架設」與散布，條件是必須把第 5 條的使用限制寫進你與使用者之間的協議，並告知下游；第 6 條授權人不主張對輸出的權利；第 7 條授權人保留遠端限制違規使用與更新模型的權利。SDXL 的 Open RAIL++-M 全文也已讀到。【直讀】[SD 1.x 授權](https://github.com/CompVis/stable-diffusion/blob/main/LICENSE)、[SDXL 授權](https://github.com/Stability-AI/generative-models/blob/main/model_licenses/LICENSE-SDXL1.0)
39. 各 Stable Diffusion 修補模型卡的授權標示：1.5 修補版（runwayml 鏡像）為 creativeml-openrail-m，且註明原始庫已停用；2 修補版為 CreativeML Open RAIL++-M；SDXL 修補 0.1 版為 openrail++。【搜尋摘要】[SDXL 修補](https://huggingface.co/diffusers/stable-diffusion-xl-1.0-inpainting-0.1)、[SD 2 修補（Azure 目錄）](https://ai.azure.com/catalog/models/stabilityai-stable-diffusion-2-inpainting)
40. Black Forest Labs 的 `flux` 庫 README 授權表：FLUX.1 [schnell] 為 apache-2.0；FLUX.1 Fill [dev]（修補與外擴）、Kontext [dev]、Canny、Depth、Redux 全部是「FLUX.1-dev Non-Commercial License」。README 另有一節「為商業用途取得模型授權」，指向 bfl.ai 的授權頁。【直讀】[README](https://github.com/black-forest-labs/flux/blob/main/README.md)
41. `flux2` 庫 README：FLUX.2 [klein] 4B 與 4B Base 為 apache-2.0，支援文字生圖與單張、多張參考影像編輯，4B 約 8 GB 顯示記憶體可跑；9B 系列與 FLUX.2 [dev] 為 FLUX 非商用授權。klein 系列於 2026-01-15 釋出。【直讀】[README](https://github.com/black-forest-labs/flux2/blob/main/README.md)
42. Qwen-Image：README「License Agreement」寫「Qwen-Image is licensed under Apache 2.0」；為 20B 參數的 MMDiT 模型；影像編輯版最新為 Qwen-Image-Edit-2511（2025-12-23）。【直讀】[README](https://github.com/QwenLM/Qwen-Image/blob/main/README.md)
43. Qwen-Image-Layered：程式庫 LICENSE 為 Apache-2.0；可把一張影像分解成多個 RGBA 圖層；附的工具可把圖層匯出成 pptx、zip、psd；範例程式註解寫「此版本建議使用 640 解析度」；README 註明釋出的權重是針對「影像轉多層 RGBA」微調的。模型卡授權為 Apache-2.0（由鏡像與轉載確認）。【直讀】[README](https://github.com/QwenLM/Qwen-Image-Layered/blob/main/README.md)；【搜尋摘要】[模型卡](https://huggingface.co/Qwen/Qwen-Image-Layered)
44. PatchMatch 演算法有 Adobe 的專利（US 8,285,055 與 US 8,811,749，「Determining correspondence between image regions」，2009 年申請）；Adobe 釋出的參考程式碼僅限非商業研究使用。專利的到期日與現況未查到。OpenCV 的 Shiftmap 與 FSR 是不同的演算法。【搜尋摘要】[專利資料](https://uspto.report/patent/grant/8811749)、[PatchMatch 專案頁](https://www.cs.princeton.edu/gfx/pubs/Barnes_2009_PAR/)

### 2.5 點陣轉向量

45. Potrace 官方頁：版本 1.16（2019-09-17）；授權為 GNU GPL 第 2 版或之後版本；「雙重授權計畫」一節寫明有非 GPL 版本「Potrace Professional」可供整合進專有軟體，授權由作者的公司 Icosasoft Software Inc. 提供，洽詢信箱 licensing@icosasoft.ca。【直讀】[官方頁](https://potrace.sourceforge.net/)
46. Potrace 官方頁：輸入是點陣圖（PBM、PGM、PPM、BMP）；輸出後端有 SVG、PDF、EPS、PostScript、DXF、GeoJSON、PGM、Gimppath、XFig；隨附的 mkbitmap 用來對灰階與彩色影像做前處理。【直讀】同上
47. Potrace 官方頁「關於移植版的重要通知」：原始碼以 GPL 授權，「Potrace」名稱是作者的商標；作者只允許遵守 GPL 且輸出品質相同的移植版使用此名稱。【直讀】同上
48. 各移植版在套件庫登記的授權：`potracer`（PyPI）GPLv2+；`pypotrace` GPL；npm 的 `potrace` GPL-2.0；`esm-potrace-wasm` GPL-2.0；`oslllo-potrace` GPL-2.0；SVGcode GPL-2.0。**`ts-potrace` 登記為 MIT，與上一條的作者聲明矛盾。**【直讀】[PyPI potracer](https://pypi.org/project/potracer/)、[npm potrace](https://registry.npmjs.org/potrace)、[npm ts-potrace](https://registry.npmjs.org/ts-potrace)、[SVGcode](https://github.com/tomayac/SVGcode/blob/main/LICENSE)
49. AutoTrace README「License」一節：主程式為 GPL v2 或之後版本；函式庫 `libautotrace` 為 LGPL v2.1 或之後版本；輸入輸出模組兩種授權皆可。功能：輪廓與中心線描圖、減色、去斑點；輸出 SVG、EPS、PDF、DXF 等；需要 GLib 2.58 以上。【直讀】[README](https://github.com/autotrace/autotrace/blob/master/README.md)
50. AutoTrace 最新版 0.31.11 於 2026-11-13 釋出，前一版 0.31.10 為 2024-01-29。Python 綁定 `pyautotrace` 0.0.8（2026-10-01）標 GPL-2.0-or-later。【直讀】[Releases](https://github.com/autotrace/autotrace/releases)、[PyPI](https://pypi.org/project/pyautotrace/)
51. VTracer：LICENSE 檔為 MIT 條文（TSANG, Hao Fung）；PyPI 授權欄為 MIT；npm 套件 `@visioncortex/vtracer` 標 MIT OR Apache-2.0；底層函式庫 visioncortex 為 MIT 與 Apache-2.0 雙授權。【直讀】[LICENSE](https://github.com/visioncortex/vtracer/blob/master/LICENSE)、[PyPI](https://pypi.org/project/vtracer/)、[npm](https://registry.npmjs.org/@visioncortex/vtracer)、[visioncortex](https://github.com/visioncortex/visioncortex)
52. VTracer 版本：PyPI 穩定版 0.6.15（2026-03-23）；1.0.0a1 於 2026-07-24，最新預發行 1.0.0a4 於 2026-08-29。【直讀】[PyPI](https://pypi.org/project/vtracer/)
53. VTracer README 自述：可處理彩色影像（Potrace 只處理黑白）；原本為處理高解析度的歷史藍圖掃描而設計，也能處理低解析度像素圖；採堆疊策略，輸出比 Illustrator 的影像描圖精簡。1.0 新增：Bradley–Roth 自適應門檻（給光線不均的掃描與照片）、`--simplify` 曲線簡化（通常可讓檔案減半）、固定調色盤與自動量化、無縫拼接模式、分水嶺分群。提供 Rust、Python（原生擴充）、Node（WebAssembly，無原生相依）三種函式庫。【直讀】[README](https://github.com/visioncortex/vtracer/blob/master/README.md)
54. ImageTracer.js：Unlicense；npm 最新版 1.2.6（2020-05-18）。【直讀】[LICENSE](https://github.com/jankovicsandras/imagetracerjs/blob/master/LICENSE)、[npm](https://registry.npmjs.org/imagetracerjs)
55. DiffVG、LIVE、StarVector、OmniSVG 的程式庫 LICENSE 都是 Apache-2.0。OmniSVG README 寫明訓練資料 MMSVG 為 CC BY-NC-SA 4.0；OmniSVG1.1 的 4B 與 8B 模型分別需 17 GB 與 26 GB 顯示記憶體。【直讀】[OmniSVG README](https://github.com/OmniSVG/OmniSVG/blob/main/README.md)、[StarVector README](https://github.com/joanrod/star-vector/blob/main/README.md)
56. kurbo（Rust 的二維曲線函式庫）為 Apache-2.0 與 MIT 雙授權。【直讀】[kurbo](https://github.com/linebender/kurbo)

### 2.6 文字筆畫分割與文字擦除

57. Hi-SAM：LICENSE 為 Apache-2.0。README 列出的權重：只做筆畫分割的 SAM-TS 有三組，分別以 Total-Text、TextSeg、HierText 訓練；完整的 Hi-SAM（筆畫、詞、行、段落）以 HierText 訓練，筆畫 F 值 79.78（B）、82.90（L）、83.36（H）。權重放在 OneDrive，不含 SAM 的影像編碼器參數，需另外下載 SAM 官方權重。小字可用滑動視窗模式（預設 512×512、步幅 384）。【直讀】[LICENSE](https://github.com/ymy-k/Hi-SAM/blob/main/LICENSE)、[README](https://github.com/ymy-k/Hi-SAM/blob/main/README.md)
58. HierText 資料集以 CC BY-SA 4.0 釋出。【直讀】[README](https://github.com/google-research-datasets/hiertext/blob/main/README.md)
59. TextSeg 資料集：README 寫「僅限學術，不得用於任何商業專案與研究」，下載需寄信並說明所屬學校，附 Adobe Research 授權檔。該庫沒有 LICENSE 檔。【直讀】[README](https://github.com/SHI-Labs/Rethinking-Text-Segmentation/blob/main/README.md)
60. SCUT-EnsText 資料集：README 寫「只能用於非商業研究目的」。【直讀】[README](https://github.com/HCIILAB/SCUT-EnsText/blob/master/README.md)
61. EraseNet 與 CTRNet：LICENSE 檔為 MIT；README 最後一段寫「商業使用請聯絡金連文教授」。兩者的 README 都以 SCUT-EnsText 與 SCUT-Syn 為訓練與測試資料；EraseNet 釋出的是「在 SCUT-EnsText 上重訓的模型」。【直讀】[EraseNet README](https://github.com/lcy0604/EraseNet/blob/master/README.md)、[CTRNet README](https://github.com/lcy0604/CTRNet/blob/main/README.md)
62. ViTEraser：LICENSE 檔為 MIT；README「Copyright」一節寫「本庫只能用於非商業研究目的；商業使用請聯絡金連文教授」。【直讀】[README](https://github.com/shannanyinxiang/ViTEraser/blob/main/README.md)
63. GaRNet（NAVER，ECCV 2022）：Apache-2.0。README 寫明「所有模型都以合成資料與 SCUT-EnsText 預訓練，而 SCUT-EnsText 只能用於非商業研究目的」。【直讀】[README](https://github.com/naver/garnet/blob/master/README.md)
64. DeepEraser：LICENSE 為 MIT（與同作者的 DocTr 系列不同）；README 說明已釋出以 SCUT-EnsText 訓練的權重。【直讀】[LICENSE](https://github.com/fh2019ustc/DeepEraser/blob/master/LICENSE)、[README](https://github.com/fh2019ustc/DeepEraser/blob/master/README.md)
65. manga-image-translator、comic-text-detector、BallonsTranslator 的 LICENSE 都是 GPL-3.0。DocEnTR 為 CC BY-NC 4.0。【直讀】連結見總表
66. UPOCR、TextCtrl、DiffUTE、PSSTRNet、FETNet 的程式庫在預設分支找不到 LICENSE 檔。【直讀】連結見總表
67. SAM 與 SAM 2 為 Apache-2.0；SAM 3 改用自訂的「SAM License」（2025-11-19 版），授予使用、重製、散布、修改的權利，但附帶散布條件與禁止用途（軍事、受出口管制的用途等）。【直讀】[SAM 3 LICENSE](https://github.com/facebookresearch/sam3/blob/main/LICENSE)
68. Doxa（二值化演算法函式庫）為 CC0 1.0；scikit-image 為 BSD-3-Clause；kraken 為 Apache-2.0；SauvolaNet 為 MIT。【直讀】連結見總表

## 3. 建議

以下是依據第 2 節的事實做的判讀，不是查證結果。

### 3.0 共通原則

1. **五類功能都做成可抽換的介面**（攤平器、增強器、修補器、向量化器、文字遮罩產生器），並且每一類都要有「不用模型」的那一級。理由：這一類的學術模型，權重授權普遍沒有明文，隨時可能需要替換。
2. **凡是會「生成」像素的步驟，都要寫進還原度報告**（修補、擴散式編輯、超解析、攤平的重新取樣）。這與產品「不宣稱還原原始檔」的承諾一致。
3. **原生 PDF 不需要這五類的大部分**：文字、向量圖、點陣圖在檔案裡本來就是分開的物件，直接移除文字物件即可，不需要擦字與修補。這五類主要服務照片、截圖、掃描檔。
4. **OpenCV**：伺服器用 `opencv-python-headless`（避開 Qt 的 LGPLv3）。桌面版要處理內附的 FFmpeg（LGPLv2.1）：本產品不需要視訊讀寫，最乾淨的作法是自行編譯不含 FFmpeg 的 OpenCV。先鎖定 4.14 或 5.1 其中一個大版本，因為 5.x 搬動了部分函式的位置。

### 3.1 文件攤平與去彎曲

- **首選：分兩級。** 第一級是四角透視校正（OpenCV 輪廓法；找不到四角時用 DocAligner）。第二級是 UVDoc，只在偵測到頁面彎曲時啟用。UVDoc 用原始版（MIT，權重就在程式庫內，授權最清楚），或用 PaddleOCR 的 UVDoc 模組（與 OCR 共用推論環境，但模型卡授權欄尚未讀到）。
- **備選：** DocRes 的攤平任務（MIT，權重無明文授權）；書頁類可試 page-dewarp（MIT，無權重）。
- **排除：** DocTr、DocTr++、DocGeoNet、DocScanner（非商用）；DvD（AGPL）。
- **理由：** 靈感模板市場拍的多半是平面海報，透視校正就夠，而且不會扭曲版面比例。模型式攤平會以粗網格重新取樣整張影像，對版面比例有風險，應該是選用步驟，並把「已做曲面攤平」寫進還原度報告。

### 3.2 去反光、去陰影、影像增強

- **首選：OpenCV 傳統作法**（背景估計後相除、CLAHE、白平衡）。零授權風險、結果可預期、不會憑空生成內容。
- **備選：** DocRes 的去陰影與外觀增強（MIT，伺服器端，只用在傳統法處理不好的案例；權重授權需確認）。
- **去反光沒有可用的模型。** 建議作法：偵測過曝的反光區域，在還原度報告標示「此區資訊已遺失」，並在上傳時提示使用者重拍。
- **不建議**把 Real-ESRGAN 這類生成式超解析用在文字區，筆畫可能被改掉。

### 3.3 背景修補

- **首選：分三級。**
  1. 純色或平滑漸層的底：直接取樣周圍顏色填補，或用 OpenCV 的 `inpaint`。文件與簡報的大多數底圖屬於這一級。
  2. 有紋理或照片的底，線上版：LaMa（big-lama）。程式碼 Apache-2.0，品質與高解析度的穩定性是目前公認最好的非擴散式選擇。**條件：權重授權沒有明文，上線前要請法務確認**；或用官方訓練程式碼以自有資料重訓，一勞永逸。
  3. 有紋理或照片的底，桌面版：MI-GAN。程式碼與權重都有 MIT 明文，模型小，可轉成單一 ONNX 檔，是唯一一個「權重授權寫得清清楚楚」的學習式修補模型。
- **備選：** 若法務對 LaMa 權重有疑慮，線上版也改用 MI-GAN。擴散式模型只在使用者明確要求「重繪被遮住的複雜背景」時使用，可用的有 FLUX.2 [klein] 4B 與 Qwen-Image-Edit（都是 Apache-2.0）；Stable Diffusion 系列可商用，但要把使用限制條款寫進服務條款。
- **需付費：** FLUX.1 Fill [dev]（非商用授權，可向 Black Forest Labs 購買商業授權）。
- **排除：** MAT、DeepFill v2（CC BY-NC）。不要使用 PatchMatch 的實作（Adobe 專利狀態未明）。
- **實驗對照組：** Qwen-Image-Layered（Apache-2.0）可直接把影像拆成圖層，值得用來與「偵測、擦除、修補」的傳統流程比較，但它會重新生成像素，且建議解析度只有 640。
- **注意：** MI-GAN 與 LaMa 都用 Places 資料集訓練。MI-GAN 的優勢只在「權重授權有明文」，訓練資料的來源問題兩者相同。

### 3.4 點陣轉向量

- **首選：VTracer。** MIT，能處理彩色，Rust、Python、WebAssembly 三種介面都有，線上版與桌面版都能用同一套。先用穩定版 0.6.15；1.0 的自適應門檻與曲線簡化對手機拍攝的線稿很有用，等正式版再升級。
- **搭配：自建的形狀辨識層**（OpenCV 輪廓加曲線擬合）。把矩形、圓、直線還原成簡報軟體與 InDesign 的原生形狀，比輸出一堆貝茲曲線更符合「可直接編輯」的承諾。
- **備選：** ImageTracer.js（公有領域，純 JavaScript，但 2020 年後沒有更新）；需要中心線描圖時用 `libautotrace`（LGPL v2.1 以上，不在排除名單，但要動態連結並遵守 LGPL 義務）。
- **需付費：** Potrace Professional（黑白描圖的品質標竿；價格要寫信問 Icosasoft）。
- **排除：** GPL 版的 Potrace 與所有移植版，包含自稱 MIT 的 `ts-potrace`；AutoTrace 的命令列主程式。
- **不建議**現階段使用 StarVector、OmniSVG 這類生成式模型：權重授權未確認，OmniSVG 的訓練資料是非商用授權，而且輸出是模型重畫的結果。

### 3.5 文字筆畫分割與文字擦除

- **首選：不使用專用的擦除模型。** 流程：OCR 給出文字框與多邊形（PP-OCR，Apache-2.0）→ 在框內用局部二值化或顏色分群求筆畫遮罩（OpenCV ximgproc、Doxa）→ 把遮罩膨脹幾個像素 → 交給 3.3 的修補器。全部元件都是寬鬆授權。
- **備選：** 字壓在複雜照片上、傳統二值化失敗時，用 Hi-SAM（Apache-2.0）且只用 HierText 訓練的那組權重，上線前請法務確認權重授權。長期更穩的作法是**自己合成訓練資料**（把字型算繪到背景圖上，天然就有像素級標註），用 Hi-SAM 或 SAM 2 的架構訓練自己的筆畫分割模型，同一批資料也能訓練 GaRNet 架構的擦除模型。
- **排除：** EraseNet、CTRNet、ViTEraser、GaRNet、DeepEraser 的預訓練權重（都依賴僅限非商業研究的 SCUT-EnsText，其中三個的 README 明文要求商用須洽作者）；TextSeg 訓練的任何權重；DocEnTR（CC BY-NC）；manga-image-translator 一系（GPL-3.0）；沒有 LICENSE 檔的 UPOCR、TextCtrl、DiffUTE 等。
- **理由：** 這一類是五類中授權最差的。沒有找到任何一個「程式碼、權重、訓練資料三層都可商用」的文字擦除或筆畫分割預訓練模型。

## 4. 查不到的事

1. **LaMa 預訓練權重的明文授權。** 官方只有一句語帶保留的回覆，2026 年的四則詢問沒有得到回答。
2. **Places 資料集的完整使用條款**，以及「用僅供學術研究的資料集訓練出來的權重能否商用」這個法律問題。後者不是查證能回答的，需要法務意見。
3. **`PaddlePaddle/UVDoc` 模型卡的授權欄。** Hugging Face 無法直接讀取，搜尋摘要也沒有帶出。
4. **Doc3D 資料集影像的使用條款**（現在放在 Hugging Face）。UVDoc、DewarpNet、PaperEdge、DocRes 的攤平權重都用到它。
5. **以下權重的明文授權**：DocRes、DewarpNet、PaperEdge、DocShadow-SD7K、DocDiff、DocAligner、Hi-SAM、DeepEraser、ZITS、AOT-GAN、PowerPaint、BrushNet、StarVector、OmniSVG、SauvolaNet、AnyText、Real-ESRGAN、NAFNet、Restormer、SwinIR。這些專案都只有程式庫層級的授權。
6. **文件去反光模型**：DocHR14K 與 L2HRNet、SHDocs、TSHRNet 的程式庫位置與授權都沒有確認到。
7. **任何模型在本產品情境下的實際品質。** 沒有找到針對海報照片、繁體中文文件照片的攤平、去陰影、修補、向量化的獨立評測。MI-GAN 與 LaMa 在文件底圖上的比較也沒有查到。
8. **Potrace Professional 的價格與授權條款**（官方頁只給洽詢信箱）；**Black Forest Labs 商業授權的價格**（bfl.ai 無法讀取）。
9. **OpenCV 5 的 Python 介面**是否因模組重組而有不相容之處。只確認了 C++ 標頭的位置變動。
10. **opencv.org 授權頁的原文**（只有搜尋摘要）。opencv-python 的 `LICENSE-3RD-PARTY.txt` 全文沒有逐項閱讀。
11. **PatchMatch 專利的到期日與目前狀態。**
12. **VTracer 是否支援中心線描圖**（README 沒有提到，但沒有逐一查原始碼）；1.0 正式版的時程。
13. **Qwen-Image-Layered 與 Qwen-Image-Edit 的硬體需求**，以及 Qwen-Image-Layered 官方模型卡的原文。
14. **Total-Text 資料集的授權**；HierText 的影像來自其他來源，影像本身的授權對訓練權重的影響未查。
15. **Stable Diffusion 3.5 與 Stability 社群授權**的條款本次沒有查。
16. **AADD 的程式碼與模型**尚未釋出，授權檔也不存在，之後需要再查一次。

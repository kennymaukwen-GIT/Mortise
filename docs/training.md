# 訓練工廠：你的評價怎麼被記住、這一版算不算一個「模型」、怎麼讓它變強

這份是寫給不寫程式的人看的。怎麼放樣本、怎麼跑評測、怎麼寫標準答案，請看 [accuracy-loop.md](accuracy-loop.md)；
這裡講的是「回饋存在哪」「模型是什麼」「三種變強的方法」以及「現在做得到和還做不到的」。

## 一句話

**Mortise 的「模型」＝規則＋門檻＋模型檔，每一個組合有一個固定的版本名字；每個版本都有一張成績單；
你的每一次修改和評價都會留下來，累積夠多之後可以用三種方法讓它變強，而新版本要在成績單上贏過舊版本才採用。**

---

## 一、你每次的修改與評價存在哪

| 你做了什麼 | 存在哪 | 會不會被覆蓋 | 多久後刪除 |
|---|---|---|---|
| 改了字、字型、字級、顏色、對齊、角色、頁面尺寸、拖了紙張四角（**修正**） | 那份工作的目錄 `jobs/<工作編號>/job.json` 的 `corrections` | **不會**：你的修正信心值固定是 1.0、標成「鎖定」，之後不管重跑幾次、系統升級幾次，都不會被系統的判斷蓋掉 | 30 天 |
| 打分數（1–5）、勾標籤（字認錯了、字型不對…）、寫意見、標「這個東西是對的／錯的」（**評價**） | `jobs/<工作編號>/feedback/` 每次一個檔 | 不會（每次新增一個檔，不改舊的） | 30 天 |
| 上傳時勾了「同意用於改善辨識」 | 上面兩樣**另外**收一份到回饋庫（`MORTISE_FEEDBACK`，預設 `jobs/_feedback/samples/`）：原始檔＋同名的 `.feedback.json` | 不會 | **不會自動刪**（這是你同意保存的資料） |

- **同意開關**：只有上傳時勾了「同意用於改善辨識」的檔案，才會進回饋庫、才會被拿去評測、調參、訓練。沒有勾的，只存在工作目錄裡，30 天後連同原始檔一起刪掉。
- **30 天**：保留天數由 `MORTISE_RETENTION_DAYS` 設定（預設 30）。目前是在**伺服器啟動時**清掉過期的工作（見下面「還不能做到」）。
- **收進回饋庫時記了什麼**：你改了什麼、改成什麼，以及**系統當時給的值和它有多少把握（信心值）**。後者是「校準」的原料：系統說九成把握的東西，你到底改了幾成。
- **自己的資料，自己帶走**：網頁上可以下載「回饋包」（`<原檔名>.mortise-feedback.zip`，API 是 `GET /api/jobs/{id}/feedback/export`），**不需要**勾同意。裡面有：
  - `sample/<原檔>`：你上傳的檔案；
  - `sample/<原檔主檔名>.feedback.json`：你的評分、修正（含系統當時的值與信心值）、標籤與意見（格式 `mortise.feedback/1`，說明在 `mortise/eval/feedback.py` 開頭）；
  - `report.json`：還原度報告；
  - `README.txt`：白話說明怎麼用。
- 想看某份工作已經送了哪些回饋：`GET /api/jobs/{id}/feedback`（列出每一筆評價、修正單數量、有沒有同意）。

把回饋包交給開發者，或自己收進回饋庫：

```bash
python -m mortise.eval feedback-import 季報.mortise-feedback.zip --store 回饋庫
```

同一份工作同時在回饋庫（勾同意後自動收的）和回饋包裡，同一筆修正只算一次。

## 二、「模型」在 Mortise 是什麼

一般說的「模型」是一個訓練出來的神經網路檔。Mortise 不是只有一個網路，它是七個階段的流水線，
裡面大部分是**量測規則**（例如「行距超過一般行距的 1.4 倍就分段」），只有少數地方用到學習式的模型檔
（文字辨識 PP-OCR、彎曲紙張攤平的 UVDoc），而且照架構的鐵律，模型只能在候選中挑或給初值，數值一律由量測決定。

所以在 Mortise，**一個「模型」＝一個引擎版本**，由這幾樣組成，任何一樣變了，版本就變：

| 組成 | 是什麼 | 怎麼變 |
|---|---|---|
| 規則 | 七個階段的程式與各自的版本號 | 工程師改演算法 |
| 門檻 | 設定檔 `mortise/config/default.toml`（加上覆寫檔） | 自動調參、人工調整 |
| 模型檔 | 文字辨識（偵測、辨識、方向分類三個檔）、攤平用的 UVDoc | 微調文字辨識、換新版模型 |

版本名字長這樣（完整的字串是唯一的；後面括號是好記的短名，由完整字串決定）：

```
mortise-0.1.0+s1v3.s2v5.s3v3.s4v3.s5v6.s6v4.s7v6-cfg02f7ad68-ocrbbb248-uv753d-srcd4c618aa   （杉狐-42）
```

`s1v3…s7v6` 是七個階段的版本、`cfg` 是設定的指紋、`ocr` 是文字辨識模型檔的指紋、`uv` 是攤平模型的指紋（沒有下載是 `uvnone`）、
`src` 是程式碼的指紋（有人改了程式卻忘了改版本號，名字照樣會變）。查目前的版本：

```bash
python -m mortise.engine          # 或網頁 API：GET /api/engine（另附最新成績單的摘要）
```

所以：**可以，你可以把「這一版」叫做一個模型，它就是那串名字。** 要「訓練成更強的模型」，就是用下面三種方法之一產生一個新名字的版本，
再拿成績單證明它比較強。它不會自己偷偷變：沒有人跑這些步驟，它就一直是同一個版本。

### 成績單：這個模型考幾分

成績單用固定的考卷：從 `tests/fixtures` 的虛構文件出題，用固定的亂數種子把每一頁做成截圖、掃描、平的手機照片、彎曲的照片，
每一題都跑完整的七個階段，答案就是原本的 PDF（所以不需要人工標答案）。記錄的分數：

- 文字錯字率（整頁，含漏字多字）、字級誤差（pt）
- ⑦驗證的總分，以及每個輸出格式（可編輯 PDF、PowerPoint、Word…）各自的分數
- 照片四角成功率（四個角都在對角線 1% 以內）、**四角錯了卻沒有請你確認的比例**（最危險的錯，應該是 0）
- 表格列數欄數是否正確、每頁處理時間

```bash
python -m mortise.eval scorecard --quick --out 成績單      # 快速版：10 題，4 核機器約 2 分鐘（2026-11-23 實測 106 秒）
python -m mortise.eval scorecard --out 成績單              # 完整版：約 30 題（時間見 docs/scorecards/README.md）
```

輸出 `成績單/<版本名>.json`（完整數字）和 `.md`（給人看）。目前引擎的基準成績單放在 [scorecards/](scorecards/)。

**新模型要比舊模型好才採用**：

```bash
python -m mortise.eval scorecard compare 舊版本.json 新版本.json
```

它只比兩份都做過的題目，總體和每一種輸入（截圖、掃描、照片、彎曲照片）各比一次，退步的數字用粗體標出。
有幾項設了「不可退步」的門檻（錯字率、字級、⑦各格式分數、照片四角、表格、整頁變成圖片…），超過門檻就回報「不建議採用」，
程式的結束碼是 1，之後可以直接接到自動檢查（CI）上。門檻可以改：`--gate page_cer=0.005`、`--gate seconds_per_page=50%`、`--gate quad_success=off`。

## 三、三種「變強」

| | ① 校準信心值 | ② 自動調參 | ③ 微調文字辨識 |
|---|---|---|---|
| 改的是什麼 | 系統「對自己有多少把握」的數字（不改輸出） | 設定檔裡的門檻 | 文字辨識的模型檔 |
| 你會感覺到 | 該問你的地方才問你，不該問的不再煩你 | 分段、對齊、字級等判斷更貼近你的文件 | 字認得更準（特別是你常用的字型、拍照的情況） |
| 需要的資料 | 有「系統當時信心值」的修正或確認：程式門檻至少 50 筆，而且至少兩個信心區間各 5 筆；每個欄位要穩定建議幾百筆（需查證） | 樣本檔案（PDF 不需要標答案；照片要有標準答案或回饋）：建議先每類 20 份，之後每類 100 份（見 accuracy-loop.md） | 文字行影像＋正確文字：PaddleOCR 官方微調說明建議字典不變時**至少 5000 行**，改字典要更多；新資料要和原本的通用資料混著練（官方建議原資料：新資料約 10:1 到 5:1）（數字取自官方文件，原文需查證） |
| 指令 | `annotations` → `calibrate` | `tune` → `scorecard --config` → `compare` | `ocr-dataset` → GPU 機器上訓練 → `ocr-model` → `scorecard --config` → `compare` |
| 要多久 | 幾秒 | 每試一個值要把樣本從④重跑一次：1 份投影片一輪約 11 秒（測試實測）；20 份樣本、2 輪估計數十分鐘到數小時（需查證） | 準備資料幾分鐘；訓練要 GPU，數千行約數十分鐘到幾小時（需查證）；這台開發機沒有 GPU，不能訓練 |
| 現在能不能做 | 算得出校準表，**還沒有自動套回流水線** | 可以 | 資料與放回去的步驟可以；訓練要另外的 GPU 機器，步驟沒有實測 |

### ① 校準信心值

```bash
python -m mortise.eval feedback-import 回饋庫/samples --store 回饋庫/store   # 或匯入回饋包（.zip）
python -m mortise.eval annotations --store 回饋庫/store --out 標註.jsonl
python -m mortise.eval calibrate 標註.jsonl                 # 全部一起
python -m mortise.eval calibrate 標註.jsonl --stage 3 --field text   # 只看③的文字
```

資料不夠時會直接說「樣本不足：只有 N 筆，至少需要 50 筆」，不會硬算。沒有記下系統當時信心值的修正（例如沒勾同意的工作、在回饋包裡標成
`system_value_missing`）、以及「同一個欄位第二次改」（那時的值是你上一次改的，不是系統的判斷）都不算進校準。

### ② 自動調參

```bash
python -m mortise.eval tune 樣本目錄 --out 調參結果 --variants screenshot,photo
python -m mortise.eval scorecard --config 調參結果/override.toml --out 成績單
python -m mortise.eval scorecard compare 成績單/舊版本.json 成績單/新版本.json
```

調參只會在 `mortise/config/tunable.toml` 列出的範圍裡找，並保留四分之一的樣本當驗證（避免只對這批樣本有效）。
`compare` 沒有超過門檻的退步，再把 `override.toml` 的數值抄進 `default.toml`（這時版本名字的 `cfg` 那段會變，就是一個新模型）。

### ③ 微調文字辨識（PP-OCR）

**第 1 步：產生訓練資料（這台機器就能做，已實測）**

```bash
# 從原生 PDF 自我監督（PDF 裡的字就是答案）＋你改過的字
python -m mortise.eval ocr-dataset --out 訓練資料 --from-fixtures --pdf 你有權使用的PDF目錄 --from-feedback 回饋庫/samples
```

- 原生 PDF：把每一頁畫成影像，加上截圖、掃描、翻拍的干擾，用 PDF 文字層的字與位置把每一行裁下來。
  `tests/fixtures` 全部 11 份虛構文件產生 858 行，約 17 秒（2026-11-23 實測）。字型種類少，單靠它練不出更強的模型，主要是用來補量。
- 你的修正：每一次你在網頁上改③讀到的一行字，就是一筆最有價值的訓練資料（真實照片、真實字型、系統真的認錯的地方）。
  只收**勾了同意**的；回饋包裡沒勾同意的，要加 `--include-unconsented` 才收（確定是你自己的資料時）。
  工作目錄 30 天後刪除，但回饋庫和回饋包裡有原始檔，會自動重跑一次流水線把那一行找回來（同一個引擎版本才找得到）。
- 輸出：`images/`、`rec_gt_train.txt`、`rec_gt_val.txt`（每行「圖片路徑＋Tab＋文字」，PaddleOCR 的格式）、`dict.txt`（目前模型的字典，18708 個字）、
  `out_of_dict.txt`（**目前的模型字典裡沒有、所以永遠認不出來的字**）、`manifest.json`（每一筆從哪來、有沒有經你同意、授權）。

**第 2 步：在有 GPU 的機器上用 PaddleOCR（Apache-2.0）微調辨識模型（以下都沒有實測，需查證）**

1. 安裝 PaddlePaddle（GPU 版）與 PaddleOCR 原始碼（`git clone https://github.com/PaddlePaddle/PaddleOCR`，`pip install -r requirements.txt`）。版本要互相對得上（需查證）。
2. 下載**和 Mortise 現在用的同一系列**的辨識模型「訓練用權重」（`.pdparams`，不是推論用的檔）。Mortise 用的是 rapidocr 內附的
   PP-OCRv6 small 辨識模型；它的訓練用權重與設定檔名稱需查證。找不到時，退而用官方有公開訓練權重的 PP-OCRv5 mobile rec
   （`configs/rec/PP-OCRv5/PP-OCRv5_mobile_rec.yml`，需查證），但字典與模型都會換，要當成一個全新的模型看待、成績單一定要重比。
3. 改設定檔（需查證欄位名稱）：`Global.pretrained_model` 指到第 2 步的權重；`Global.character_dict_path` 用第 1 步的 `dict.txt`
   （**字典要和模型一致**；要加 `out_of_dict.txt` 裡的字，就得改字典，模型最後一層會重新訓練，需要更多資料）；
   `Train.dataset.data_dir` 設成「訓練資料」目錄、`label_file_list` 用 `rec_gt_train.txt`；`Eval` 用 `rec_gt_val.txt`；
   官方說最重要的是預訓練權重、學習率（微調時調小）、批次大小。
4. 訓練：`python tools/train.py -c 你的設定.yml`（多張 GPU 用 `python -m paddle.distributed.launch --gpus 0,1 tools/train.py -c …`；需查證）。
5. 匯出推論模型：`python tools/export_model.py -c 你的設定.yml -o Global.pretrained_model=output/rec/best_accuracy Global.save_inference_dir=inference/rec`（需查證）。
6. 轉成 ONNX：用 paddle2onnx（Apache-2.0；新版 PaddleX 有 `paddlex --paddle2onnx --paddle_model_dir inference/rec --onnx_model_dir onnx/rec`；需查證）。

**第 3 步：放回 Mortise（這台機器就能做，已實測）**

paddle2onnx 轉出來的 ONNX 沒有字典，Mortise 讀字典的地方是模型檔裡的 metadata。這個指令把字典寫進去，並檢查「模型輸出的類別數 = 字典字數 + 2」：

```bash
python -m mortise.eval ocr-model onnx/rec/inference.onnx --dict 訓練資料/dict.txt --out /固定的位置/mortise_rec_v2.onnx
```

它會印出換上的設定。把這段寫進一個覆寫檔（例如 `new_ocr.toml`）：

```toml
[recognize.ocr_params]
rec_model_path = "/固定的位置/mortise_rec_v2.onnx"
```

**第 4 步：考試，贏了才採用**

```bash
python -m mortise.eval scorecard --config new_ocr.toml --out 成績單
python -m mortise.eval scorecard compare docs/scorecards/<目前的版本>.json 成績單/<新版本>.json
```

新版本的名字 `ocr` 那段會變。`compare` 沒有超過門檻的退步，才把 `new_ocr.toml` 的設定併進正式的設定（伺服器要重新啟動才會換上新模型），
並把新成績單放進 `docs/scorecards/` 當下一次的基準。成績單的考卷是合成的；換 OCR 這種大改動，最好再用回饋庫裡的真實照片跑一次
`python -m mortise.eval run`（見 accuracy-loop.md）。

## 四、現在能做到與還不能做到的

**能做到（有測試）**

- 修正鎖定、不被覆蓋；評價逐筆保存；同意的才進回饋庫；回饋庫不跟著過期。
- 網頁 API：查一份工作的回饋（`GET /api/jobs/{id}/feedback`）、下載回饋包（`GET /api/jobs/{id}/feedback/export`）、查引擎版本與最新成績單（`GET /api/engine`）。
- 引擎版本：規則、門檻、模型檔任何一樣變了，名字就變。
- 成績單與比較（含「不可退步」門檻、結束碼）；目前引擎的基準成績單在 `docs/scorecards/`。
- 回饋 → 標註 → 校準表；自動調參；OCR 訓練資料（自我監督＋你的修正）；把微調好的模型放回並檢查。

**還不能做到**

- **網頁上還沒有**評分、回報錯誤、拖曳四角、下載回饋包的按鈕（API 已經好了，網頁由另一位開發者接）。
- **校準表還沒有套回流水線**：現在只能算出「系統說九成把握時實際對幾成」，系統給你的信心值還是原本的。
- **沒勾同意的工作，修正當時系統的值沒有記下**（回饋包裡標 `system_value_missing`），這些修正不能拿來校準；要等工作管理那邊在工作目錄也存一份快照。
- **30 天刪除只在伺服器啟動時做**：伺服器一直不重啟，過期的工作會留到下次啟動。
- **沒有真的訓練過 OCR**：這台機器沒有 GPU；PaddleOCR 的訓練、匯出、轉 ONNX 步驟都還沒實測（上面標了需查證），PP-OCRv6 的訓練用權重是否公開也需查證。
- **成績單的考卷是合成的**：真實照片的光線、鏡頭變形不在裡面；每種輸入只有一到幾題，一題錯分數就差很多。
- **換 OCR 模型要重新啟動伺服器**：同一個程式裡第一次載入的文字辨識模型會一直沿用（已請負責③的開發者改成依設定重新載入）。
- 字型檔、LibreOffice 的版本、大型 AI 服務端的更新不算在引擎版本裡（它們會影響結果，但版本名字不會變）。

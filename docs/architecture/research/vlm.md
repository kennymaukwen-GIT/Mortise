# 查證紀錄：大型多模態模型用於文件結構理解

- 查證日期：2026-11-21（工作指派日 2026-11-20）
- 適用產品：Mortise（榫）文件反編譯 SaaS，未來可能有桌面版
- 授權底線：所有元件必須可商用；排除 GPL、AGPL 元件（另有可購買商業授權者列為「需付費」）；排除非商用授權的模型權重
- 查證範圍：(1) 編號標記法（Set-of-Mark）用在版面分組、閱讀順序、表格理解的公開效果；(2) Anthropic、OpenAI、Google 三家商用多模態模型的影像規格與計價；(3) 可自行部署且可商用的開源視覺語言模型；(4) 用大型模型做 OCR 後校正的做法與風險；(5) 強制結構化輸出

## 0. 查證方式與限制

本次查證的工具狀況直接影響每一條結論的可信度，先講清楚。

| 來源 | 狀況 |
|---|---|
| 網路搜尋 | 可用 |
| platform.claude.com（Anthropic 官方文件） | **可直接讀取**。影像、座標、PDF、結構化輸出、計價五頁都讀到原文 |
| github.com、raw.githubusercontent.com | 可直接讀取（README、LICENSE 標示、官方文件的 Markdown 原檔） |
| developers.openai.com、platform.openai.com（OpenAI 官方文件） | **無法直接讀取**（被工作環境的網路規則擋下）。只能靠搜尋摘要 |
| ai.google.dev（Gemini API 官方文件） | **無法直接讀取**。只能靠搜尋摘要 |
| huggingface.co（模型卡與權重的 LICENSE 檔） | **無法直接讀取**。權重授權只能靠搜尋摘要或 GitHub README 的轉述 |
| arxiv.org（論文） | **無法直接讀取**。論文結論只能靠搜尋摘要 |

下文每條事實後面標註查證方式：

- **【直讀】** 直接讀到官方來源的原文
- **【搜尋摘要】** 搜尋工具轉述了該頁面的內容，但我沒有親自打開該頁（未直接開啟）
- **【第三方直讀】** 直接讀到的非官方來源（例如 LiteLLM 維護的價目表）
- **【推算】** 我用已查證的公式與單價自己算出來的數字

這造成一個不對稱：**Anthropic 的規格全部是直讀，OpenAI 與 Google 的規格幾乎都不是直讀**（唯一例外是 Google 放在 GitHub 上的官方範例庫，用來確認外框格式與現行型號名稱）。下面三家的比較不是在同一個可信度上，採用前必須在網路放行後補讀 OpenAI 與 Google 的官方頁面。

OpenAI 與 Google 的單價主要來自 LiteLLM 的公開價目表（第三方，2026-11-21 下載），再用搜尋摘要交叉比對。這份價目表中 Claude 四個模型（Fable 5.2、Opus 5.5、Sonnet 5.5、Haiku 5.5）的單價與我直讀的 Anthropic 官方計價頁完全相同，可當作它可信度的旁證，但不能取代官方頁面。對不上的地方文中逐一標出。

**時效提醒**：OpenAI 在 2026-11-17 才發布 GPT-6.2；Google 的 Gemini 4 系列有到 2026-11-30 為止的優惠價，12-01 起部分型號漲一倍。這一節的單價在幾週內就會過時。

論文數字全部是作者自報。沒有找到任何一份針對繁體中文文件、比較這些模型在版面分組或閱讀順序上表現的獨立評測。

## 1. 總表

「可否商用」指用在收費的線上服務；「可否隨桌面版散布」指把程式與權重打包進安裝檔交給使用者。商用 API 模型沒有權重可散布，桌面版只能連線呼叫。

### 1.1 商用多模態模型（只能透過 API 使用）

單價為每百萬 token 的美元價格（輸入／輸出）。

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **Anthropic Claude Sonnet 5.5** | 版面分組、閱讀順序、表格結構判斷的主力候選 | 不適用（閉源服務） | 不適用 | 可（付費 API） | 不可散布；桌面版只能連線 | $2／$10；高解析度層：長邊上限 2,576 px、每張圖上限 4,784 個視覺 token；每張圖 token 數為 ⌈寬/28⌉×⌈高/28⌉；結構化輸出為正式功能；批次處理半價 | 官方明說座標與定位輸出是近似值；低品質、旋轉、小於 200 px 的影像可能幻覺；直接送 PDF 時每頁影像被壓到標準層（最多 1,568 token） | 是（全部直讀） | [影像](https://platform.claude.com/docs/en/build-with-claude/vision)、[座標](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates)、[計價](https://platform.claude.com/docs/en/about-claude/pricing)、[結構化輸出](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) |
| **Anthropic Claude Haiku 5.5** | 大量、簡單頁面的低成本路線 | 不適用 | 不適用 | 可 | 不可散布 | $0.10／$0.50（提示超過 10 萬 token 時為 $0.50／$2.50）；其餘同上 | 依文件字面（「Claude 4.7 及之後的模型」）屬高解析度層，未實測；判斷力低於 Sonnet 與 Opus | 是（層級歸屬為字面推定） | 同上 |
| **Anthropic Claude Opus 5.5** | 困難頁面的升級路線 | 不適用 | 不適用 | 可 | 不可散布 | $4／$20；官方計價頁以它為高解析度層的成本範例 | 不能關閉思考；單價是 Sonnet 5.5 的兩倍 | 是 | 同上 |
| **Anthropic Claude Fable 5.2** | 最高能力，僅供比較 | 不適用 | 不適用 | 可 | 不可散布 | $8／$40 | **要求 30 天資料保留**，除非 Anthropic 明確授權，否則不能用於零資料保留；使用客戶自管金鑰的組織不能用結構化輸出 | 是 | [計價](https://platform.claude.com/docs/en/about-claude/pricing)、[資料保留](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention) |
| **OpenAI GPT-6.2 Sol、GPT-6.2 Astra、GPT-6.1 Terra、GPT-6 Luna（另有 GPT-5.5、GPT-5.4）** | 同類候選 | 不適用 | 不適用 | 可 | 不可散布 | gpt-6.2-sol $2／$10、gpt-6.2-astra $8／$40（2026-11-17 發布，多個二手來源與第三方價目表一致）；gpt-6.1-terra $1／$5、gpt-6-luna $0.10／$0.50（只有第三方價目表）；影像以 32×32 px 小塊計 token；`detail` 分 low、high、original、auto；original 模式保留原始尺寸；有 Structured Outputs（嚴格 JSON Schema） | **官方文件未能直接開啟**；各模型的影像 token 倍率表沒讀到；提示超過 27.2 萬 token 另有加價 | **否**（單價為二手來源一致，規格為搜尋摘要） | [影像指南](https://developers.openai.com/api/docs/guides/images-vision)（未直接開啟）、[計價頁](https://developers.openai.com/api/docs/pricing)（未直接開啟）、[LiteLLM 價目表](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json) |
| **Google Gemini 4 系列（Argon、Carbon、Hydrogen、Neon）與 Gemini 3 系列（3.1 Pro Preview、3.8 Flash、3.5 Flash-Lite 等）** | 同類候選 | 不適用 | 不適用 | 可 | 不可散布 | Gemini 4 Argon 與 Carbon $2／$10（優惠價到 2026-11-30；Carbon 自 12-01 起 $4／$20）；Gemini 3.8 Flash $0.75／$3.75；每張圖的 token 數由 `media_resolution` 決定（Gemini 3：low 280、medium 560、high 1,120 為預設、ultra_high 2,240）；外框為 [y0, x0, y1, x1]、正規化到 0–1000；結構化輸出支援 JSON Schema | **官方文件網站未能直接開啟**；Gemini 4 的 media_resolution 數字沒查到；單價即將調整且部分來源互相矛盾（見 2.4）；綱要複雜度上限沒有官方數字 | **否**（只有外框格式與型號名稱由官方 GitHub 範例庫直讀） | [媒體解析度](https://ai.google.dev/gemini-api/docs/media-resolution)（未直接開啟）、[計價](https://ai.google.dev/gemini-api/docs/pricing)（未直接開啟）、[官方範例庫](https://github.com/google-gemini/cookbook/blob/main/quickstarts/Spatial_understanding.ipynb)、[LiteLLM 價目表](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json) |

### 1.2 可自行部署的開源視覺語言模型（供離線或桌面版）

文件解析專用的小型模型（PaddleOCR-VL、GLM-OCR、MinerU、dots.mocr、DeepSeek-OCR、HunyuanOCR、olmOCR 等）已在 [ocr.md](ocr.md) 第 1.2 節查過，這裡不重複，只列通用型模型。

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **Qwen3-VL（2B、4B、8B、32B、30B-A3B、235B-A22B）** | 離線的結構判斷模型 | Apache-2.0 | Apache-2.0（只有第三方來源；模型卡未直接開啟） | 可（待確認權重 LICENSE 檔） | 可（同上） | Instruct 與 Thinking 兩種版本；OCR 支援 32 種語言；文件解析輸出含版面位置的「Qwen HTML」格式；定位用 0–1000 的相對座標（前一代 Qwen2.5-VL 是絕對座標）；視覺編碼 32 倍壓縮，範例預算每張圖 256–1,280 個視覺 token；原生 25.6 萬 token 脈絡；可用 vLLM、SGLang、Transformers 部署 | README 沒有逐一寫明各尺寸權重的授權；README 沒提 llama.cpp 與 GGUF；大尺寸需要 H100 等級的 GPU | 部分（程式碼是，權重否） | [GitHub](https://github.com/QwenLM/Qwen3-VL) |
| **Qwen3.5（0.8B、2B、4B、9B、27B、35B-A3B、122B-A10B、397B-A17B）、Qwen3.6、Qwen3.8** | 同上，較新一代 | Apache-2.0 | Qwen3.5-9B、2B、4B 的模型卡標示 apache-2.0（搜尋摘要）；Qwen3.6、Qwen3.8 未查到 | Qwen3.5 小尺寸：可（待確認）；Qwen3.8：未確認 | 同左 | 官方稱 Qwen3.5 是「統一的視覺語言基礎模型」；llama.cpp 支援 Qwen3.5（文字加視覺）；可用 MLX（mlx-vlm）在 Mac 執行；vLLM 可開工具呼叫；Qwen3.8-27B 模型卡自述為原生視覺語言模型 | 官方 README 要使用者自行查看權重所附的授權檔；Qwen3.8 最大版的公開權重據報導只有文字能力；文件理解的評測數字在 README 只以圖片呈現，沒讀到 | 部分 | [GitHub](https://github.com/QwenLM/Qwen3.8)、[Qwen3.5-9B 模型卡](https://huggingface.co/Qwen/Qwen3.5-9B)（未直接開啟） |
| **Qwen2.5-VL-7B** | 舊一代備援 | Apache-2.0 | Apache-2.0（搜尋摘要） | 可 | 可 | — | 已被 Qwen3-VL 取代 | 否 | [討論串](https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct/discussions/31)（未直接開啟） |
| **Qwen2.5-VL-3B** | — | Apache-2.0 | **Qwen Research License**（搜尋摘要） | **不可**（研究用途授權） | **不可** | — | 排除 | 否 | [LICENSE](https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct/blob/main/LICENSE)（未直接開啟） |
| **Qwen2.5-VL-72B** | — | Apache-2.0 | 自訂的「Qwen License」（搜尋摘要；二手報導稱月活躍低於 1 億可免費商用） | 條款未讀到原文，不建議 | 不建議 | — | 不建議 | 否 | [The Batch 報導](https://www.deeplearning.ai/the-batch/alibaba-debuts-qwen2-5-vl-a-powerful-family-of-open-vision-language-models/) |
| **InternVL3.5（1B、2B、4B、8B、14B、38B、20B-A4B、30B-A3B、241B-A28B）** | 離線的結構判斷模型 | MIT | InternVL3_5-8B 與 38B 的模型卡標示 apache-2.0（搜尋摘要） | 可（待確認） | 可（待確認） | 動態高解析度：把影像切成 448 px 的小塊（預設最多 12 塊）再加一張縮圖；2025-08-26 釋出 | README 註明「部分程式與模型來自其他來源，各依其授權」；語言模型底座的授權要逐一確認；README 的待辦清單上「支援 vLLM 與 Ollama」仍未勾選；README 截至查證日沒有比 3.5 更新的版本 | 部分 | [GitHub](https://github.com/OpenGVLab/InternVL)、[模型卡](https://huggingface.co/OpenGVLab/InternVL3_5-8B)（未直接開啟） |
| **MiniCPM-V 4.7（35B-A3B、1B）、MiniCPM-V 4.6、MiniCPM-o 4.5（9B）** | 裝置端或桌面版的輕量模型 | Apache-2.0 | Apache-2.0（README 明文：「Code and model weights are released under Apache-2.0」） | 可 | 可 | 4.7 於 2026-10-12 釋出；提供 GGUF（可用 CPU 推論）、Ollama、llama.cpp 部署指南；支援 iOS、Android、HarmonyOS；自述在 iPhone 17 Pro Max 上每秒 13 個以上 token、記憶體 3 GB；另有 MiniCPM-OCR 1.0（2026-10-28） | 影像解析度上限與 OCR 語言清單在 README 沒寫；官方另請使用者自願填寫登記問卷（非強制） | 是（LICENSE 檔、README、Model License 說明三處都直讀） | [GitHub](https://github.com/OpenBMB/MiniCPM-V)、[LICENSE](https://github.com/OpenBMB/MiniCPM-V/blob/main/LICENSE)、[Model License 說明](https://github.com/OpenBMB/MiniCPM-V/blob/main/docs/project_en.md) |
| **Gemma 4（E2B、E4B、26B MoE、31B）** | 離線候選 | — | Apache-2.0（Google 官方部落格，搜尋摘要） | 可 | 可 | 四個尺寸都能處理影像與影片、支援可變解析度；小尺寸另支援音訊；二手來源稱每張圖 70–1,120 token | 文件理解能力沒有查到數字；Gemma 3 以前是 Google 自訂的 Gemma 授權，不是 Apache-2.0 | 否 | [Google Open Source Blog](https://opensource.googleblog.com/2026/03/gemma-4-expanding-the-gemmaverse-with-apache-20.html)（未直接開啟） |
| **GLM-4.6V（106B）、GLM-4.6V-Flash（9B）、GLM-4.1V-9B-Thinking、GLM-5.3-Flash** | 離線候選 | Apache-2.0 | GLM-4.6V 模型卡標示 MIT（搜尋摘要；另有鏡像站標 Apache-2.0，兩者都是寬鬆授權）；GLM-5.3-Flash 只有第三方清單稱 MIT | 可（待確認） | 可（待確認） | 定位框為左上與右下兩角、正規化後乘以 1000；GLM-4.6V 首次內建工具呼叫；可把文件頁當影像讀，訓練脈絡 12.8 萬 token；可用 vLLM、SGLang 部署 | 官方於 2026-09-02 宣布**此程式庫不再維護**；README 沒提結構化輸出 | 部分 | [GitHub](https://github.com/zai-org/GLM-V) |

### 1.3 編號標記法與相關工具

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **Set-of-Mark（做法本身）** | 在影像上疊加編號，讓模型用編號指稱區域 | 不適用（提示技巧） | 不適用 | 可 | 可 | 原作者稱 GPT-4V 加上編號標記後「在多數視覺任務上勝過專用模型」 | 沒有查到用在文件版面、閱讀順序、表格結構的實測數字；後續研究指出標記會遮住或混入內容、開源模型看不懂標記 | 部分（原始論文未直接開啟） | [GitHub](https://github.com/microsoft/SoM)、[論文](https://arxiv.org/abs/2310.11441)（未直接開啟） |
| **microsoft/SoM 程式庫** | 原作者的示範程式 | MIT | 依賴的分割模型（Mask DINO、OpenSeeD、GroundingDINO、SEEM、Semantic-SAM、SAM）各有授權，未逐一查 | 可（程式碼） | 本產品用不到它的分割模型 | 自動分割後畫上編號 | 本產品的區域來自自己的版面與 OCR 偵測，不需要這個程式庫 | 是（只查程式碼授權） | [GitHub](https://github.com/microsoft/SoM) |
| **OmniParser（微軟）** | 螢幕截圖的元件偵測加編號，供模型操作介面 | **標示不一致**：README 徽章寫 MIT，但程式庫的 LICENSE 檔是 CC BY 4.0 | README 原文：icon_detect_v3 以 MIT 授權的 YOLOv9 實作為基礎；較早以 Ultralytics 為基礎的圖示偵測器維持 **AGPL**；描述模型為 MIT | AGPL 版**排除**；其餘需先釐清程式碼授權 | 同左 | README 自述搭配 OmniParser v2 在 ScreenSpot Pro 得 39.5% | 針對使用者介面，不是文件；只當作「偵測器出框、編號、模型選編號」這個模式的旁證；本產品不需要用它 | 是（授權直讀；成績為自述） | [README](https://github.com/microsoft/OmniParser/blob/master/README.md)、[LICENSE](https://github.com/microsoft/OmniParser/blob/master/LICENSE) |

### 1.4 強制結構化輸出

| 名稱 | 用途 | 程式碼授權 | 權重授權 | 可否商用 | 可否隨桌面版散布 | 能力 | 限制 | 已查證 | 來源 |
|---|---|---|---|---|---|---|---|---|---|
| **Anthropic 結構化輸出** | 讓回應必定符合指定的 JSON Schema | 不適用 | 不適用 | 可 | 不適用 | 正式功能，所有模型與所有平台可用；用受限解碼；可與批次、串流並用 | 不支援遞迴綱要、數值上下限、字串長度限制；選填參數合計上限 24、聯集型別參數上限 16；與引用功能不相容；拒絕回應或撞到輸出上限時可能不符綱要；列舉值的大小寫不保證 | 是 | [官方文件](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) |
| **OpenAI Structured Outputs** | 同上 | 不適用 | 不適用 | 可 | 不適用 | 官方稱輸出必定符合所給的 JSON Schema；模型仍可拒絕 | 官方文件未直接開啟；綱要上限的現行數字沒查到 | 否 | [官方文件](https://developers.openai.com/api/docs/guides/structured-outputs)（未直接開啟） |
| **Gemini 結構化輸出** | 同上 | 不適用 | 不適用 | 可 | 不適用 | 2025-11 起所有現役模型支援 JSON Schema；輸出鍵的順序依綱要 | 官方文件未直接開啟；綱要太複雜會被拒絕，但沒有公開門檻 | 否 | [Google 部落格](https://blog.google/technology/developers/gemini-api-structured-outputs/)（未直接開啟） |
| **vLLM 結構化輸出** | 自行部署時的受限解碼 | Apache-2.0 | 不適用 | 可 | 可（伺服器端） | 支援 choice、regex、json、grammar、structural_tag；可用 `response_format` 的 `json_schema`；後端為 xgrammar 與 guidance，預設自動選擇 | 舊的 `guided_*` 參數在 v0.12.0 移除；lm-format-enforcer 後端將在 v0.35.0 移除；文件沒說明是否適用於視覺模型 | 是 | [官方文件](https://github.com/vllm-project/vllm/blob/main/docs/features/structured_outputs.md)、[LICENSE](https://github.com/vllm-project/vllm/blob/main/LICENSE) |
| **llama.cpp 文法與 JSON Schema** | 桌面版的受限解碼 | MIT | 不適用 | 可 | 可 | GBNF 文法；可把 JSON Schema 的子集轉成文法；`/chat/completions` 用 `response_format` | 只支援 JSON Schema 子集（巢狀 `$ref` 有問題、不支援 `patternProperties` 等）；不支援的功能會被**靜默略過**；綱要不會自動放進提示 | 是 | [官方文件](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)、[LICENSE](https://github.com/ggml-org/llama.cpp/blob/master/LICENSE) |
| **XGrammar、Outlines、llguidance、Ollama** | 受限解碼函式庫與本機執行環境 | XGrammar Apache-2.0；Outlines Apache-2.0；llguidance MIT；Ollama MIT | 不適用 | 可 | 可 | 前三者是 vLLM 等推論框架使用的受限解碼後端 | 本次只查了授權（LICENSE 檔開頭），沒有查功能細節 | 是（僅授權） | [XGrammar](https://github.com/mlc-ai/xgrammar/blob/main/LICENSE)、[Outlines](https://github.com/dottxt-ai/outlines/blob/main/LICENSE)、[llguidance](https://github.com/guidance-ai/llguidance/blob/main/LICENSE)、[Ollama](https://github.com/ollama/ollama/blob/main/LICENSE) |

## 2. 逐條事實與來源

### 2.1 編號標記法（Set-of-Mark）

1. **做法**：在影像上疊加可指稱的標記（數字、字母、遮罩、框），讓模型用編號指稱特定區域。原始程式庫 README 的說法是「simply overlaying a number of spatial and speakable marks on the images, to unleash the visual grounding abilities in the strongest LMM」，對象是 GPT-4V。【直讀】[GitHub](https://github.com/microsoft/SoM)
2. **原作者的主張**：GPT-4V 加上編號標記後「outperforms specialists on most vision tasks and is comparable to MaskDINO on COCO panoptic segmentation」。README 沒有列出數字，成績只放在一張圖片裡。展示案例是推理、驗證碼、食材辨識、遊戲規劃，都是個案展示。【直讀】同上
3. **README 完全沒有提到文件、表格、版面或閱讀順序。**【直讀】同上
4. 程式庫授權為 MIT。用來產生標記的分割模型（Mask DINO、OpenSeeD、GroundingDINO、SEEM、Semantic-SAM、Segment Anything）README 沒寫授權。README 最近一則消息是 2025-04 的 SoM-LLaVA。【直讀】同上
5. **沒有查到任何一篇把編號標記法用在文件版面分組、閱讀順序或表格結構、並報告實測數字的論文。** 兩次搜尋都沒有結果。這一項是「查不到」，不是「證實有效」。
6. **後續研究指出的限制**（全部為【搜尋摘要】，論文未直接開啟）：
   - 一篇多語言視覺資料集的研究在初步測試中發現編號標記對 GPT-4V 的答題品質沒有明顯幫助，後來沒有採用。[arXiv 2406.15359](https://arxiv.org/pdf/2406.15359)
   - Describe Anything 的作者報告，在複雜場景中編號標記會讓結果變差，原因是標記會混入內容或遮住物體的一部分。[arXiv 2504.16072](https://arxiv.org/pdf/2504.16072)
   - 一篇研究開源模型的論文報告，編號標記通常會**降低** LLaVA 系列模型的表現，並推測這個做法需要模型有 OCR 能力。[arXiv 2403.02325](https://arxiv.org/pdf/2403.02325)
   - 另一篇研究指出多數多模態模型看不懂這些視覺標記，因此提出專門的微調資料集。[arXiv 2404.16375](https://www.arxiv.org/pdf/2404.16375)
7. **相近的文件領域證據**（全部為【搜尋摘要】）：
   - OmniDocBench（CVPR 2025）涵蓋 1,651 頁、有閱讀順序標註，閱讀順序以編輯距離衡量，表格以 TEDS 衡量。作者觀察到所有模型在多欄與複雜版面上準確度明顯下降；InternVL2 在單欄得分高，雙欄與三欄得分差。[論文](https://arxiv.org/html/2412.07626v1)、[GitHub](https://github.com/opendatalab/OmniDocBench)
   - 一篇 2025 年的論文報告：微調過的 3B 視覺語言模型在閱讀順序上達到 Kendall's τ 0.95，相對於零樣本的 GPT-4o 與 Claude-3.7 有將近 200% 的相對改善。這說明當時的通用模型零樣本排閱讀順序並不可靠。[arXiv 2504.10258](https://arxiv.org/pdf/2504.10258v3)
   - 一篇 EACL 2026 的論文用語言模型推斷整頁閱讀順序，並報告把閱讀順序關係放進提示後，GPT-4o 在 DocVQA 與 InfoVQA 上有改善。[論文](https://preview.aclanthology.org/ingest-eacl/2026.eacl-long.192.pdf)
   - ParseBench（2026）用提示要求視覺語言模型輸出版面標註、閱讀順序與含合併儲存格的 HTML 表格；摘要中提到一個閱讀順序 79.3% 的例子，並指出在密集的資料欄有排序不一致。[arXiv 2604.08538](https://arxiv.org/html/2604.08538v3)
   - WACV 2026 的商業文件零樣本表格擷取評測用 TEDS 比較 GPT-4o、GPT-5-mini 與 YOLO、DETR 類偵測器。結論是零樣本視覺語言模型在表格**結構**辨識上表現強，而在需要框的品質與抗雜訊能力時，微調過的偵測器領先。[WACV 2026](https://openaccess.thecvf.com/content/WACV2026/html/Thomas_Zero-Shot_Table_Extraction_in_Business_Documents_A_Unified_Benchmark_with_WACV_2026_paper.html)
   - NGTR 的作者發現輸入影像品質差是視覺語言模型做表格辨識的主要瓶頸。[arXiv 2412.20662](https://arxiv.org/html/2412.20662v3)
   - 兩篇論文（2025、2026）指出合併儲存格、巢狀表頭、空白儲存格對視覺語言模型仍然困難。[arXiv 2511.08298](https://arxiv.org/pdf/2511.08298v1)、[arXiv 2604.02880](https://arxiv.org/pdf/2604.02880)
8. **廠商自己對座標能力的說法**：Anthropic 官方文件寫明「Claude's coordinate and localization outputs are approximate」，並要求「verify outputs before relying on them」。【直讀】[影像文件](https://platform.claude.com/docs/en/build-with-claude/vision)。這與上面 WACV 2026 的結論方向一致：模型擅長判斷結構，不擅長產生精確的框。

    **旁證：OmniParser。** 微軟的 OmniParser 把同一個模式用在螢幕截圖上（先偵測可互動元件、編號，再交給模型選編號），README 自述搭配 v2 在 ScreenSpot Pro 得 39.5%。對象是使用者介面，不是文件，不能當作文件上有效的證據。授權方面要注意：README 的徽章寫 MIT，但程式庫的 LICENSE 檔是 CC BY 4.0；README 的「Model Weights License」一節寫明較早以 Ultralytics 為基礎的圖示偵測器維持 AGPL。【直讀】[README](https://github.com/microsoft/OmniParser/blob/master/README.md)、[LICENSE](https://github.com/microsoft/OmniParser/blob/master/LICENSE)

### 2.2 Anthropic Claude

以下全部【直讀】，來源為 [影像](https://platform.claude.com/docs/en/build-with-claude/vision)、[座標與外框](https://platform.claude.com/docs/en/build-with-claude/vision-coordinates)、[PDF](https://platform.claude.com/docs/en/build-with-claude/pdf-support)、[計價](https://platform.claude.com/docs/en/about-claude/pricing)。

**影像輸入的限制**

9. 支援 JPEG、PNG、GIF、WebP。動畫只取第一格。
10. 每張圖最大 8000×8000 px。單一請求超過 20 張圖時，每張圖適用更嚴格的尺寸上限；官方建議此時每邊不超過 3,000 px。
11. 每張圖最大 10 MB（base64 編碼後，Claude API）；Amazon Bedrock 上是 5 MB。
12. 每個請求最多 600 張圖（脈絡視窗 20 萬 token 的模型是 100 張）。請求大小上限 128 MB。

**解析度與 token 計算**

13. 影像以 28×28 px 的小塊處理，每塊是一個視覺 token。一張圖的成本是 `⌈寬/28⌉ × ⌈高/28⌉` 個視覺 token。
14. 兩個解析度層：

    | 層級 | 模型 | 長邊上限 | 視覺 token 上限 |
    |---|---|---|---|
    | 高解析度 | Claude 4.7 及之後的模型 | 2,576 px | 4,784 |
    | 標準 | 其他模型 | 1,568 px | 1,568 |

    高解析度自動生效，不需要額外參數。
15. 超過任一上限的影像會被等比例縮小到符合兩個上限的最大尺寸。官方範例：1920×1080 在標準層縮成 1456×819（1,560 token），在高解析度層不縮（2,691 token）；3840×2160 在高解析度層縮成 2576×1449（4,784 token）。
16. **token 上限會在兩邊都沒超過長邊上限時觸發縮小。** 官方範例：A4 以 130 DPI 掃描是 1075×1520 px，兩邊都小於 1,568，但成本是 39×55 = 2,145 token，標準層會把它縮成 924×1307。官方說這是座標對不準最常見的原因。
17. 所有影像都會在右側與下方補白到 28 的倍數。補白不含內容、不移動原點。換算座標時要除以縮小後的尺寸，不是補白後的尺寸。
18. 官方提供計算縮小後尺寸的參考實作（Python、TypeScript、C#、Go、Java、PHP、Ruby）。
19. 在影像區塊加上 `"transformations": {"oversized_image": "error"}`，伺服器遇到需要縮小的影像會回 400 錯誤，而不是悄悄縮小。計算 token 的端點也遵守這個設定。

**依官方公式算出的頁面成本**【推算】

20. 我用官方參考實作算了幾種常見頁面（參考實作對官方表格中的 1920×1080 與 3840×2160 兩列算出相同結果，可視為驗算通過）：

    | 頁面 | 像素 | 標準層 | 高解析度層 |
    |---|---|---|---|
    | A4，100 DPI | 827×1169 | 不縮，1,260 token | 不縮，1,260 token |
    | A4，150 DPI | 1240×1754 | 縮成 924×1307，1,551 token | 不縮，2,835 token |
    | A4，200 DPI | 1654×2339 | 縮成 924×1307，1,551 token | 縮成 1624×2296，4,756 token |
    | A4，300 DPI | 2480×3508 | 同上 | 縮成 1623×2296，4,756 token |
    | 16:9 投影片 | 1920×1080 | 縮成 1456×819，1,560 token | 不縮，2,691 token |
    | 16:9 投影片 | 2560×1440 | 同上 | 不縮，4,784 token |
    | 手機照片 | 3024×4032 | 縮成 952×1270，1,564 token | 縮成 1659×2212，4,740 token |

    結論：高解析度層的模型實際能看到的 A4 頁面大約是 **196 DPI**（1624×2296）。送 300 DPI 不會讓模型看得更清楚，只會被縮回去。

**座標與外框**

21. 官方說 Claude 能回傳表格、表單欄位、圖表元件、介面元件的外框，並把 OCR 流程、表單擷取、圖表解析列為用途。
22. 官方要求使用**絕對像素座標**，並明說要求 0 到 1000 之類的正規化座標效果不好。原文：「Claude does not work well when you ask for normalized coordinates」。
23. 回傳的座標是相對於**縮小後**的影像。最可靠的做法是自己先縮到官方公式算出的尺寸再上傳。
24. 官方說小元件在影像縮小後會失去精度，建議裁切感興趣的區域再送，回傳座標加上裁切原點。
25. **直接送 PDF 時無法使用座標**：頁面由伺服器轉成影像，尺寸不受控制，回傳的座標無法可靠對回頁面。官方建議自己把頁面轉成影像再送。

**PDF**

26. 每個請求最多 600 頁（脈絡視窗小於 100 萬 token 時是 100 頁）；請求上限 128 MB；不支援有密碼或加密的 PDF。
27. 處理方式：每一頁轉成一張影像，同時抽出該頁文字，兩者一起交給模型。
28. 成本：文字部分每頁通常 1,500–3,000 token；**每頁影像在所有模型上最多 1,568 個視覺 token**，因為伺服器把頁面影像壓在標準層的上限內。也就是說，直接送 PDF 拿不到高解析度層的好處。

**官方列出的限制**

29. 「Claude might hallucinate or make mistakes when interpreting low-quality, rotated, or very small images under 200 pixels.」
30. 座標與定位輸出是近似值；計數是近似值，物件小而多時尤其如此。
31. 官方提醒重度 JPEG 壓縮會讓文字難以辨讀，多次壓縮更嚴重。

**計價**（每百萬 token，美元）

32. 主要模型的單價：

    | 模型 | 輸入 | 輸出 | 批次輸入／輸出 |
    |---|---|---|---|
    | Claude Fable 5.2 | $8 | $40 | $4／$20 |
    | Claude Opus 5.5 | $4 | $20 | $2／$10 |
    | Claude Opus 5、4.8、4.7 | $5 | $25 | $2.50／$12.50 |
    | Claude Sonnet 5.5、Sonnet 5 | $2 | $10 | $1／$5 |
    | Claude Sonnet 4.6 | $3 | $15 | $1.50／$7.50 |
    | Claude Haiku 5.5（提示不超過 10 萬 token） | $0.10 | $0.50 | $0.05／$0.25 |
    | Claude Haiku 5.5（提示超過 10 萬 token） | $0.50 | $2.50 | $0.25／$1.25 |
    | Claude Haiku 4.5 | $1 | $5 | $0.50／$2.50 |

33. 影像沒有另外的價格，視覺 token 以該模型的輸入 token 單價計費。官方範例：1000×1000 的圖在 Haiku 4.5 約每千張 $1.30；在 Opus 5.5 約每千張 $5.18，4K 圖約每千張 $19.14。
34. 批次處理輸入與輸出都打五折，可與提示快取折扣疊加。快取讀取在 Opus 5.5 與 Sonnet 5.5 是輸入價的 0.05 倍。
35. Claude 4.7 及之後的模型使用新的分詞器，同樣的文字大約多出 30% 的 token。
36. 指定只在美國推論（`inference_geo: "us"`）時所有 token 價格乘以 1.1。
37. **每頁成本估算**【推算】：以 A4、150 DPI（2,835 個視覺 token）加 1,000 token 的文字提示與元素清單、輸出 3,000 token 的結構 JSON 計：

    | 模型 | 輸入成本 | 輸出成本 | 每頁合計 | 批次每頁 |
    |---|---|---|---|---|
    | Haiku 5.5 | $0.0004 | $0.0015 | 約 $0.002 | 約 $0.001 |
    | Sonnet 5.5 | $0.0077 | $0.030 | 約 $0.038 | 約 $0.019 |
    | Opus 5.5 | $0.0153 | $0.060 | 約 $0.075 | 約 $0.038 |
    | Fable 5.2 | $0.0307 | $0.120 | 約 $0.151 | 約 $0.075 |

    這只是量級估計。輸出 token 佔成本的大宗，所以輸出綱要要盡量精簡（只回編號與標籤，不回文字與座標）。模型的思考過程也算輸出 token，上表沒有計入。

**資料保留**

38. 以下全部【直讀】，來源為 [API 與資料保留](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)：
    - 官方承諾：「Retained data is never used for model training without your express permission.」
    - **Fable 與 Mythos 系列（5、5.1、5.2）被列為 Covered Models，要求 30 天資料保留**，除非 Anthropic 明確授權，否則不能用零資料保留；不符合的組織會收到 400 錯誤。Opus、Sonnet、Haiku 不在這份名單上。
    - 零資料保留（ZDR）是要向 Anthropic 業務申請、逐一組織開通的安排，不是預設。
    - 在零資料保留安排下，一般的訊息 API、PDF 輸入、提示快取都符合資格；結構化輸出是「有條件符合」（提示與輸出不保存，只有 JSON 綱要快取最多 24 小時）。
    - **批次處理不符合零資料保留，資料保留 29 天**；Files API 也不符合（檔案保留到刪除或到期）。也就是說，「走批次半價」與「零資料保留」不能同時成立。
    - 即使有零資料保留安排，被自動安全系統標記的內容最多可保留 2 年。
    - 另外，使用客戶自管金鑰的組織不能對 Fable 5.2 用結構化輸出。這一點來自 Anthropic 隨開發工具發行的 API 參考資料（快取日期 2026-11-03），不是公開網頁。
**38 之二　模型定位與汰換**【直讀】[模型總覽](https://platform.claude.com/docs/en/about-claude/models/overview)：官方對四個現行模型的描述是 Fable 5.2「For demanding reasoning and long-horizon agentic work」、Opus 5.5「For long-running agentic coding and knowledge work」、Sonnet 5.5「The best combination of speed and intelligence」、Haiku 5.5「For high-volume, latency-sensitive tasks such as classification, extraction, and routing」。官方建議不確定時從 Opus 5.5 開始。四個模型都支援影像輸入、脈絡 100 萬 token、輸出上限 12.8 萬 token。相對延遲：Sonnet 5.5 快、Haiku 5.5 最快。每個模型 ID 都是固定快照；承諾的最早退役日分別是 Opus 5.5 不早於 2027-09-22、Sonnet 5.5 不早於 2027-09-28、Haiku 5.5 不早於 2027-10-07、Fable 5.2 不早於 2027-11-09。也就是說，架構要預期大約每年換一次模型並重跑測試集。

### 2.3 OpenAI

**官方文件網站被擋，以下沒有一項是直讀。**

39. **影像計 token 的方法**【搜尋摘要】：官方影像指南說部分模型把影像切成 32×32 px 的小塊，塊數是 ⌈寬/32⌉×⌈高/32⌉，再乘以模型倍率。[影像指南](https://developers.openai.com/api/docs/guides/images-vision)（未直接開啟）
40. **detail 參數**【搜尋摘要】：有 low、high、original、auto。對 GPT-5.5 與 GPT-5.4，high 最多 2,500 塊或最長邊 2,048 px；original 最多 10,000 塊或最長邊 6,000 px。在 gpt-5.5 與 GPT-5.6 上，auto 與不指定都等同 original。
41. 對 gpt-6-astra【搜尋摘要】：low 把影像縮到 512×512 以內；original 保留原尺寸，除非任一邊超過 65,535 px。官方範例：4096×512 的圖不縮，128×16 = 2,048 塊，token 數為 ceil(2048×1.2) = 2,458。由此推得該模型的倍率是 1.2，但倍率表本身沒讀到。
42. 套用縮放規則後每張圖上限 30,000 塊，超過會被**拒絕**而不是縮小。【搜尋摘要】
43. **請求限制，來源互相矛盾**：一份搜尋摘要說現行官方文件寫的是每個請求總量 512 MB、最多 1,500 張圖；較舊的副本與第三方頁面寫的是 50 MB、500 張圖、每張 20 MB。支援 PNG、JPEG、WEBP、非動畫 GIF。
44. 官方指南建議需要座標的任務先把影像縮到上述限制內再送，並把回傳座標對回原圖。【搜尋摘要】
45. **計價：二手來源一致，官方頁面未直讀**：
    - 多家媒體報導 OpenAI 於 **2026-11-17** 發布 GPT-6.2 Astra 與 GPT-6.2 Sol。GPT-6.2 Astra 為 $8／$40（比 GPT-6 Astra 的 $10／$50 降 20%），快取輸入 $0.40；GPT-6.2 Sol 維持 $2／$10，快取輸入 $0.10。脈絡 105 萬 token、輸出上限 12.8 萬。提示超過 27.2 萬 token 適用較高費率（Astra 為 $16／$60）。【搜尋摘要】[報導一](https://aiweekly.co/alerts/openai-ships-gpt-62-astra-at-840-20-below-gpt-6-astra)、[報導二](https://runtimewire.com/article/runtimewire-confirms-gpt-6-2-astra-and-sol-have-launched)（均未直接開啟）
    - LiteLLM 的公開價目表【第三方直讀，2026-11-21 下載】與上述相符，並另外列出：gpt-6.1-terra $1／$5、gpt-6-luna $0.10／$0.50、gpt-5.5 $5／$30、gpt-5.4 $2.50／$15、gpt-5.4-mini $0.75／$4.50。這些條目都標示支援影像輸入與回應綱要，並註明來源是 OpenAI 計價頁。批次半價。[LiteLLM 價目表](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json)
    - 一篇 2026 年 11 月的第三方教學稱 GPT-6.1 Terra 於 2026-10-26 發布，$1／$5，與 LiteLLM 相符。
    - 有一則報導提到，11-18 時 OpenAI 計價頁上還沒有出現 GPT-6.2 的列。我的搜尋摘要從官方計價頁只抓到 $5／$30、$2.50／$15、$0.75 三個價位（對應 GPT-5.5、GPT-5.4、GPT-5.4 mini），可能是舊的快取。
    - 結論：型號與單價有多個二手來源互相印證，但**官方計價頁沒有直讀**，採用前要補讀。Sol、Terra、Luna 之間的能力差異沒有查。[計價頁](https://developers.openai.com/api/docs/pricing)（未直接開啟）
46. **每頁成本估算**【推算，建立在未確認的數字上】：A4、150 DPI（1240×1754）是 39×55 = 2,145 塊，在 2,500 塊預算內不縮；若倍率是 1.2 則約 2,574 token。以 gpt-6.2-sol 的 $2 計，影像輸入約 $0.005，與 Claude Sonnet 5.5 同量級。
47. 官方文件列出的視覺限制（小字、旋轉、非拉丁文字、空間推理等）本次沒有查到現行版本的內容。

### 2.4 Google Gemini

**官方文件網站被擋。以下只有外框格式與型號名稱兩項由 Google 放在 GitHub 的官方範例庫直讀，其餘都不是直讀。**

48. **每張圖的 token 數由 media_resolution 決定**【搜尋摘要】：Gemini 3 系列為 low 280、medium 560、high 1,120、ultra_high 2,240；不指定時是 1,120。ultra_high 只能針對個別內容項目設定。官方建議多數影像分析用 high。Gemini 2.5 的預設是 256、low 是 64。[媒體解析度文件](https://ai.google.dev/gemini-api/docs/media-resolution)（未直接開啟）
49. 這表示 Gemini 的影像成本是**固定級距**，不隨像素數線性增加。各級距對應的實際解析度沒有查到。
50. **外框格式**：Google 官方範例庫的空間理解筆記本把回傳的四個數字依序以「第 0 與第 2 個除以 1000 乘以高、第 1 與第 3 個除以 1000 乘以寬」換算，也就是 [y0, x0, y1, x1]、正規化到 0–1000。【直讀】[官方範例庫](https://github.com/google-gemini/cookbook/blob/main/quickstarts/Spatial_understanding.ipynb)。官方文件的說法相同（`box_2d` 為 [ymin, xmin, ymax, xmax]），並稱模型針對物件偵測與分割做過額外訓練。【搜尋摘要】[影像理解文件](https://ai.google.dev/gemini-api/docs/image-understanding)（未直接開啟）
51. 注意各家的座標慣例不同：Claude 用絕對像素、先 x 後 y；Gemini 用 0–1000 正規化、先 y 後 x；Qwen3-VL 與 GLM 用 0–1000 的相對座標（Qwen2.5-VL 是絕對座標）。轉接層必須各自處理。
52. **型號與計價：正在變動，部分來源互相矛盾**：
    - **Gemini 4 系列已經上線。** Google 官方範例庫的模型選單中有 `gemini-4-argon`、`gemini-4-carbon`，以及 `gemini-3.8-flash`、`gemini-3.7-flash`、`gemini-3.6-flash`、`gemini-3.5-flash-lite`、`gemini-3.1-pro-preview`。【直讀】[官方範例庫](https://github.com/google-gemini/cookbook/blob/main/quickstarts/Spatial_understanding.ipynb)
    - 媒體報導【搜尋摘要】：Gemini 4 Argon 於 2026-10-13 在付費 API 上線，優惠價 $2／$10 到 11-30，快取輸入 $0.10（之後 $0.20）；Gemini 4 Carbon 為 $2／$10 到 11-30，**12-01 起 $4／$20**；Gemini 4 Hydrogen $1.50／$7.50，有來源說 11-12 發布，另有來源說 Google 尚未正式公布；Gemini 4 Neon $3／$15，同樣有來源說只在計費目錄中出現。[The New Stack](https://thenewstack.io/gemini-4-argon-live/)、[報導](https://aiweekly.co/alerts/google-ships-gemini-4-carbon-to-paid-api-and-ai-ultra-tops-cursorbench-and)（均未直接開啟）
    - LiteLLM 價目表【第三方直讀】與上述優惠價相符（argon、carbon $2／$10；hydrogen $1.50／$7.50；neon $3／$15），並列出 Gemini 3 系列：gemini-3.1-pro-preview $2／$12（提示超過 20 萬 token 時 $4／$18）；gemini-3.8-flash、3.7-flash、3.6-flash $0.75／$3.75；gemini-3.5-flash $1.50／$9；gemini-3.5-flash-lite $0.30／$2.50；gemini-3.1-flash-lite $0.25／$1.50（標示 2027-05-07 停用）。
    - 對不上的地方：**Gemini 3.5 Flash 的輸出價三個來源三個數字**（$13.50、$7.50、$9）。搜尋摘要稱 Gemini 3.7 與 3.8 Flash 的 $0.75／$3.75 是到 2026-12-31 為止的優惠價。Argon 在 11-30 之後的價格沒有查到。
    - **結論：Gemini 的單價在 2026-12-01 前後會變，成本估算必須在那之後重做。**
53. **每頁成本估算**【推算，建立在未確認的數字上】：Gemini 3 預設 1,120 token 一張圖，以 gemini-3.8-flash 的 $0.75 計約 $0.0008，以 3.1 Pro Preview 的 $2 計約 $0.0022。影像輸入比另外兩家便宜，但 1,120 token 能承載多少版面細節沒有資料。Gemini 4 每張圖的 token 數沒有查到。
54. PDF 的頁數上限與每頁 token 數本次沒有查到。

### 2.5 可自行部署的開源視覺語言模型

55. **Qwen3-VL**：程式庫授權 Apache-2.0。2025-09-23 釋出 235B-A22B，10-04 釋出 30B-A3B 與 FP8 版，10-15 釋出 4B 與 8B，10-21 釋出 2B 與 32B；每個尺寸都有 Instruct 與 Thinking 版。README 沒有逐一寫各尺寸權重的授權。【直讀】[GitHub](https://github.com/QwenLM/Qwen3-VL)
56. Qwen3-VL 的文件相關能力（README 自述）：OCR 支援 32 種語言（前一代是 10 種），強調低光、模糊、傾斜下的穩定性與罕見字；文件解析可輸出含版面位置資訊的「Qwen HTML format」；定位框與點使用相對座標。官方定位範例筆記本寫明：「Qwen3-VL's default coordinate system has been changed from the absolute coordinates used in Qwen2.5-VL to relative coordinates ranging from 0 to 1000」。視覺處理器做 32 倍空間壓縮，範例設定每張圖 256–1,280 個視覺 token，尺寸取 32 的倍數。原生脈絡 25.6 萬 token。【直讀】同上、[定位範例](https://github.com/QwenLM/Qwen3-VL/blob/main/cookbooks/2d_grounding.ipynb)
57. Qwen3-VL 部署：vLLM 0.11.0 以上、SGLang、Transformers 4.57.0 以上。FP8 版需要 H100 以上與 CUDA 12 以上。README 沒有提 llama.cpp、GGUF、MLX。【直讀】同上
58. 第三方清單稱 Qwen3-VL 的 2B 到 235B 都是 Apache 2.0。【搜尋摘要】模型卡未能開啟，權重授權未確認。
59. **Qwen3.5、3.6、3.8**：程式庫 QwenLM/Qwen3.8 授權 Apache-2.0，README 對權重的說法是「Please find the license file released with the model weights on Hugging Face Hub or ModelScope」。發布紀錄：Qwen3.5-397B-A17B（2026-02-16）；Qwen3.5-122B-A10B、35B-A3B、27B（2026-02-24）；Qwen3.5-9B、4B、2B、0.8B（2026-03-02）；Qwen3.6-35B-A3B（2026-04-16）；Qwen3.6-27B（2026-04-22）；Qwen3.8-2.4T-A95B（2026-08-12）；Qwen3.8-27B（2026-08-14）。【直讀】[GitHub](https://github.com/QwenLM/Qwen3.8)
60. README 稱 Qwen3.5 是「Unified Vision-Language Foundation」，並自述在推理、程式、代理、視覺理解的評測上勝過 Qwen3-VL；llama.cpp 支援 Qwen3.5 系列的文字與視覺；MLX 可用 mlx-lm（文字）與 mlx-vlm（視覺加文字）。部署範例用 vLLM 與 SGLang，並開啟工具呼叫。README 沒有提 JSON 或結構化輸出，也沒有以文字列出文件類評測數字。【直讀】同上
61. Qwen3.5-9B 的模型卡標示 `license: apache-2.0`、類別 `image-text-to-text`；Qwen3.5-2B 與 4B 的鏡像或衍生頁面也顯示 Apache-2.0；Qwen3.5-0.8B 模型卡自述為「Causal Language Model with Vision Encoder」。【搜尋摘要】[模型卡](https://huggingface.co/Qwen/Qwen3.5-9B)（未直接開啟）
62. Qwen3.8-27B 的模型卡自述為能理解影像與影片的原生視覺語言模型。The Batch 報導 Qwen3.8-Max 的公開權重只有文字能力，不含視覺輸入。Qwen3.6 與 Qwen3.8 的權重授權沒有查到。【搜尋摘要】[The Batch](https://www.deeplearning.ai/the-batch/qwen3-8-max-lands-with-a-bang)
63. **Qwen2.5-VL 三個尺寸三種授權**：3B 是 Qwen Research License（研究用途）；7B 是 Apache-2.0；72B 是自訂的 Qwen License。【搜尋摘要】這說明同一系列內不同尺寸的授權可以不同，**每個要用的權重都必須單獨確認**。
64. **InternVL**：程式庫授權 MIT；README 註明「Parts of this project contain code and models from other sources, which are subject to their respective licenses」。InternVL3.5 於 2025-08-26 釋出（1B、2B、4B、8B、14B、38B、20B-A4B、30B-A3B、241B-A28B），其中 20B-A4B 以 GPT-OSS-20B-A4B 為底座。InternVL3 於 2025-04-11 釋出（1B 到 78B）。README 上沒有更新的世代。【直讀】[GitHub](https://github.com/OpenGVLab/InternVL)
65. InternVL 用動態高解析度：把影像切成 448 px 的小塊（`max_num` 預設 12），另加一張縮圖。README 的待辦清單上「Support vLLM and Ollama」未勾選；有 lmdeploy 的 AWQ 4 位元量化。【直讀】同上
66. InternVL3_5-8B 與 InternVL3_5-38B 的模型庫授權標籤是 apache-2.0。【搜尋摘要】[模型卡](https://huggingface.co/OpenGVLab/InternVL3_5-8B)（未直接開啟）。與程式庫的 MIT 不同，但兩者都是寬鬆授權。
67. **MiniCPM-V**：README 明文「Code and model weights are released under Apache-2.0」。MiniCPM-V 4.7 於 2026-10-12 釋出，有 35B-A3B（MoE）與 1B 兩個版本，都有 GGUF，並註明 GGUF 權重支援 CPU 推論。MiniCPM-V 4.6 於 2026-05-11 釋出。MiniCPM-o 4.5 是 9B 的全模態模型。MiniCPM-OCR 1.0 於 2026-10-28 釋出，以 4.6 為基礎，自述在 Real5-OmniDocBench 與 OCRBench v2 上領先。支援 Ollama、llama.cpp、iOS、Android、HarmonyOS；自述在 iPhone 17 Pro Max 上每秒 13 個以上 token、記憶體 3 GB。【直讀】[GitHub](https://github.com/OpenBMB/MiniCPM-V)
68. MiniCPM-V 的 README 另有一個「Model license」連結，指向 `docs/project_en.md`。原文是：「The MiniCPM-o/V model weights and code are open-sourced under the Apache-2.0 license」，另外請使用者「consider optionally filling out a brief registration questionnaire」，是自願填寫，不是授權條件。程式庫的 LICENSE 檔是 Apache License 2.0。這是本次唯一一個**權重授權由官方原文直讀確認**的開源模型。【直讀】[Model License 說明](https://github.com/OpenBMB/MiniCPM-V/blob/main/docs/project_en.md)、[LICENSE](https://github.com/OpenBMB/MiniCPM-V/blob/main/LICENSE)

    其他程式庫的 LICENSE 檔也直讀確認過（只代表程式碼）：Qwen3-VL 為 Apache-2.0、InternVL 為 MIT、GLM-V 為 Apache-2.0。
69. **Gemma 4**：Google Open Source Blog（2026-04-02）稱 Gemma 4 是該系列第一個以 OSI 認可的 Apache 2.0 授權發布的模型。尺寸為 E2B、E4B、26B MoE、31B Dense，四個都能處理影像與影片。先前的 Gemma 用 Google 自訂授權。【搜尋摘要】[Google Open Source Blog](https://opensource.googleblog.com/2026/03/gemma-4-expanding-the-gemmaverse-with-apache-20.html)（未直接開啟）
70. **GLM-V**：程式庫授權 Apache-2.0；模型有 GLM-4.6V（106B）、GLM-4.6V-Flash（9B）、GLM-4.5V、GLM-4.1V-9B-Thinking（2025-07-01）、GLM-5V-Turbo（2026-04-02）、GLM-5.3-Flash（2026-08-26，自述為該系列第一個開源的原生多模態模型）。定位框是左上與右下兩角，依影像寬高正規化後乘以 1000。GLM-4.6V 首次內建函式呼叫。**維護者於 2026-09-02 宣布此程式庫不再維護。**【直讀】[GitHub](https://github.com/zai-org/GLM-V)
71. GLM-4.6V 的 Hugging Face 模型卡標示 MIT，VentureBeat 的報導相同；SourceForge 的鏡像標示 Apache-2.0。【搜尋摘要】
72. **與 ocr.md 的交叉參照**：PaddleOCR 的 PP-OCRv6 文件列出通用視覺語言模型在其繁體中文辨識資料集上的成績（Qwen3-VL-235B 76.4、Gemini-3.1-Pro 69.5、GPT-5.5 57.5），並聲稱視覺語言模型會依語言先驗「幻覺式修正」文字。那是 PaddleOCR 自己的內部資料集與自己的說法，但方向與 2.6 節的獨立研究一致。詳見 [ocr.md](ocr.md) 第 3.1 節。

### 2.6 用大型模型做 OCR 後校正

以下論文全部為【搜尋摘要】，沒有一篇直接開啟。研究對象多半是歷史文獻，沒有一篇針對繁體中文。

73. **成效不穩定**：Kanerva 等人（2025）的〈OCR Error Post-Correction with LLMs in Historical Documents: No Free Lunches〉測試開放權重的語言模型，英文的字元錯誤率有下降，芬蘭文達不到實用水準。後續研究把原因歸於模型對該語言的訓練資料少。[arXiv 2502.01205](https://arxiv.org/pdf/2502.01205)
74. **原文錯誤少的時候最容易改壞**：ICDAR 2026 HIPE-OCRepair 競賽的總覽報告，校正收益在 OCR 雜訊中到高的時候最大；低雜訊的文字對過度校正很敏感，少量不必要的修改就會讓結果變差。總覽把「控制過度校正與幻覺」列為未來工作。[arXiv 2607.08143](https://arxiv.org/pdf/2607.08143)
75. **平均值會掩蓋個別文件變差**：CLOCR-C 論文的摘要指出，多數文件經校正後改善，但在其某項統計上幾乎所有模型的字元錯誤率反而變差；行序錯亂時幻覺特別嚴重。這一條的確切數字與適用範圍需要讀原文確認。[arXiv 2408.17428](https://arxiv.org/pdf/2408.17428)
76. **模型會發明原本沒有的錯誤**：一篇 2026 年比較數位化流程的研究發現，所有加了語言模型的流程整體表現都比不加時差，並觀察到模型發明輸入中不存在的錯誤、以及自行加上評論。[arXiv 2608.24976](https://arxiv.org/html/2608.24976v1)
77. **錯誤率低不代表沒有幻覺**：一篇 2026 年研究烏拉圭歷史文件的論文主張，低字元錯誤率可能掩蓋視覺語言模型的幻覺；影像嚴重劣化時，模型依賴語言先驗反而成為弱點。[arXiv 2607.24077](https://arxiv.org/pdf/2607.24077)
78. **模型會悄悄改寫**：一篇 2026 年比較 TrOCR 與視覺語言模型的研究報告，視覺語言模型會做拼字正規化、把舊式拼法現代化、並在看不清楚的區域產生幻覺內容。另一篇古希臘文版本的研究發現，在字元擾動測試下，受測的每個視覺語言模型都比傳統 OCR 更不忠於畫面上的文字。[arXiv 2605.27750](https://arxiv.org/pdf/2605.27750)、[arXiv 2602.14524](https://arxiv.org/html/2602.14524)
79. **同時給影像與 OCR 文字**：一篇 2026 年阿拉伯文手稿研究把頁面影像與 Tesseract 輸出一起交給視覺語言模型，探討有雜訊的 OCR 文字是提供了錨點、還是把模型拉向 OCR 的系統性錯誤。摘要沒有給出結論。[arXiv 2608.22366](https://arxiv.org/pdf/2608.22366)
80. **已知的緩解做法**：一項研究報告，把模型輸出限制在合法詞彙內可以減少幻覺而保留校正能力。[ResearchGate](https://www.researchgate.net/publication/396768884_Opportunities_and_Challenges_of_LLMs_as_Post-OCR_Correctors)（未直接開啟）
81. 搜尋結果中另有〈Multimodal LLMs for OCR, OCR Post-Correction, and Named Entity Recognition in Historical Documents〉（[arXiv 2504.00414](https://arxiv.org/pdf/2504.00414)），內容沒有讀到，只列為線索。
82. 廠商自己的說法與上述一致：Anthropic 官方文件承認低品質、旋轉、極小的影像可能造成幻覺（第 29 條）；PaddleOCR 文件聲稱視覺語言模型會依語言先驗修改文字（第 72 條）。

    **中文方面的相鄰證據**（【搜尋摘要】，對象是中文文字校正，不是 OCR 後校正）：一篇 2023 年的論文標題就是〈On the (In)Effectiveness of Large Language Models for Chinese Text Correction〉（[arXiv 2307.09007](https://arxiv.org/pdf/2307.09007)）；一篇 AACL 2026 的論文把「對乾淨輸入過度校正」列為實務上的挑戰之一（[論文](https://aclanthology.org/2026.aacl-long.41.pdf)）；另有一個免訓練的方法用漢明距離（限制可改動的字數）來防止過度校正（[arXiv 2504.01519](https://arxiv.org/html/2504.01519v3)）。沒有查到針對繁體中文印刷文件 OCR 後校正的研究。

### 2.7 強制結構化輸出

**Anthropic**（全部【直讀】，[官方文件](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)）

83. 狀態是正式功能（stable），不需要測試版標頭。文件寫「Structured outputs are available on every model」，在 Claude API、Amazon Bedrock、Google Cloud、Microsoft Foundry 都可用。
84. 兩種用法：`output_config.format`（`type: "json_schema"`）約束回應本身；工具定義加 `strict: true` 約束工具參數。兩者可同時使用。舊的 `output_format` 參數已棄用。
85. 原理是把 JSON Schema 編譯成文法，以受限解碼約束輸出。新綱要第一次使用時有編譯延遲；編譯結果快取 24 小時。
86. 支援：基本型別、`enum`、`const`、`anyOf`、`allOf`（有例外）、`$ref` 與 `$defs`、`required`、`additionalProperties: false`（物件必須設定）、部分字串格式、`minItems` 為 0 或 1。
87. **不支援**：遞迴綱要、`oneOf`、`not`、數值限制（`minimum`、`maximum`、`multipleOf`）、`minLength`、`maxLength`、超出 `minItems` 0 或 1 的陣列限制、外部 `$ref`。用到不支援的功能會回 400 錯誤。
88. **複雜度上限**：每個請求最多 20 個嚴格工具；選填參數合計最多 24 個；使用 `anyOf` 或型別陣列的參數合計最多 16 個。另有未公開的內部上限，超過會回「Schema is too complex for compilation」；編譯逾時為 180 秒。官方建議把參數設為必填、把巢狀結構攤平。
89. 物件不能有任意鍵。鍵不固定時，官方建議改用陣列，每個項目含 `key` 與 `value`。
90. 屬性順序：必填屬性在前、選填在後，各自依綱要順序。
91. **不保證符合綱要的三種情況**：安全拒絕（`stop_reason: "refusal"`）；撞到 `max_tokens`（輸出被截斷）；列舉值的大小寫可能與綱要不同（官方建議比對時不分大小寫）。
92. 與引用（citations）功能不相容，同時啟用會回 400。可與批次處理、串流、token 計算並用。文法只約束最後的回應，不約束思考內容。
93. 在 Claude Opus 5.5、Sonnet 5.5、Fable 5.1 與 5.2 上，強制指定工具（`tool_choice` 為 `any` 或 `tool`）會回 400 錯誤，所以「用強制工具呼叫拿 JSON」的舊做法在這些模型上不能用，要改用 `output_config.format`。【直讀】來源是 Anthropic 隨開發工具發行的 API 參考資料。

**OpenAI**

94. 官方稱 Structured Outputs 讓模型「always generate responses that adhere to your supplied JSON Schema」；發布公告說模型仍可拒絕不安全的請求。文件片段建議新專案從 gpt-6-astra 開始。較舊的模型只能用 JSON 模式。【搜尋摘要】[官方文件](https://developers.openai.com/api/docs/guides/structured-outputs)（未直接開啟）、[發布公告](https://openai.com/index/introducing-structured-outputs-in-the-api/)（未直接開啟）
95. 綱要上限：一則論壇貼文引用舊版文件，寫的是最多 100 個物件屬性、5 層巢狀。現行數字沒有查到。

**Google Gemini**

96. Google 2025-11-05 的公告稱所有現役 Gemini 模型都支援 JSON Schema，可直接搭配 Pydantic 與 Zod；輸出鍵的順序依綱要（Gemini 2.5 起）。【搜尋摘要】[Google 部落格](https://blog.google/technology/developers/gemini-api-structured-outputs/)（未直接開啟）
97. Vertex AI 文件說要保證輸出是合法 JSON，請求必須同時帶回應綱要與 `response_mime_type: application/json`，並建議減少選填屬性與列舉值。【搜尋摘要】
98. 綱要太複雜時 API 會以「too many states for serving」拒絕；Google 開發者論壇有人抱怨上限沒有文件可循。這兩點都是使用者回報，不是官方文件。【搜尋摘要】

**自行部署**

99. **vLLM**：支援 `choice`、`regex`、`json`（JSON Schema）、`grammar`（EBNF）、`structural_tag`；線上服務用 `extra_body={"structured_outputs": {...}}` 或 `response_format` 的 `json_schema`。後端是 xgrammar 與 guidance（llguidance），預設 `auto`。舊的 `guided_json` 等參數在 v0.12.0 移除。lm-format-enforcer 後端在 v0.33.0 開始警告、v0.35.0 移除。文件提醒不同後端的正規表示式語法不同（Python 風格與 Rust 風格），切換後端輸出可能改變。文件沒說明是否適用於視覺模型。【直讀】[官方文件](https://github.com/vllm-project/vllm/blob/main/docs/features/structured_outputs.md)
100. **llama.cpp**：GBNF 是用來約束輸出的文法格式；llama.cpp 可把 JSON Schema 的子集轉成 GBNF。`llama-server` 的 `/chat/completions` 用 `response_format`，其他端點用 `json_schema` 欄位。【直讀】[官方文件](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)
101. llama.cpp 的限制：`additionalProperties` 預設為 false；`minimum` 與 `maximum` 只對整數有效；巢狀 `$ref` 有問題；不支援 `patternProperties`、`not`、`uniqueItems`、條件式；**不支援的功能會被靜默略過**；文法有效能問題。文件特別註明「The JSON schema is only used to constrain the model output and is not injected into the prompt」，所以提示裡要自己描述期望的結構。【直讀】同上
102. **授權**（各程式庫 LICENSE 檔開頭）：vLLM 為 Apache License 2.0；llama.cpp 為 MIT；XGrammar 為 Apache License 2.0；Outlines 為 Apache License 2.0；llguidance 為 MIT；Ollama 為 MIT。都沒有 GPL 或 AGPL。【直讀】[vLLM](https://github.com/vllm-project/vllm/blob/main/LICENSE)、[llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/LICENSE)、[XGrammar](https://github.com/mlc-ai/xgrammar/blob/main/LICENSE)、[Outlines](https://github.com/dottxt-ai/outlines/blob/main/LICENSE)、[llguidance](https://github.com/guidance-ai/llguidance/blob/main/LICENSE)、[Ollama](https://github.com/ollama/ollama/blob/main/LICENSE)。這些專案各自的相依套件授權沒有查。

## 3. 建議

### 3.1 先講定位：大型模型做什麼、不做什麼

查到的事實指向同一個分工：

- **不讓大型模型讀字。** 文字來自 OCR 或 PDF 的文字層。視覺語言模型會依語言先驗改寫畫面上的字（第 76–78 條），而本產品的急救市場最重視文字正確。
- **不讓大型模型產生座標。** 座標來自版面偵測與 OCR。官方自己說座標是近似值（第 8、30 條），獨立評測也顯示微調過的偵測器在框的品質上領先（第 7 條）。
- **讓大型模型做結構判斷。** 哪些文字行屬於同一段、區塊的閱讀順序、區塊的角色（標題、內文、圖說、頁首頁尾）、表格的列與欄歸屬。這是零樣本視覺語言模型相對強的部分（第 7 條 WACV 2026）。

具體做法是編號標記法的變形：把偵測到的元素畫上編號，連同元素清單（編號、外框、文字片段）一起交給模型，模型**只回傳編號**。回傳結果可以用程式檢查（每個編號恰好出現一次、沒有不存在的編號、表格的列欄數一致），檢查不過就退回規則式的結果。

要誠實說明的是：**這個做法在文件上的效果沒有公開數字**（第 5 條）。它是由相鄰證據推出來的設計，不是已被證實的方法。必須用自己的測試集驗證，而且流程要設計成沒有大型模型也能跑完，大型模型只是提升品質的一層。

編號標記法已知的失敗方式是標記遮住或混入內容（第 6 條）。對策是把編號畫在外框的外側或角落、加白底，並且同時送一張沒有標記的原圖。這是建議，沒有實測依據。

### 3.2 首選（線上平台）：商用 API，介面做成可抽換，先接 Claude

授權面上，三家商用 API 的條件相同：付費即可商用，沒有權重可散布，桌面版只能連線呼叫。授權限制不構成選擇依據。

建議先接 Anthropic Claude，理由全部來自可信度，而不是來自效果比較：

1. **只有 Claude 的規格是直讀確認的。** OpenAI 與 Google 的文件網站在這個工作環境被擋，數字靠搜尋摘要與第三方價目表。OpenAI 四天前（2026-11-17）才換代；Google 的 Gemini 4 優惠價到 11-30 為止，Carbon 在 12-01 漲一倍。兩家的影像 token 規則我都沒有讀到完整原文。
2. **縮放規則公開而且有參考實作**（第 13–19 條）。本產品要把模型選的編號對回自己的座標系；雖然不依賴模型的座標，但標記畫在哪個尺寸的影像上仍然要算準。`oversized_image: "error"` 可以把「悄悄縮小」變成明確錯誤。
3. **高解析度層剛好涵蓋 A4 在 150 DPI 的整頁**（2,835 token，不縮）。上限約等於 196 DPI。
4. **結構化輸出是正式功能，限制條列清楚**（第 83–93 條），綱要設計有明確的邊界可循。

模型分級：預設 Claude Sonnet 5.5（每頁約 $0.04，批次約 $0.02）；簡單頁面用 Haiku 5.5（每頁約 $0.002）；失敗或複雜頁面升級到 Opus 5.5。官方的說法是不確定時從 Opus 5.5 開始；本產品每一頁都要呼叫，對成本敏感，所以建議用自己的測試集判斷 Sonnet 5.5 是否足夠，不夠再把預設改成 Opus 5.5。不建議用 Fable 5.2 處理使用者文件，因為它要求 30 天資料保留（第 38 條）。

資料保留是產品層的決定，要先定：如果對使用者承諾文件不留存，就要向 Anthropic 申請零資料保留，而且**不能用批次處理**（批次保留 29 天）也不能用 Files API，上面的批次價格就不適用。OpenAI 與 Google 的對應條件本次沒有查。

**我沒有找到任何證據說 Claude 在版面分組或閱讀順序上比 GPT 或 Gemini 準。** 上線前應該用自家的繁體中文測試集比較三家，再決定預設用哪一家。依目前查到的單價，三家的中階模型在同一個量級（Claude Sonnet 5.5 $2／$10、GPT-6.2 Sol $2／$10、Gemini 4 Argon 優惠價 $2／$10），低價模型則是 Claude Haiku 5.5 與 GPT-6 Luna 都是 $0.10／$0.50。Gemini 3 的影像輸入成本看起來最低（每張圖固定 1,120 token），如果它在測試中夠準，會是更便宜的選擇。所以轉接層從第一天就要做成可抽換，並注意三家的座標慣例不同（第 51 條）。價格比較要在 2026-12-01 之後重做一次。

實作上要遵守的幾件事，都來自已查證的事實：

- **自己把 PDF 頁面轉成影像再送，不要直接送 PDF。** 直接送 PDF 每頁影像只有標準層的 1,568 token，而且座標對不回頁面（第 25、28 條）。
- **先用官方公式算好尺寸、自己縮圖再上傳**，並設定 `oversized_image: "error"`。
- **輸出綱要要扁平**：元素用陣列、每項帶編號；所有屬性設為必填；不用遞迴（表格的巢狀結構要攤平成「儲存格編號、列、欄、跨列、跨欄」）；不用數值上下限（第 87–88 條）。
- **處理三種不符綱要的情況**：拒絕、截斷、列舉值大小寫（第 91 條）。
- **輸出只含編號與標籤**。輸出 token 是成本大宗（第 37 條），也避免模型有機會改寫文字。
- 不趕時間的工作可以走批次處理，半價；但批次資料保留 29 天，與零資料保留互斥（第 38 條）。

### 3.3 備選（離線與桌面版）：Apache-2.0 的 Qwen 或 MiniCPM-V，搭配 llama.cpp

桌面版要把權重打包交給使用者，必須是可再散布的寬鬆授權。

- **伺服器端自行部署**（例如客戶要求資料不出境）：Qwen3-VL 8B 或 32B，或 Qwen3.5-9B 或 27B，用 vLLM 部署並開啟結構化輸出。程式碼 Apache-2.0 已直讀確認。
- **桌面版**：MiniCPM-V 4.7 的 1B 版或 4.6（README 明文程式碼與權重都是 Apache-2.0，有 GGUF 可用 CPU 推論），或 Qwen3.5 的 2B、4B（llama.cpp 支援視覺）。用 llama.cpp 的 JSON Schema 轉文法功能約束輸出。
- **次要備選**：Gemma 4 的 E4B（Apache-2.0，僅搜尋摘要）；InternVL3.5 的 8B（程式碼 MIT、權重標籤 apache-2.0，僅搜尋摘要）。

必須先做的事：

1. **逐一打開要用的權重庫的 LICENSE 檔。** 本次只有 MiniCPM-V 的權重授權由官方原文直讀確認（第 68 條）；Qwen、InternVL、Gemma、GLM 的權重授權都沒有直讀（huggingface.co 被擋）。Qwen2.5-VL 三個尺寸三種授權的前例說明不能從系列名稱推定（第 63 條）。就授權確定性而言，MiniCPM-V 目前是桌面版最穩的選擇。
2. **測繁體中文。** 沒有任何一個開源模型查到繁體中文文件結構任務的數字。
3. **llama.cpp 的綱要要用它支援的子集重寫**，因為不支援的功能會被靜默略過（第 101 條），並且要在提示中自己描述結構。

小模型的判斷力明顯低於商用大模型，桌面版離線時要預期結構品質下降，還原度報告應該標示這次是用哪一種模型處理的。

### 3.4 OCR 後校正：只允許有約束的校正

查到的研究一致指出自由改寫有風險，而且在原文錯誤少的時候最容易改壞（第 74 條）。本產品的 OCR 來源是印刷文件，屬於低雜訊，正是最容易被改壞的情況。建議：

- **預設不做全文後校正。**
- 只針對 OCR 信心值低的片段，讓模型**從 OCR 的候選字中選擇**，或只標記「可疑」，不讓它自由生成（對應第 80 條的詞彙約束做法）。[ocr.md](ocr.md) 指出 PP-OCR 不輸出候選字、Tesseract 可以，這會影響這條路線的可行性。
- 任何修改都要能對回影像驗證（例如對該片段重新辨識），否則不套用。
- 所有經模型修改的字都要記在還原度報告裡，標為「推論」。
- 用結構化輸出回傳「片段編號、原字、建議字、理由類別」，程式端限制每個片段的編輯距離。

這些是由風險文獻推出的設計原則，沒有查到針對中文印刷文件驗證過這套做法的研究。

### 3.5 不建議

- **讓大型模型直接輸出整頁文字或整頁 Markdown 當作最終文字來源。** 理由見 2.6 節。
- **依賴大型模型回傳的座標做排版。** 理由見第 8、30 條。
- **Qwen2.5-VL-3B**（研究用途授權）、**Qwen2.5-VL-72B**（自訂授權，原文未讀）。
- **OmniParser 的舊版圖示偵測權重**（AGPL-3.0）。
- **Claude Fable 5.2 用於使用者文件**，除非確認資料保留條件可接受。
- **把 GLM-V 當長期依賴。** 官方程式庫已停止維護。

## 4. 查不到的事

1. **編號標記法用在文件版面分組、閱讀順序、表格結構的實測數字。** 兩次搜尋都沒有找到這樣的論文。原始論文（arXiv 2310.11441）因網站被擋沒有打開，裡面的實驗數字沒有讀到。
2. **OpenAI 官方文件的原文。** 包括：各模型的影像 token 倍率表、現行的請求大小與張數上限（兩種說法）、目前的型號清單與單價、Structured Outputs 的現行綱要上限、官方列出的視覺限制、PDF 輸入的計費方式。
3. **Google Gemini 官方文件的原文。** 包括：現行單價（Gemini 4 的優惠價到 2026-11-30，之後的價格只查到 Carbon；Gemini 3.5 Flash 三個來源互相矛盾）、Gemini 4 每張圖的 token 數、各 media_resolution 級距對應的實際像素解析度、PDF 的頁數上限與每頁 token 數、結構化輸出的綱要限制、Hydrogen 與 Neon 是否已正式發布。
4. **多數開源模型權重的 LICENSE 檔原文。** Hugging Face 被擋。Qwen3-VL、Qwen3.5、Qwen3.6、Qwen3.8、InternVL3.5、Gemma 4、GLM-4.6V、GLM-5.3-Flash 的權重授權都只有搜尋摘要或第三方說法。只有 MiniCPM-V 由 GitHub 上的官方說明直讀確認。
5. **三家商用模型在繁體中文文件上做版面分組、閱讀順序、表格結構的比較。** 沒有找到獨立評測。唯一相關的是 PaddleOCR 自己的內部資料集，量的是文字辨識，不是結構。
6. **開源視覺語言模型在繁體中文文件上的任何數字。**
7. **Claude Haiku 5.5 是否屬於高解析度層。** 文件寫「Claude 4.7 及之後的模型」，依字面應屬於，但沒有逐一列出型號，也沒有實測。
8. **OpenAI 與 Google 的資料保留條件。** 沒有查。Anthropic 的條件已直讀（第 38 條），但沒有零資料保留安排時一般 API 請求的預設保留天數，要看該頁連出去的商用資料保留政策，那一頁沒有讀。
9. **用大型模型校正中文（尤其繁體中文）印刷文件 OCR 的研究。** 查到的研究都是歷史文獻，語言是英文、芬蘭文、俄文、阿拉伯文、古希臘文、西班牙文。
10. **多模態後校正（同時給影像與 OCR 文字）是否比純文字後校正安全。** 查到一篇提出這個問題的研究，摘要沒有給出結論。
11. **標記的畫法（位置、大小、顏色）對模型判斷的影響。** 沒有找到針對文件的研究。
12. **vLLM 的結構化輸出是否完整支援視覺模型。** 官方文件沒有說明。
13. **本次沒有查的開源模型**：Mistral Small 與 Pixtral、Phi-4-multimodal、Llama 視覺版、SmolVLM、Florence-2、Molmo。它們的授權與能力不在這份紀錄的結論內。
14. **各家 API 的速率限制、延遲、區域可用性。** 沒有查。
15. **OpenAI 與 Google 各級模型之間的能力差異**（例如 GPT-6.2 Sol 與 Astra、Gemini 4 Argon 與 Carbon），以及它們在文件影像上的官方能力說明。沒有查到可引用的官方原文。
16. **vLLM、llama.cpp 等工具的相依套件授權。** 只查了各專案本身的 LICENSE 檔。

# Mortise 前端（web/）

Vite + React + TypeScript，CSS 手寫（沒有 UI 套件）。設計稿：`docs/design/ui-mockup-20261122.png`。
後端 API 說明見 `mortise/api/app.py` 開頭。

## 在 Mac 上安裝與執行

需要：Python 3.13、Node 22（npm 10）。LibreOffice 是選用的（見下面「LibreOffice 的作用」）。
Mac 沒有 fontconfig（`fc-match`、`fc-list`）也沒關係：後端用 `mortise/tools/fontfind.py` 直接掃描系統字型目錄。

```bash
# 1. 工具（用 Homebrew；已經有 Python 3.13 與 Node 22 就跳過）
brew install python@3.13 node@22
export PATH="$(brew --prefix node@22)/bin:$PATH"   # node@22 不會自動加進 PATH，可以寫進 ~/.zshrc
brew install --cask libreoffice                     # 選用

# 2. 後端（在專案根目錄）
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[api,dev]"

# 3. 前端
cd web
npm install
npm run build          # 產生 web/dist/
cd ..

# 4. 啟動：後端會在 "/" 提供 web/dist 裡的前端
uvicorn mortise.api.app:app --port 8000
open http://127.0.0.1:8000
```

轉換的檔案存在 `./jobs`（`MORTISE_JOBS` 可改），30 天後自動刪除（`MORTISE_RETENTION_DAYS`）。

### LibreOffice 的作用

後端用 `soffice` 指令找 LibreOffice。Mac 上它在 App 裡面，要加進 PATH 才找得到：

```bash
export PATH="/Applications/LibreOffice.app/Contents/MacOS:$PATH"
curl -s http://127.0.0.1:8000/api/health    # "verify_engine" 不是 null 就表示找到了
```

目前（照前後端約定）只有 **PowerPoint** 用到它：

| 輸出格式 | 沒有 LibreOffice | 有 LibreOffice |
| --- | --- | --- |
| PowerPoint（.pptx） | 預覽與分數都來自 Mortise 自己畫的「中性渲染」 | 預覽由 LibreOffice 畫（代替 PowerPoint），⑦也用它驗證、提出修正，比較準 |
| Word（.docx） | 中性渲染 | **仍是中性渲染**（約定第 7 點只有 pptx 用 LibreOffice；已提出改成 Word 也用它的請求） |
| 可編輯 PDF、Illustrator PDF、SVG | 直接畫輸出檔本身（PDFium、resvg），與 LibreOffice 無關 | 同左 |

畫面上會照實標示：預覽不是輸出檔本身畫的時候，框內會寫「這是 Mortise 自己畫的預覽，不是 Word 實際顯示的樣子…」；
PowerPoint 在沒有 LibreOffice 時另外提示「在伺服器安裝 LibreOffice，就能看到更接近的預覽」。
沒有分數時，框內的「沒有量測還原度 · 為什麼？」會列出伺服器報告裡的原因。

## 開發

```bash
# 終端機 1：後端（開發時前端由 Vite 提供，/api 轉到 8000）
uvicorn mortise.api.app:app --port 8000 --reload
# 終端機 2：前端
cd web && npm run dev     # http://127.0.0.1:5173
```

`vite.config.ts` 把 `/api` 轉到 `http://127.0.0.1:8000`。改了前端要給後端提供，就重新 `npm run build`（後端每次請求都會檢查 `web/dist`，不必重啟）。

結構：

- `src/api`：型別（照後端欄位名稱）與呼叫（`client.ts`：上傳用 XHR 以便回報進度；進度用 SSE，斷線改輪詢；網路錯誤與 5xx 會退避重試）。
- `src/state`：`store.ts`（reducer，純資料）、`useController.ts`（所有副作用）、`derive.ts`（尺寸、方向、從工作設定填回卡片）、`corrections.ts`（修正對話框只送改過的欄位）。
- `src/components`：`Stage`（大黑框）、`Viewer`（縮放、平移）、`SettingsCard`（右邊白卡）、`FileMenu`、`AccountMenu`、`EditLayer`（修正）。
- `src/lib`：`localDoc.ts`（pdf.js 在瀏覽器裡畫預覽，有優先順序的佇列、LRU 快取、canvas 面積上限）、`serverImages.ts`（用 fetch 取預覽圖以讀 `X-Mortise-Render`）、`errors.ts`（伺服器錯誤轉成雙語訊息）、`pendingDelete.ts`、`storage.ts`、`units.ts`、`files.ts`、`catalog.ts`。
- `src/i18n/dict.ts`：English 與繁體中文（鍵與佔位符一致，有測試檢查）。
- `src/styles/app.css`：設計稿量出的顏色與尺寸都在 `:root` 的 CSS 變數。

## 建置

```bash
npm run build      # tsc 型別檢查 + vite build → web/dist/
npm run licenses   # 檢查相依授權（-- --all 包含開發工具）
```

pdf.js 需要的 cmaps、standard_fonts、wasm 由建置複製到 `dist/pdfjs/`；字型自帶（Barlow、Noto Sans TC，皆 SIL OFL 1.1）。
`dist/` 裡沒有任何外部網址，不從 Google Fonts 或 CDN 載入任何東西。後端給頁面加上 CSP（只允許自己的來源）。

## 測試

```bash
npm run lint       # tsc --noEmit
npm test           # Vitest：單元與元件測試（jsdom）
npm run e2e        # Node 版 Playwright 對真的後端跑（8000 沒有後端就自己啟動一個；前端用 Vite 5174）
E2E_BASE=http://127.0.0.1:8000 npm run e2e    # 改測後端提供的 web/dist（先 npm run build）
E2E_ONLY=Word npm run e2e                      # 只跑名稱含這段字的測試
node e2e/final-shots.mjs http://127.0.0.1:8000 <輸出目錄>   # 主要畫面的截圖（含與設計稿並排）
E2E_BASE=http://127.0.0.1:8000 node e2e/review.mjs          # 照片四角確認、評分、回報、匯出回饋（先 npm run build）
```

`e2e/review.mjs` 用 `e2e/make_photo.py` 把虛構的 `tests/fixtures/doc_a4.pdf` 以 `mortise/eval/synth.py` 拍成手機照片，
再切掉下緣（紙超出畫面），所以②一定會請使用者確認四角。截圖存在 `MORTISE_SHOTS`（預設 `test-results/review-shots`，桌面與手機寬度，2 倍解析度）。

e2e 其他環境變數：`E2E_API`（後端網址）、`MORTISE_SHOTS`（截圖目錄）、`E2E_CONVERT_TIMEOUT`（毫秒）。
後端測試：專案根目錄 `python -m pytest tests/api/ tests/test_output_size.py`。

## 目前有的功能

- **大黑框**：按鈕、File 選單或拖放開啟 PDF／PNG／JPEG，立刻在框內顯示（PDF 由 pdf.js 在瀏覽器裡畫），按 CONVERT 前不上傳。
  翻頁（箭頭、← →、頁碼、縮圖列，縮圖只在捲到時才畫）、縮放（按鈕、滾輪、雙指、拖曳平移、Fit、雙擊）。
  錯誤的檔案（類型、改副檔名、超過大小、加密 PDF、0 頁 PDF）在框內說明。拖檔時框內只顯示放開提示。
- **右邊卡片**：Convert to（後端的格式清單；關起來時顯示短名稱，例如「SVG」）、Origin Size（選填）、Size（原尺寸、預設紙張含直式／橫式、或自訂寬高）。
  兩組寬高都在卡片上即時檢查 12.7–5080 mm（與後端相同），不合格不上傳。
- **轉換中**：整體百分比、7 個步驟（逐頁處理時不倒退）、多頁時「第 n / N 頁 · 步驟」、Cancel（刪除伺服器上的工作；已上傳完才取消也會刪）。
- **完成後**：Original／Result／Compare（可拖的分隔線、鍵盤可操作）／Overlay，還原度、推斷尺寸，Download、Download report（中文檔名可下載）、Convert again。
  預覽不是輸出檔本身畫的時候會說明是誰畫的；沒有分數時說明原因；「SVG 向量圖」找不到任何向量圖時在下載前提醒。
- **修正（Edit）**：文字框可改文字、字型、字級、顏色、對齊，只送改過的欄位，重跑時可以按「先看目前的結果」回到上一版（工作不會被刪），重跑完自動更新。
  低信心或有問題的元素標紅框並說明原因。
- **網址**：畫面上的工作在網址裡（`?job=…`），重新整理或另開分頁會接回進度或結果，卡片照工作的設定填回。
- **Account**：帳號即將推出、English／繁體中文、「用我的檔案改善辨識」（預設關閉）、這個瀏覽器最近的工作（開啟或刪除；多個分頁共用、互相同步）、
  「關於」顯示辨識引擎的名稱與版本（GET /api/engine）和最新成績單的摘要。
- **照片的紙張四角**：照片有頁面需要確認時（GET /pages 的 needs_review），檢視區上方出現淡黃色提示條，說明原因（中文介面用伺服器的句子）。
  點它進入「調整頁面」：未攤平的原圖（kind=source）、目前的四角與四個把手（滑鼠、觸控、方向鍵；Shift 一次 10 倍；拖曳時旁邊有放大鏡）、
  彎曲外框（虛線）、候選晶片（伺服器建議的預先選好）、「整張圖當頁面」、套用（送 corners 修正單，工作從②重跑，完成後回到檢視、提示條消失）、取消（Esc）。
  紙超出畫面時，畫面外的角也看得到、拉得到。不需要確認的照片也能從頁面選單（翻頁列的「⋯」）進入。四角一律用原圖像素（lib/quad.ts 換算）。
- **回饋**：結果卡片有「為第 n 頁評分」（1–5 顆星，點了就送）；修正對話框有「這裡錯了」（勾選問題＋一句說明，送 POST /feedback，標成「這個元素錯了」）；
  頁面選單有「回報這一頁的問題」。送出後顯示「已記下」與這份工作的回饋數；說明修改會被鎖住、這份工作有沒有同意用於改善辨識。
  File 選單「匯出這份工作的回饋」下載 `<檔名>.mortise-feedback.zip`（原檔、回饋、還原度報告、說明；不需要同意）。
- **錯誤**：網路錯誤、伺服器錯誤都有雙語訊息，伺服器原文放在「Server message」裡；網路斷掉不會把已完成的工作標成失敗，Retry 會先問同一份工作，不重傳；
  檔案本身有問題時不顯示沒用的 Retry，改成「選擇其他檔案」。工作在別處被刪除時，畫面回到本機檔案並說明。
- **版面**：寬 900 px 以上卡片在右邊（900–1099 px 用較窄的卡片，CONVERT 留在第一屏），以下卡片在框下方；手機寬度可用。
- **無障礙**：鍵盤焦點都看得到（含 Windows 高對比）、選單 Esc 後焦點回到按鈕、Tab 離開選單會關閉、對話框關閉後焦點回到原處、
  單選群組用方向鍵、進度只在步驟改變時朗讀。

## 還沒做的、已知限制

- 只有文字框能修正；表格（含公式）、圖片、形狀只標示與說明原因，沒有編輯表單（可以用「這裡錯了」回報）。
- 四角編輯在觸控裝置上只用模擬（Playwright 滑鼠、jsdom 觸控事件）測過，沒有在手機實機測。
- Word 的預覽與分數都來自中性渲染，即使裝了 LibreOffice（見上表；已提出約定修改請求）。
- 雙指縮放只用滑鼠滾輪與拖曳測過，沒有在觸控裝置上實測；canvas 面積上限是為 iOS Safari 設的，也沒在 iOS 實機測。
- 從 Account 重新開啟的工作沒有本機檔案，所以沒有 Convert again（沒有「用同一份來源再轉一次」的 API）。
- 加密的 PDF 只會提示先移除密碼，沒有輸入密碼的欄位。
- 伺服器的錯誤與問題說明是中文文字；前端用規則對應到雙語訊息，認不得的就顯示通用訊息加伺服器原文（已請後端改帶錯誤代碼）。
- 沒有 EXIF、內容平坦的 JPEG 截圖會被後端判成照片而改變顏色（①②的問題，不在前端範圍；已回報）。
- 設計稿的字型像 DIN；這裡用 Barlow（OFL）代替，比例略寬。
- `dist/` 約 11 MB，大部分是依字元範圍切開的 Noto Sans TC（只下載用到的部分）與 pdf.js worker。

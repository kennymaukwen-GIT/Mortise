# Mortise 前端（web/）

Vite + React + TypeScript。設計稿：`docs/design/ui-mockup-20261122.png`。後端 API 說明見 `mortise/api/app.py` 開頭。

```bash
npm install
npm run dev      # http://127.0.0.1:5173，/api 轉到 http://127.0.0.1:8000（先啟動後端）
npm run build    # 型別檢查＋建置到 web/dist/；後端看到 web/dist/index.html 就在 "/" 提供前端
npm run lint     # tsc --noEmit
npm test         # Vitest 單元與元件測試
npm run e2e      # Node 版 Playwright 對真的後端跑（沒有後端就自己在 8000 啟動一個）
npm run licenses # 檢查相依套件授權（-- --all 包含開發工具）
```

- 結構：`src/api`（型別與呼叫）、`src/state`（reducer、副作用、衍生計算）、`src/components`、`src/i18n`（English／繁體中文）、`src/lib`、`src/styles/app.css`（設計稿量出的顏色與尺寸都在 `:root` 的 CSS 變數）。
- 選檔後用 pdf.js 在瀏覽器裡畫預覽，按 CONVERT 之前不上傳。pdf.js 需要的 cmaps、standard_fonts、wasm 由建置複製到 `dist/pdfjs/`。
- 字型自帶：Barlow 與 Noto Sans TC（皆 SIL OFL 1.1），不從外部字型服務載入。
- 這個瀏覽器的設定（語言、是否同意用檔案改善辨識、最近的工作）只存在 localStorage。
- e2e 環境變數：`E2E_BASE`（測哪個網址的前端，例如後端提供的 `http://127.0.0.1:8000`）、`E2E_API`、`E2E_ONLY`（只跑名稱含這段字的測試）、`MORTISE_SHOTS`（截圖目錄）。

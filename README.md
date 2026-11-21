# Mortise（榫）

把不可編輯的文件（照片、截圖、掃描 PDF、原生 PDF）還原成可自由編輯、排版一致的檔案（PowerPoint、Word、Excel、給 Illustrator 的 PDF、InDesign IDML、版面模板），並附上還原度報告，逐欄位標出哪些是精確擷取、哪些是量測、哪些是 AI 判斷、哪些是生成。

架構總覽與每個步驟的說明：[docs/architecture/README.md](docs/architecture/README.md)。

## 流水線

```
檔案 → ①輸入 → ②頁面 → ③辨識 → ④結構 → ⑤設定 → ⑥輸出 → ⑦驗證 → 下載
                                                  ↑            │
                                                  └── 修正單 ──┘
```

每個階段只交出自己的產物、不修改上游；回頭修改只有「修正單」一種形式（來源可以是驗證、AI、使用者；使用者的修正不會被之後的重跑覆蓋）。

## 安裝

```bash
pip install -e ".[api,dev]"
```

需要 LibreOffice（`soffice`）才能做⑦的目標驗證；沒有的話流水線照常跑，只是不驗證。

## 使用

命令列：

```bash
mortise run 檔案.pdf -o 輸出目錄 --job 工作目錄
```

```bash
mortise correct 工作目錄 --stage 5 --field font_family --value Calibri --element p001-box-xxxx
```

網頁 API（前端只透過它溝通，端點說明見 `mortise/api/app.py` 開頭）：

```bash
uvicorn mortise.api.app:app --host 0.0.0.0 --port 8000
```

批次評測（放一批照片、截圖、檔案進去，得到分數與報表）：

```bash
python -m mortise.eval run 樣本目錄 --out 報表目錄
```

## 大型 AI

設定檔 `mortise/config/default.toml` 的 `[ai]` 一節。預設用 Claude（`claude-opus-5-5`），只有在有憑證（`ANTHROPIC_API_KEY` 或 `ant auth login`）時才會呼叫；沒有憑證時全部用規則判斷。AI 只回答編號、標籤或在候選中選擇，座標與數值一律由演算法量測。每頁、每份文件都有呼叫次數與花費上限。

## 測試

```bash
pytest
```

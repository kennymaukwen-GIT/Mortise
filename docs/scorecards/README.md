# 成績單

每一份成績單是一個引擎版本（見 [../training.md](../training.md) 第二節）在固定考卷上的分數，檔名就是引擎版本。
`GET /api/engine` 會讀這個目錄，回報和目前引擎版本相同的（沒有就最新的）成績單摘要。

## 目前的基準

- [`mortise-0.1.0+s1v3.s2v5.s3v3.s4v3.s5v6.s6v4.s7v6-cfg02f7ad68-ocrbbb248-uv753d-src801ad2fd.md`](mortise-0.1.0+s1v3.s2v5.s3v3.s4v3.s5v6.s6v4.s7v6-cfg02f7ad68-ocrbbb248-uv753d-src801ad2fd.md)
  （短名「橡鯨-90」，快速版考卷，2026-11-23）。版本字串裡的 `src` 是程式碼指紋：之後只要流水線的程式改了，目前的引擎版本就會和這份不同，
  要重跑一次快速版、把新的成績單放進來（舊的留著，方便比較）。

## 怎麼跑、要多久（4 核、15 GB 的開發機，同時有其他工作在跑；2026-11-23 實測）

| 考卷 | 指令 | 題數 | 時間 |
|---|---|---|---|
| 快速版 | `python -m mortise.eval scorecard --quick --out docs/scorecards` | 10 題（3 份文件；輸出可編輯 PDF、PowerPoint、Word） | 106–107 秒（`--jobs 2`；開始時負載 1.2–1.4） |
| 完整版 | `python -m mortise.eval scorecard --out 成績單` | 32 題（7 份文件；另加 SVG、Excel） | 7 分 29 秒（`--jobs 2`；負載約 3） |

和舊的比：

```bash
python -m mortise.eval scorecard compare docs/scorecards/舊的.json 新的.json   # 退步超過門檻時結束碼是 1
```

## 第一次完整版看到的問題（2026-11-23，引擎 櫸獺-26，交給負責的人看）

- 完整版有 4 題彎曲照片（slide_basic 弓起、cv_two_column 折痕、poster 弓起、table_synth 捲起加折痕）在評測模組
  `mortise/eval/runner.py` 的攤平誤差計算裡出錯（OpenCV `remap` 一次查超過 32767 個點），整題沒有分數；成績單的「錯誤」一節有列出。
- 平的照片：cv_two_column（兩欄履歷）整頁錯字率 0.977、每頁 127 秒；table_synth 0.367。同一批的截圖、掃描都在 0.003 以下。
  單獨重跑 cv_two_column 的照片：②的四角是對的（status ok），但攤平後的工作影像只有 761×1094 像素（A4 約 92 dpi）而且模糊，
  文字辨識只找到 4 行（找到率 0.077），每頁仍要 108 秒。可能是合成照片本身解析度太低，也可能是②的攤平解析度選得太小，需要追查。
- 彎曲照片的四角：偵測器 4 張只對 1 張，但錯的都有請使用者確認（「錯了卻沒提醒」是 0）。

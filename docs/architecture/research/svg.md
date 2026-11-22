# 查證紀錄：SVG 輸出（好編輯、各軟體開得正確）與伺服器端渲染驗證

- 查證日期：2026-11-21
- 適用產品：Mortise（榫）文件反編譯 SaaS，未來可能有桌面版
- 授權底線：所有元件必須可商用；排除 GPL、AGPL；LGPL 可用但有義務（與 image.md 對 `libautotrace` 的處理一致）
- 範圍：整頁 SVG 與向量圖 SVG 的寫法、各軟體開啟 SVG 的行為、把 SVG 畫成影像的元件、PPTX 內嵌 SVG、安全

## 0. 查證方式與限制

| 來源 | 狀況 |
|---|---|
| github.com、raw.githubusercontent.com、gitlab.com、pypi.org、crates.io | 可直接讀取。W3C 規格、MDN、Inkscape、Microsoft Direct2D 文件都改讀放在這些站上的原始檔 |
| helpx.adobe.com、community.adobe.com、support.microsoft.com、learn.microsoft.com、help.figma.com、forum.figma.com、support.apple.com、www.canva.com、www.sketch.com、affinity.help、wiki.inkscape.org、www.w3.org、developer.mozilla.org、resvg-py.readthedocs.io | **無法讀取**（不在工作環境允許的網域內）。這些站的內容只有搜尋摘要；要直讀須先在網路設定放行 |

標註沿用 formats.md：**【直讀】** 親自讀到原文（LICENSE、原始碼、規格原始檔、套件登錄站中繼資料）；**【實測】** 在這台機器的暫存虛擬環境實際跑過（見第 7 節）；**【搜尋摘要】** 只看到搜尋工具轉述；**【第三方】** 論壇、部落格、廠商說法。

根本限制：**這台機器沒有 Illustrator、Inkscape、Figma、Affinity、Canva、PowerPoint、Keynote、Sketch。** 凡是「某軟體打開後會怎樣」的結論都沒有實測；實測只涵蓋 resvg、CairoSVG、Chromium、LibreOffice。

## 1. 總表：把 SVG 畫成影像的元件

| 元件 | 版本（上傳日） | 授權 | 可否商用 | 這台機器 | 準確度 |
|---|---|---|---|---|---|
| **resvg-py**（PyPI，baseplate-admin） | 0.5.0（2026-08-24），內含 resvg 0.48.1 | MIT；內含的 Rust 套件無 GPL、LGPL、MPL | 可 | **可用**，`pip install` 即可，不需系統函式庫 | 實測與 Chromium 幾乎逐像素一致（第 7 節） |
| resvg（Rust 本體） | 0.50.0（2026-11-11） | Apache-2.0 OR MIT（0.45.0 起；0.44.0 以前是 MPL-2.0） | 可 | 經 resvg-py 使用 | 自家測試集 1520／1616（0.45.1 版） |
| Chromium（經 Playwright） | Playwright 1.63.0；Chromium 157.0.8103.78 | Playwright 為 Apache-2.0 | 只當測試時的交叉比對 | **可用**（Node 的 `require('playwright')`）；Python 的 `playwright` **沒有安裝** | 同一測試集 Chrome 123 為 1423／1616 |
| CairoSVG | 2.9.1（2026-09-07） | LGPL-3.0-or-later | 可，有 LGPL 義務 | 可用（靠系統的 libcairo） | **實測遮罩沒有套用**、點陣圖被模糊放大 |
| skia-python | 144.1（2026-10-20） | BSD-3-Clause | 可 | **匯入失敗**：缺 `libEGL.so.1` | 查不到 |
| svglib | 2.4.0（2026-11-04） | LGPL-3.0-or-later | 可，有 LGPL 義務 | 轉點陣圖失敗：缺 `rlPyCairo` | README 自述不支援 `mask` |
| librsvg | 2.63.4（原始碼庫現況） | LGPL-2.1-or-later | 可，有 LGPL 義務 | 系統有 `librsvg-2.so.2`，沒有命令列工具，未測 | 同一測試集 librsvg 2.58.0 為 1168／1616 |
| `resvg`（PyPI，briceyan，另一個綁定） | 0.2.0（2026-04-14），共 4 版 | MIT | 可 | 未測 | 未測 |

## 2. 各軟體開啟或匯入 SVG 的行為

### 2.1 規格與瀏覽器

1. 一個使用者單位等於 1 px；其他絕對單位是 CSS 定義的倍數：1 in = 96 px、1 pt = 1/72 in、1 cm = 96/2.54 px。【直讀】[SVG 2 coords](https://github.com/w3c/svgwg/blob/main/master/coords.bs)、[CSS Values 3](https://github.com/w3c/csswg-drafts/blob/main/css-values-3/Overview.bs)
2. 展示屬性（`fill="…"` 這類）屬於作者層級、特異度為 0，任何 CSS 規則都會蓋過它。檔案裡完全不寫 CSS 時沒有這個問題。【直讀】[SVG 2 styling](https://github.com/w3c/svgwg/blob/main/master/styling.bs)
3. `xlink:href` 在 SVG 2 被列為過時但仍保留；不帶前綴的 `href` 在 `image` 上自 Chrome 50、Firefox 51、Safari 12.1 才支援，`xlink:href` 則三者從最早版本就支援。【直讀】[SVG 2 linking](https://github.com/w3c/svgwg/blob/main/master/linking.bs)、[MDN 相容性資料](https://github.com/mdn/browser-compat-data/blob/main/svg/elements/image.json)
4. `xml:space` 在 SVG 2 被列為新內容不建議使用，但規格仍要求支援。【直讀】[SVG 2 text](https://github.com/w3c/svgwg/blob/main/master/text.bs)
5. SVG 當成影像使用時（`img`、CSS 背景），瀏覽器會停用指令碼、不載入外部資源，只有 `data:` 內嵌的可用。【直讀】[MDN](https://github.com/mdn/content/blob/main/files/en-us/web/svg/guides/svg_as_an_image/index.md)

### 2.2 Inkscape（讀原始碼，主分支為 1.5 開發版）

1. `<g>` 帶 `inkscape:groupmode="layer"` 才是圖層，否則是一般群組。名稱讀 `inkscape:label`；沒有時介面顯示 `#` 加 `id`。【直讀】[sp-item-group.cpp](https://gitlab.com/inkscape/inkscape/-/blob/master/src/object/sp-item-group.cpp)、[sp-object.cpp](https://gitlab.com/inkscape/inkscape/-/blob/master/src/object/sp-object.cpp)
2. 命名空間：`xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"`。【直讀】[inkex](https://gitlab.com/inkscape/extensions/-/blob/master/inkex/elements/_utils.py)
3. 單位換算表：1 in = 96、1 pt = 1.3333、1 mm = 3.7795 個使用者單位。【直讀】[inkex units.py](https://gitlab.com/inkscape/extensions/-/blob/master/inkex/units.py)。0.92 版把預設解析度從 90 dpi 改成 96 dpi。【搜尋摘要】[發行說明](https://wiki.inkscape.org/wiki/Release_notes/0.92)
4. 不帶前綴的 `href` 與 `xlink:href` 對應到同一個屬性，兩種都讀。【直讀】[attributes.cpp](https://gitlab.com/inkscape/inkscape/-/blob/master/src/attributes.cpp)
5. **多行文字的雷：** 帶 `sodipodi:role="line"` 的 `tspan`，其單一的 `x`、`y` 會被丟掉，改由 Inkscape 自己排行；原始碼註解寫明這「可能毀掉匯入檔案的排行」。沒有這個屬性的 `tspan`，`x`、`y` 會被採用。【直讀】[sp-text.cpp](https://gitlab.com/inkscape/inkscape/-/blob/master/src/object/sp-text.cpp)
6. Inkscape 自己建立文字物件時一律寫 `xml:space="preserve"`。【直讀】同上

### 2.3 Illustrator（沒有官方文件；文字部分見 formats.md 3.10）

1. 文字：`tspan` 來回轉換後被拆成獨立文字、不使用 `text-anchor`、不會偵測缺字型。引用 formats.md 3.10 第 2、4、5 點，不重查。
2. `<g id="…">` 匯入後是「圖層 1」底下的群組，不是圖層；發問者靠指令碼事後把群組轉成圖層。Inkscape 的圖層名稱匯入後被丟掉。【搜尋摘要】[Adobe 論壇](https://community.adobe.com/questions-652/how-to-create-g-elements-in-svg-that-are-recognized-as-layers-in-illustrator-817756)、[Inkscape 郵件群組](https://lists.inkscape.org/hyperkitty/list/inkscape-user@lists.inkscape.org/thread/DFF7IA4JCM4XFOXLB4774T7JJ3WIZQOY)
3. Illustrator 匯出時把圖層名稱寫成 `id`，非英數字元轉成十六進位碼（底線變 `_x5F_`、井號變 `_x23_`）；論壇建議改用連字號。**匯入時會不會反向解碼：查不到。** 另有回報說 Illustrator 2020 匯出的圖層長成 `<g id="Layer_2" data-name="Layer 2">`。【搜尋摘要】[Adobe 論壇](https://community.adobe.com/questions-652/layer-names-with-non-alphanumeric-characters-converted-to-hex-code-when-exporting-svgs-811260)、[Inkscape 問題回報](https://gitlab.com/inkscape/extensions/-/issues/175)
4. **72 對 96：** 論壇一致說法是 Illustrator 把 SVG 的 1 px 當 1 pt（每英吋 72）。同一個檔案到 Inkscape、CorelDRAW、PowerPoint（每英吋 96）會變成 75% 大小；反方向則放大。有使用者說 Affinity Designer 匯入時會維持 72 px 等於 1 英吋。【搜尋摘要】[Adobe 論壇](https://community.adobe.com/t5/illustrator-discussions/vectors-scaled-down-75-when-saving-svgs/td-p/13942950/page/2)、[Inkscape 論壇](https://inkscape.org/forums/questions/svgs-import-smaller-then-they-should)
5. 字型名稱：Illustrator 匯出時在 `font-family` 寫 PostScript 名稱（`ArialMT`、`TimesNewRomanPSMT`），瀏覽器對不上而換字型。**匯入時認家族名稱還是 PostScript 名稱：查不到。**【搜尋摘要】[Adobe 論壇](https://community.adobe.com/questions-652/illustrator-exports-svg-with-font-set-to-arialmt-802958)、[Mozilla 問題回報](https://bugzilla.mozilla.org/show_bug.cgi?id=354323)

### 2.4 Figma

1. 外掛介面 `createNodeFromSvg(svg)` 回傳框架節點，文件寫明「等同於編輯器的 SVG 匯入功能」。可用來寫自動化測試。【直讀】[plugin-api.d.ts](https://github.com/figma/plugin-typings/blob/master/plugin-api.d.ts)
2. 沒有找到官方的支援清單。論壇回報（多為舊文，可能已修）：放在 `defs` 裡的 `mask` 匯入後遺失，放在外面正常；用 `defs` 加 `use` 的檔案處理不了；`textPath` 的文字不出現；`tspan` 的 `letter-spacing` 被忽略；文字的 `opacity` 被忽略；`font-size` 用 `em` 時被當成 px；粗斜體匯回來變成別的字重。【搜尋摘要】[遮罩](https://forum.figma.com/t/problem-importing-svgs-with-mask/15254)、[defs 與 use](https://forum.figma.com/t/any-figma-svg-import-compatibility-guidance-are-defs-use-fill-url-mycolor-supported/1996)、[字距](https://forum.figma.com/report-a-problem-6/svg-importiing-ignored-letter-spacing-attribue-40310)、[透明度](https://forum.figma.com/report-a-problem-6/respect-opacity-attribute-on-text-when-importing-svgs-56811)、[em](https://forum.figma.com/report-a-problem-6/figma-svg-import-incorrectly-treats-1-em-font-sizes-as-1-pixel-font-sizes-51503)、[字重](https://forum.figma.com/t/import-text-svg-not-working-propperly/23364)
3. 這些回報談的都是文字圖層的屬性，可推論 `text` 匯入後是文字圖層而非外框；這是推論，沒有官方說法。

### 2.5 Keynote、Canva、Affinity、Sketch（都只有搜尋摘要）

1. **Keynote、Pages、Numbers：** 13.1 版加入 SVG 支援，可「分開」成圖形並存進圖形庫；選單項目變灰代表該圖不能分開；iCloud 版不能分開。第三方說複雜的 SVG 不能分開、分開後的圖形只有單一填色。文字怎麼處理：查不到。【搜尋摘要】[TidBITS](https://tidbits.com/watchlist/pages-13-1-numbers-13-1-and-keynote-13-1/)、[Apple 使用手冊](https://support.apple.com/guide/pages/tan719325126/mac)；【第三方】[MacMost](https://macmost.com/new-in-pages-using-svg-files.html)
2. **Canva：** 英文說明頁寫 SVG 須小於 3 MB。多個語言版本另寫寬度 150 到 200 像素、存成 SVG 1.1。中國版說明頁建議：上傳前把筆畫、路徑、文字方塊都展開；同色圖形放同一群組；避免漸層與材質；Illustrator 存檔時用展示屬性而非 CSS。含文字或內嵌點陣圖的 SVG 會怎樣：查不到。【搜尋摘要】[Canva 說明](https://www.canva.com/help/upload-formats-requirements/)、[中國版](https://www.canva.cn/en/help/upload-formats-requirements-cn/)
3. **Affinity：** 功能頁列有 SVG 匯入與匯出（含 Inkscape 擴充的 SVG）。匯入時 `text`、`tspan`、`text-anchor` 如何處理：查不到。【搜尋摘要】[功能頁](https://affinity.help/designer2/English.lproj/pages/Introduction/keyFeatures.html)
4. **Sketch：** 說明頁列 SVG 為可匯入格式，向量保留路徑可編輯。論壇回報匯入時忽略模糊效果。文字如何處理：查不到。【搜尋摘要】[說明](https://www.sketch.com/docs/other/importing/)、[論壇](https://forum.sketch.com/t/blur-effects-in-svg-files-are-ignored-by-sketch-when-it-opens-them/5368)

## 3. 「最好編輯」的寫法

沒有找到一份各家共同背書的規範。下列每條分開標示「有來源」與「工程判斷」。

1. **樣式用展示屬性，不寫 `<style>` 與 `class`。** 有來源：Canva 說明這樣建議（2.5 第 2 點）；Microsoft Direct2D 的 SVG 渲染器只認元素上的 `style` 屬性與展示屬性，清單裡沒有 `style` 元素。【直讀】[Direct2D](https://github.com/MicrosoftDocs/win32/blob/docs/desktop-src/Direct2D/svg-support.md)；Blender 的匯入器回報忽略以 class 定義的樣式。【第三方】[Blender](https://projects.blender.org/blender/blender-addons/issues/101225)。resvg 與 Chromium 兩種寫法都支援。【實測】
2. **基本圖形用基本元素**（`rect`、`circle`、`ellipse`、`line`、`polygon`），其餘用 `path`。Direct2D 清單全部支援這些元素。沒有找到任何目標軟體讀不了基本元素的回報。各工具匯出時作法不一（Sketch 一律轉成 `path`）。【第三方】[Smashing Magazine](https://www.smashingmagazine.com/2019/05/svg-design-tools-practical-guide/)
3. **變換：** 「避免 `matrix()`」沒有找到來源，屬工程判斷：`rotate(角度 cx cy)` 讓編輯者看得出旋轉角，`matrix` 看不出。變換只放在需要旋轉的元素上，座標其餘一律寫成絕對值，不要層層巢狀。
4. **不用 `use`、`symbol`、巢狀 `svg`。** Figma 有處理不了的回報（2.4）；resvg 與 Chromium 支援【實測】。重複的圖形直接展開。
5. **遮罩與裁切：** 能用 `clipPath` 就不用 `mask`。Direct2D 清單沒有 `mask`；svglib 不支援；CairoSVG 實測沒套用；Figma 有遺失回報。
6. **漸層：** `linearGradient`、`radialGradient` 放在檔頭的 `defs`，`gradientUnits="userSpaceOnUse"`，色標用 `stop-color` 加 `stop-opacity`。Canva 建議避免漸層（2.5）。
7. **色彩：** `#rrggbb` 加獨立的 `fill-opacity`、`stroke-opacity`、`opacity`。不寫 `rgba()` 與八位十六進位。屬工程判斷（Direct2D 清單列有這三個透明度屬性；SVG 1.1 的色彩語法沒有透明度）。
8. **點陣圖：** 用 `data:` 內嵌，屬性寫 `xlink:href` 並宣告 `xmlns:xlink`。Direct2D 只支援 base64 的 `xlink:href`；所有瀏覽器與 Inkscape、resvg 都讀 `xlink:href`（2.1、2.2）。**不要 `href` 與 `xlink:href` 兩個都寫**，圖檔資料會重複一份。
9. **群組與命名：** `id` 只用英文字母開頭、英數與連字號，不用底線（2.3 第 3 點）。給人看的名稱寫在 `inkscape:label`；最上層群組加 `inkscape:groupmode="layer"`。Illustrator 不會把群組變圖層（2.3 第 2 點），無解。
10. **小數位數：** 查不到有來源的共識。工程判斷：座標與長度 2 位，透明度與色標位置 3 位。
11. **根元素的尺寸與單位：** 見 9.2 第 2 點。72 對 96 的落差（2.3 第 4 點）讓這一項沒有已知對所有軟體都正確的寫法。

## 4. 文字

1. **一行一個 `text`，自帶 `x`、`y`。** 理由：SVG 1.1 沒有自動換行；Illustrator 會把 `tspan` 拆散（formats.md 3.10）；Inkscape 對帶 `sodipodi:role="line"` 的多行文字會重排（2.2 第 5 點）。逐段寫成一個 `text` 加多個帶 `x`、`y` 的 `tspan`，在 resvg 與 Chromium 位置一致【實測】，但在編輯軟體裡的行為沒有資料。**不要寫 `sodipodi:role`。**
2. **同一行內樣式不同的片段，用不帶 `x`、`y` 的 `tspan`。** resvg 與 Chromium 排出來一致【實測】。Illustrator 來回轉換會把它拆成獨立文字（formats.md 3.10 第 2 點），這是已知的失真。
3. **一律 `text-anchor="start"`（即不寫），由我方算好每行的起點。** Illustrator 不使用 `text-anchor`（formats.md 3.10 第 4 點）。另外實測：`letter-spacing="10"` 配 `text-anchor="end"` 時，resvg 與 Chromium 的位置差 10 px；配 `middle` 差 5 px（剛好一個、半個字距）。
4. **每個 `text` 寫 `xml:space="preserve"`。** 實測：預設的空白處理下，`tspan` 邊界的空格在 resvg 與 Chromium 收合結果不同（右緣差 7 px，約一個空格）；加了之後兩者一致。Inkscape 自己也這樣寫（2.2 第 6 點）。寫了之後字串裡不能有為了排版而加的換行與縮排。
5. **字型名稱寫家族名稱，粗細與斜體另寫 `font-weight`（數字）與 `font-style`。** 實測 resvg 與 Chromium：家族名稱（含在地化名稱，如 `IPAゴシック`）兩者都對得上；PostScript 名稱（`DejaVuSans-Bold`）與全名（`DejaVu Sans Bold`）兩者都對不上，而且**不報錯、直接換成後備字型**；帶字重的舊式家族名（`Inter SemiBold`）Chromium 對得上、resvg 對不上；`font-family="Inter" font-weight="600"` 兩者都正確。找不到字型時兩者挑的後備字型不同，畫面會不一樣。Illustrator 匯入時認哪一種名稱查不到（2.3 第 5 點）。
6. **字級與位置都用不帶單位的數字。** Figma 把 `em` 當 px（2.4）；Direct2D 不支援 `em`、`ex`；resvg-py 在預設設定下連 `pt` 都會讓文字消失（5.2 第 3 點）。
7. `letter-spacing` 會被部分軟體忽略：Figma 的回報（2.4）；LibreOffice 24.2 實測也沒有套用。字距不為零的行，位置誤差要算進驗證的容許值，或改走外框。
8. `dominant-baseline="hanging"` 在 resvg 與 Chromium 一致【實測】，其他軟體沒有資料。保守做法是只用預設基線，`y` 直接給基線位置。
9. **必須提供「文字轉外框」選項。** 理由：Canva 的說明直接建議上傳前把文字展開（2.5）；Illustrator 與 Figma 的文字屬性各有遺失回報；缺字型時各軟體都是靜默替換。實作不需要新元件：專案已相依的 uharfbuzz 做排字、fontTools 的 `SVGPathPen` 加 `TransformPen` 輸出路徑。實測（uharfbuzz 0.57.0、fontTools 4.68.0）：外框與 resvg 畫的同一段文字相比，差異超過 64 階的像素為 0.000%（拉丁字含字距微調，以及繁體中文各一例）。

## 5. 伺服器端渲染元件：逐條事實

### 5.1 resvg

1. 授權：`Apache-2.0 OR MIT`。0.45.0（2025-02-26）起由 Linebender 接手並從 MPL-2.0 改授權；crates.io 上 0.44.0 仍標 MPL-2.0。最新 0.50.0（2026-11-11），主分支最後提交 2026-11-17。【直讀】[README](https://github.com/linebender/resvg/blob/main/README.md)、[CHANGELOG](https://github.com/linebender/resvg/blob/main/CHANGELOG.md)、[crates.io](https://crates.io/crates/resvg)
2. 只支援靜態子集：沒有 `a`、`script`、`view`、`cursor`，沒有事件與動畫。不用系統函式庫，同一檔案在不同平台逐像素相同。點陣化用 tiny-skia（BSD-3-Clause）。【直讀】README
3. 不支援的 SVG 1.1 功能：SVG 字型相關元素、`color-profile`、參照外部 SVG 檔的 `use`；屬性有 `direction`、`unicode-bidi`、`font-stretch`、`font-size-adjust` 等。【直讀】[unsupported.md](https://github.com/linebender/resvg/blob/main/docs/unsupported.md)
4. 自家測試集（1616 項）通過數：resvg 0.45.1 為 1520、Chrome 123 為 1423、Firefox 124 為 1385、Safari 17.3.1 為 1341、librsvg 2.58.0 為 1168、Inkscape 1.3.2 為 985、Batik 1.17 為 976。這是 resvg 自己出題自己評分。【直讀】[chart.svg](https://github.com/linebender/resvg/blob/main/.github/chart.svg)

### 5.2 resvg-py

1. 授權 MIT（輪檔內的 LICENSE 與 Cargo.toml）。PyPI 的授權欄位是空的，要以 LICENSE 檔為準。輪檔附軟體物料清單，74 個 Rust 套件，授權只有 MIT、Apache-2.0、BSD、Zlib、Unlicense、0BSD、Unicode-3.0。【直讀】[PyPI](https://pypi.org/project/resvg-py/)、[LICENSE](https://github.com/baseplate-admin/resvg-py/blob/master/LICENSE)
2. 維護：2024-04 首版，共 21 版，最新 0.5.0（2026-08-24）；主分支最後提交 2026-11-10，已改用 resvg 0.49。輪檔涵蓋 Linux（x86_64、aarch64，glibc 與 musl）、macOS、Windows，Python 3.10 以上。單一作者。【直讀】PyPI、儲存庫
3. **雷：`dpi` 預設值 0.0 會原樣傳給 resvg。** 實測：根元素 `width="100pt"`（或 `in`、`mm`、`cm`）丟出 `ValueError: SVG has an invalid size`；`font-size="24pt"` 的文字整段消失且不報錯。傳 `dpi=96.0` 後正常（100pt 得 133 px）。【直讀加實測】[lib.rs](https://github.com/baseplate-admin/resvg-py/blob/master/src/rust/lib.rs)
4. 介面只有一個函式，回傳 PNG 位元組，錯誤丟 `ValueError`：

```python
import resvg_py
png = resvg_py.svg_to_bytes(
    svg_string=svg,            # 或 svg_path=
    zoom=2.0,                  # 或 width=、height=
    dpi=96.0,                  # 一定要給，見上一點
    background="#ffffff",      # 不給則透明
    skip_system_fonts=True, font_dirs=[字型目錄],   # 或 font_files=[…]
    serif_family="後備字型家族名稱",                # 見下一點
    log_information=True)      # 警告印到標準輸出
```

5. 字型【實測】：`font-family` 找不到時，後備是 `serif_family` 指定的家族（不是 `font_family`，那個只在元素沒寫 `font-family` 時用）；`font-family` 寫成清單時會依序嘗試；單一字型缺字時會從已載入的其他字型補。**只載入自備字型而沒設 `serif_family` 時，找不到家族的文字整段不畫，不丟例外**，只有開 `log_information` 才印 `No match for … font-family`。有後備可用時完全沒有訊息。
6. 速度【實測】：800 × 600 的測試圖放大兩倍，每次約 0.2 秒；只有一行文字的小圖約 5 毫秒。

### 5.3 其他元件

1. **CairoSVG** 2.9.1：LGPL-3.0-or-later；相依 cairocffi（BSD）與系統的 libcairo。實測同一張圖：`maskUnits="userSpaceOnUse"` 的遮罩沒有套用，內嵌點陣圖放大時被模糊且溢出邊界；與 Chromium 相比差異像素 2.88%（resvg 為 0.30%）。不適合當驗證用的渲染器。【直讀加實測】[LICENSE](https://github.com/Kozea/CairoSVG/blob/main/LICENSE)、[PyPI](https://pypi.org/project/CairoSVG/)
2. **skia-python** 144.1：BSD-3-Clause。README 寫明 Linux 上必須有 OpenGL、libEGL、fontconfig。這台機器缺 `libEGL.so.1`，`import skia` 直接失敗，沒有測到它的 SVG 模組。【直讀加實測】[README](https://github.com/skia-python/skia-python/blob/main/README.md)、[LICENSE](https://github.com/skia-python/skia-python/blob/main/LICENSE)
3. **svglib** 2.4.0：LGPL-3.0-or-later；先轉成 ReportLab 圖形再輸出。README 的已知限制：不支援 `mask`、`foreignObject`，每個 `clipPath` 只認一種 `clip-rule`，CSS 支援有限。【直讀】[README](https://github.com/deeplook/svglib/blob/main/README.md)
4. **librsvg** 2.63.4：LGPL-2.1-or-later。忽略動畫、指令碼、事件；會自行處理外部檔案參照，依位置規則決定接受與否。Python 端的官方輪檔：沒有查。【直讀】[COPYING.LIB](https://github.com/GNOME/librsvg/blob/main/COPYING.LIB)、[security.rst](https://github.com/GNOME/librsvg/blob/main/devel-docs/security.rst)
5. **Chromium 交叉比對的用法**（Node，從專案目錄執行）：`chromium.launch()` 後開 `newContext({viewport, deviceScaleFactor: 2, javaScriptEnabled: false})`，`page.goto('file://…svg')` 再 `page.screenshot()`。套件在 `/opt/node-tools/node_modules`，瀏覽器在 `/opt/pw-browsers`；`/usr/bin/google-chrome` 是另一份較舊的 Chrome for Testing 153。【實測】

## 6. PowerPoint 內嵌 SVG

1. **寫法：** 圖片的 `a:blip` 用 `r:embed` 指向 PNG 後備圖，底下加擴充清單指向 SVG。【直讀】[PptxGenJS gen-xml.ts](https://github.com/gitbrent/PptxGenJS/blob/master/src/gen-xml.ts)

```xml
<a:blip r:embed="rId2">  <!-- PNG 後備 -->
  <a:extLst><a:ext uri="{96DAC541-7B7A-43D3-8B79-37D633B846F1}">
    <asvg:svgBlip xmlns:asvg="http://schemas.microsoft.com/office/drawing/2016/SVG/main" r:embed="rId3"/>
  </a:ext></a:extLst>
</a:blip>
```

2. 一張 SVG 佔兩個關聯編號（PNG 與 SVG 各一），類型都是一般的圖片關聯（`…/relationships/image`）；`[Content_Types].xml` 要有 `image/svg+xml`（PptxGenJS 寫成 `<Default Extension="svg" …/>`）。【直讀】[gen-objects.ts](https://github.com/gitbrent/PptxGenJS/blob/master/src/gen-objects.ts)、同上的 gen-xml.ts
3. Microsoft 的 Open XML SDK 把 `asvg:svgBlip` 定義為 `SVGBlip` 類別，標示 Office 2019 起，屬性為 `r:embed` 與 `r:link`。【直讀】[SDK 結構資料](https://github.com/dotnet/Open-XML-SDK/blob/main/data/schemas/schemas_microsoft_com_office_drawing_2016_SVG_main.json)
4. **python-pptx 不支援**（formats.md 3.1 第 14 點；原始碼只有一句「略過不支援的影像類型，例如 SVG」）。【直讀】[package.py](https://github.com/scanny/python-pptx/blob/master/src/pptx/package.py)。**但可以自己補**：先 `add_picture` 放 PNG，再用 `pptx.opc.package.Part` 建一個內容類型為 `image/svg+xml` 的組件、`slide.part.relate_to(part, RT.IMAGE)` 取得關聯編號，最後用 lxml 在 `a:blip` 下補上面那段。實測檔案寫得出來、python-pptx 讀得回來；**LibreOffice 24.2.7.2 開啟時畫的是 SVG 而不是後備圖**（把後備圖做成全紅來分辨）。沒有用真的 PowerPoint 驗證。【實測】
5. 支援版本：Microsoft 的說明寫 Microsoft 365 的 Word、PowerPoint、Outlook、Excel（Windows、Mac、Android）可插入與編輯 SVG；另一篇把 Office 2019、2021、2024 與 Microsoft 365 列為支援、Office 2016 不支援。不支援的版本顯示後備圖。【搜尋摘要】[Microsoft 支援](https://support.microsoft.com/en-us/topic/69f29d39-194a-4072-8c35-dbe5e7ea528c)、[EPS 停用說明](https://support.microsoft.com/office/a069d664-4bcf-415e-a1b5-cbb0c334a840)；【第三方】[Telerik](https://www.telerik.com/products/reporting/documentation/styling/rendering-and-paging/design-considerations-for-report-rendering/powerpoint-rendering-design-considerations)
6. **「轉換成圖形」：** 把 SVG 變成一組 Office 圖形，轉換前有警告；Microsoft 的說明寫要個別改色必須先轉換，且目前要在 Windows 版 Microsoft 365 才能做。**沒有官方的失真清單。** 第三方說法：文字標籤、漸層、裁切路徑可能遺失；隱藏的元素會重新出現；結果因檔案而異。文字轉換後是文字方塊還是外框：查不到。【搜尋摘要】同上；【第三方】[Ipe 教學](https://algo.win.tue.nl/tutorials/ipe-to-ppt)、[Office Watch](https://office-watch.com/2021/editing-icon-svg-trick-coming-office/)
7. 旁證：Direct2D 的 SVG 渲染器支援清單裡沒有 `text`、`mask`、`filter`、`pattern`、`symbol`、`style` 元素；長度不支援 `em`、`ex`。**PowerPoint 是否使用這個渲染器：查不到**，只能當保守子集的參考。【直讀】[Direct2D](https://github.com/MicrosoftDocs/win32/blob/docs/desktop-src/Direct2D/svg-support.md)
8. 設計含意：本產品的 PPTX 主路徑是原生圖形（06-export.md），svgBlip 只適合「不值得拆成原生圖形的複雜向量圖」；放進去的 SVG 應該不含 `text`（文字另外用原生文字方塊，或先轉外框）。

## 7. 實測紀錄

環境：Python 3.13.18 暫存虛擬環境；resvg_py 0.5.0、CairoSVG 2.9.1、skia-python 144.1、svglib 2.4.0、python-pptx 1.0.2；Node 22 的 Playwright 1.63.0 加 Chromium 157；LibreOffice 24.2.7.2；系統字型 119 個字面（DejaVu、Liberation、Inter、文泉驛正黑、IPA Gothic 等）。測試圖 800 × 600，含線性與放射漸層、貝茲路徑、`clipPath`、`mask`、虛線、旋轉群組、內嵌 PNG、五段文字（含 `tspan` 變色加粗、繁體中文、置中、靠右加字距、三行 `tspan`）。程式與輸出都在工作階段暫存目錄，沒有寫進專案。

| 測試 | 結果 |
|---|---|
| resvg-py 畫測試圖 | 成功；與 Chromium 相比差異超過 32 階的像素 0.30%（圖形區 0.06%、文字區 0.96%）；差異像素有 72% 集中在靠右加字距那一行 |
| CairoSVG 畫測試圖 | 成功但遮罩沒套用、點陣圖模糊；與 Chromium 差異 2.88% |
| skia-python | `ImportError: libEGL.so.1` |
| svglib 轉 PNG | `RenderPMError`，缺 `rlPyCairo` |
| resvg-py：`script`、`onload`、`foreignObject` | 都被忽略，不報錯 |
| resvg-py：`image` 的 `href` | 絕對路徑的本機檔案**會被讀進來畫出**；`file://` 與 `https://` 不會 |
| resvg-py：`class` 加 `<style>`、`use` 加 `symbol`、巢狀 `svg` | 都正確畫出 |
| resvg-py：只有 `viewBox` 沒有寬高 | 輸出尺寸等於 `viewBox` |
| 文字的字型比對、`dpi`、空白、`text-anchor` | 見 4、5.2 各點 |
| 文字轉外框 | 見第 4 節第 9 點 |
| PPTX 內嵌 svgBlip | 見第 6 節第 4 點 |

## 8. 安全

1. SVG 2 定義「安全靜態模式」：不執行指令碼、不解析外部參照、無動畫、無互動；當成影像嵌入時應以此模式處理。我方輸出應該在這個模式下內容完全不變。【直讀】[SVG 2 conform](https://github.com/w3c/svgwg/blob/main/master/conform.bs)
2. **輸出時不得出現：** `script`；任何 `on…` 事件屬性；`foreignObject`；`a`；動畫元素；指向外部的 `href`、`xlink:href`、`url(…)`（只允許 `#id` 與 `data:image/png`、`data:image/jpeg`）；`<!DOCTYPE>` 與實體宣告；`<style>` 與 `@import`；XInclude。來源文件的文字一律經 XML 跳脫後寫入，並濾掉 XML 不允許的控制字元；`id` 由我方產生，不直接採用來源字串。
3. 渲染端：resvg 會讀取 SVG 裡以路徑指到的本機影像【實測】；resvg-py 解析 XML 時開啟了 DTD（`allow_dtd: true`）【直讀】。librsvg 曾有透過 XInclude 讀取任意檔案的漏洞（CVE-2023-38633）【直讀】[security.rst](https://github.com/GNOME/librsvg/blob/main/devel-docs/security.rst)。CairoSVG 另有 `unsafe` 參數，開了才允許外部檔案與 XML 實體【直讀】套件原始碼。結論：**驗證時只渲染我方自己產生的 SVG**；本產品不接受 SVG 輸入（01-input.md），這條界線要維持。
4. Chromium 交叉比對時關掉 JavaScript（`javaScriptEnabled: false`），且只在測試環境使用。

## 9. 給實作的建議

### 9.1 明確做法

1. **渲染驗證用 resvg-py**，寫在 `mortise/verify/svg_target.py`。固定參數：`dpi=96.0`、`skip_system_fonts=True`、`font_dirs` 指向伺服器的字型目錄、`serif_family` 與 `sans_serif_family` 都設成伺服器上確實有的家族（前者是找不到字型時的後備，後者對應 `font-family` 清單尾端的 `sans-serif`）、`background="#ffffff"`。版本釘住（`resvg-py==0.5.0`），升版時重跑比對，因為內含的 resvg 版本會跟著變。
2. **字型替換要自己擋。** resvg 找不到字型時不報錯。寫出前，用 fontTools 讀字型目錄建立「家族名稱加字重加斜體」的清單，逐一核對 SVG 裡用到的組合；對不上就記進驗證報告，不要等渲染結果。
3. **Chromium 只當測試時的第二意見**（Node 的 Playwright）。正式流程不依賴它；Python 的 `playwright` 沒裝，要用得另外安裝。不採用 CairoSVG、svglib（LGPL 且準確度不足）與 skia-python（要額外的系統函式庫）。
4. **寫出器的固定寫法**（`mortise/exporters/svg.py`）：
   - 根元素：`xmlns`、`xmlns:xlink`、`xmlns:inkscape`、`viewBox`、`width`、`height`；全檔不寫 `<style>`、`class`、`style` 屬性。
   - `VectorPrimitive` 有值時寫基本元素，旋轉寫 `transform="rotate(角度 cx cy)"`；其餘寫 `path`，用絕對座標指令。
   - 漸層放 `defs`、`userSpaceOnUse`；色彩 `#rrggbb` 加獨立透明度；座標 2 位小數。
   - 點陣圖用 `xlink:href="data:…"`；不用 `use`、`symbol`、巢狀 `svg`、`mask`（必要時改 `clipPath` 或點陣化）。
   - 最上層每個群組：`id`（英數與連字號）、`inkscape:label`、`inkscape:groupmode="layer"`。
   - 文字：一行一個 `text`，不帶單位的 `x`、`y`、`font-size`，`xml:space="preserve"`，不寫 `text-anchor`；`font-family` 寫家族名稱並在後面接通用後備（例：`"Noto Sans TC", sans-serif`），`font-weight` 寫數字；行內樣式片段用不帶座標的 `tspan`。
5. **提供「文字轉外框」選項**，用 uharfbuzz 加 fontTools 產生路徑。整頁 SVG 預設保留文字；`SVG_ASSETS`（單張向量圖）含文字時預設要不要轉外框由產品決定（要丟進 Canva、PowerPoint 的話外框較穩，屬工程判斷）。放進 PPTX 的 svgBlip 一律不含 `text`。
6. **寫出後做一次安全檢查**：用 lxml 重新解析輸出檔，確認沒有第 8 節第 2 點列的任何元素與屬性，當成單元測試與寫出器的最後一步。
7. **PPTX 的 svgBlip** 照第 6 節第 4 點實作，後備 PNG 由 resvg-py 產生（同一份 SVG、兩倍放大）。注意用 LibreOffice 當替身引擎驗證時，它畫的是 SVG 而不是後備圖。

### 9.2 待驗證的假設（都需要真的軟體）

1. **Illustrator 匯入時認家族名稱。** 若實際上要 PostScript 名稱，就得在 `font-family` 清單裡兩種都列（家族名稱在前）。
2. **根元素尺寸。** 預設寫不帶單位的 `width`、`height`，數值等於 `viewBox`，一個單位當 1 pt。這樣 Illustrator 的實體尺寸正確（它把 px 當 pt），瀏覽器、Inkscape、PowerPoint 會是 75% 大小但幾何不變。替代寫法是 `width="595.28pt"` 這類帶單位的值，理論上兩邊實體尺寸都對，但 Illustrator、Figma、Canva 是否正確解讀沒有資料。做成設定項，測過再定預設。
3. 一行一個 `text` 在 Illustrator、Figma、Affinity、Sketch 匯入後是可編輯的文字物件，位置不變。
4. `xml:space="preserve"` 與 `inkscape:` 開頭的屬性不會讓 Illustrator、Figma、Canva、PowerPoint 拒絕開檔或出現警告。
5. PowerPoint 接受我方寫的 svgBlip（關聯、內容類型、擴充清單的順序），「轉換成圖形」後漸層與裁切保留的程度。
6. Figma 可用外掛的 `createNodeFromSvg` 自動化驗證；其餘軟體只能人工開檔，建議固定一份測試圖（第 7 節那張可直接沿用）做成檢查表。

## 10. 查不到的事

1. Illustrator、Affinity、Sketch、Canva、Keynote 匯入 SVG 時對 `text`、`tspan`、字型名稱的官方說明；Illustrator 匯入時是否解碼 `_x5F_` 這類 `id`。
2. Figma 的官方 SVG 支援清單；論壇回報的問題哪些已修。
3. PowerPoint「轉換成圖形」的官方失真清單，文字轉換後的型態，以及它用哪個 SVG 渲染器。
4. 「最好編輯的 SVG」的跨廠商共識文件；小數位數與 `matrix` 的說法都找不到有根據的出處。
5. skia-python 的 SVG 模組準確度（這台機器跑不起來）；librsvg 的 Python 輪檔來源與授權。
6. Google 簡報、Keynote 開啟含 svgBlip 的 PPTX 時顯示哪一張圖。

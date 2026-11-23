"""③辨識．影像產線：非文字區域的分割、分類與形狀擬合（設計文件 3B.7、3B.10、3B.11，及 3B.12 的裁切）。

採「剝洋蔥」順序：文字已經先處理掉（呼叫端交來 text_mask），這裡處理剩下的像素。
本模組只做量測與感知，不呼叫任何大型語言模型；分類只交候選與分數，不替⑤定案。

做法（每一步用的演算法）
------------------------
1. 文字視為未知：text_mask 內的像素以「最近的非文字像素」填平（cv2.distanceTransformWithLabels
   的最近鄰標籤）。只為了分割與分類，不是最終修補；擬合時文字旁的輪廓點一律不採用。
2. 雜訊估計：Immerkær 拉普拉斯核的中位數絕對值，逐格計算，取最平坦的四分位當頁面雜訊 σ。
3. 背景模型（estimate_background）：影像切成格子，算每格的中位色與 MAD；平坦格子之間色差小者
   連成圖（scipy 稀疏圖連通元件），取「面積＋碰邊加權」最大的一片當背景種子。
   先試純色（95 百分位殘差夠小），不行就以加權最小平方擬合二次曲面漸層（每個色版 6 項），
   反覆剔除離群格並重新納入其他符合模型的平坦格。殘差仍大則記為 complex（例如整頁是照片）。
4. 前景：高斯平滑後與背景模型的 RGB 歐氏距離，超過 max(絕對下限, k·平滑後雜訊, k·模型殘差)。
5. 有框線表格（3B.10）：前景做水平、垂直長條形態學開運算取細長線段，合併共線斷線，
   互相交叉的線段組成網；至少兩橫兩直且至少兩格才算表格，線段像素先被表格認領。
6. 平坦區塊：高斯平滑後的 Sobel 梯度低於門檻（隨雜訊調整）的前景像素做 4 連通標記；
   每塊以「常數＋線性漸層」擬合顏色，離散度小者為平坦區塊（漸層填色算一群）。
7. 邊緣過渡帶與遮罩精修：每個平坦區塊外緣一圈，以「區塊色 F 與外側色 O 的混色」解出覆蓋率
   α = (I−O)·(F−O)/|F−O|²，α ≥ 0.5 併入遮罩（反鋸齒、模糊都能還原到半覆蓋位置）；
   混色殘差小的像素記為「已解釋」，不再當紋理。
8. 紋理區塊：前景扣掉平坦區塊與已解釋的過渡帶，剩下的做 8 連通標記（小閉運算），面積過小丟棄。
9. 分類（門檻都在 DEFAULT_PARAMS）：
   - 細長（最小外接矩形短邊 ≤ 線寬上限、長寬比夠大）→ 形狀．直線。
   - 環狀（有大洞、厚度薄）→ 有外框線的形狀；洞內若是另一個平坦區塊，該區塊就是填色。
   - 紋理區塊以三個特徵計分：有效色彩數（兩組錯位量化格取小者，涵蓋 95 % 像素所需的格數）、
     平坦比（梯度低於門檻的比例）、高頻比（區內拉普拉斯雜訊 ÷ 頁面雜訊）。高分為照片，低分為插畫。
   - 被照片包圍的平坦區塊：先判斷是不是「後來疊上去的平塗形狀」（_OverlayContext：面積、內部顆粒 ÷ 照片最平坦處的顆粒、
     內部顆粒與頁面底色比、顏色均勻、邊緣銳利而且連續），是的話照常擬合、圖層在照片之上；擬合不成基本幾何就試描圖，
     過關才拆成插畫；其餘併回照片（照片內容）。照片的外形另做擬合。
   - 照片紋理與形狀顏色相近時，平滑區會穿過形狀邊界連成一片（整片就不平坦）：用「顆粒幾乎為零」把乾淨的大塊挖出來，
     只收被前景整個圍住的（_clean_parts）。
   - 平坦區塊做幾何擬合；擬合不好的（不是簡單幾何）與插畫型紋理就近合併成插畫。
   - 陰影：緊貼其他區域、比背景暗、對比低、很薄的區塊不成為區域，記在鄰居的 notes。
10. 幾何擬合（3B.11）：輪廓點（像素中心）以 scipy least_squares（soft-L1 穩健損失）擬合
    圓角矩形的有號距離函數（矩形是半徑 0 的特例）與橢圓的 Sampson 距離；多邊形用 Douglas–Peucker。
    先選「殘差在容許內的最簡單模型」：直線 → 矩形 → 橢圓 → 圓角矩形 → 多邊形；都不行就不是形狀。
    文字與其他區域旁的輪廓點視為被遮擋，不參與擬合（所以被字蓋住的色塊仍認得完整）。
    參數誤差由 Jacobian 共變異數估計，寫入 ShapeFit.estimates。像素中心輪廓再向外補 0.5 px。
11. 填色取內縮後區域的中位數（避開邊緣與文字）；外框線分開量線色與線寬（面積 ÷ 平均周長）。
12. 圖層：每對區域在「補全後遮罩」的重疊處，看得到誰的像素誰就在上；巢狀時內層自然在上。
    以拓撲排序給 z，無關係者面積大的在下。
13. 無框線表格：由 text_quads 分列、找跨列貫通的空白帶（欄間河道），至少兩欄三列，信心值較低；
    儲存格幾乎填滿整欄（分欄長文）者不算。
附加的清理步驟：相鄰同色平坦區塊合併（JPEG 區塊邊界會切開色塊）；外框線碎段（平坦段＋轉角紋理）
連成環狀就併成一個候選；緊貼色塊、顏色接近色塊或背景的薄紋理視為邊緣殘影（JPEG 振鈴、模糊殘邊）；
像素其實是背景色的候選（平滑造成的殘影）丟棄；緊貼文字遮罩的小塊視為文字殘渣丟棄；
表格內的小塊記在表格 notes（可能是漏抓的文字）。
除錯／調參：params["_debug"] 若給一個 list，會附上背景模型、門檻與每個候選的角色與特徵。
14. crop_region：裁切成 RGBA；有外形擬合者以 4× 超取樣畫出反鋸齒的透明度，否則以遮罩羽化。
15. fill_covered：照片被上層元素蓋住的地方補回來（背景補全＋薄膜內插＋複製照片自己的顆粒），回傳生成遮罩。
多邊形：Douglas–Peucker 的頂點被接受後再精修（每邊直線擬合取交點，去掉共線頂點與太短的邊；反鋸齒削鈍的尖角因此還原）。

座標：工作影像像素，原點左上、y 向下；像素 i 佔 [i, i+1)，中心在 i+0.5。
quad 一律為「左上、右上、右下、左下」（以形狀自身方向為準）；有外框線的形狀，quad 是線的中心線
（與 PDF／PPTX 的路徑定義一致），沒有外框線時 quad 是看得到的外緣。

已知限制
--------
- 半透明色塊壓在照片上：一律當照片內容，不拆出來。照片上的不透明平塗形狀會拆（見第 9 步），但照片本身很乾淨
  （顆粒 < overlay_min_texture）時比不出來，就留在照片裡；照片裡過曝或欠曝成純白、純黑的大塊，若邊緣銳利、形狀規則，可能被誤拆。
- 與背景同色、疊在其他色塊上的形狀（藍卡片上的白框）看起來是洞，不會成為區域。
- 純色加簡單幾何組成的「圖片」會被讀成形狀（例如 slide_basic 的藍底黃橢圓）；類型歧義交⑤。
- 由基本幾何拼成的插畫會被拆成多個形狀；只有擬合失敗的色塊才合併成插畫。
- 漸層填色只回報中位色與「有漸層」的註記，還沒有擬合漸層參數。
- 有外框線的橢圓，線寬約低估 0.5 px；模糊下外框線色會被稀釋（照實回報，不校正）。
- 完全被照片包住、但沒通過疊加判斷的平坦色塊會併回照片（沒拆的原因記在照片 notes）。碰到照片外緣的形狀不走這個判斷。
- 照片與背景同色的邊界（白底商品圖放白底上）找不準；外形擬合不好時退回原始遮罩。
- 同色相接的兩個形狀會連成一塊，擬合失敗後成為插畫（尚未做凹角切分）。
- 虛線、箭頭端點、雙線格線、漸層以外的特效（光澤、紋理填色）未特別處理。
- 次像素細線（< 2 px）的線寬與顏色無法分離，回報大誤差、低信心。
- 背景若整頁都是照片（找不到平坦背景），前景判斷不可靠，會在 notes 註明。
- 影像很大時沒有降採樣，時間約與像素數成正比。

可單獨升級的點
--------------
- 【替換點 A】候選分割：_segment_candidates 可改接版面模型（PP-DocLayoutV3）或 SAM 2.1 的遮罩當候選。
- 【替換點 B】照片／插畫分類：_classify_texture 可換成圖片類型分類器（DocumentFigureClassifier）。
- 【替換點 C】任意外形描圖：擬合失敗的形狀可交 VTracer（MIT）描成路徑，目前直接改判插畫。
- 【替換點 D】表格偵測：_detect_ruled_tables 可加表格偵測模型交叉驗證。
- 背景模型可加入放射漸層、分段平滑；遮擋關係可加入邊界連續性證據。
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np
from scipy import optimize
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from .types import RGB, Estimate, Region, ShapeFit, TableGrid

__all__ = [
    "DEFAULT_PARAMS",
    "BackgroundModel",
    "estimate_background",
    "find_regions",
    "crop_region",
    "fit_shape",
    "fill_covered",
    "fill_quality",
    "isolate_overlay",
]

DEFAULT_PARAMS: dict = {
    # ---------------- 背景模型 ----------------
    "bg_tile_div": 40,  # 背景估計的格子數：短邊切成幾格（格）
    "bg_tile_min_px": 8,  # 格子最小邊長（像素）
    "bg_tile_valid_frac": 0.5,  # 格子內非文字像素至少佔多少才採用（比例 0–1）
    "bg_tile_flat_abs": 3.0,  # 平坦格子的色彩 MAD 上限的下限（色階 0–255）
    "bg_tile_flat_k": 3.0,  # 平坦格子的色彩 MAD 上限＝k·頁面雜訊 σ（倍）
    "bg_adj_tol": 6.0,  # 相鄰平坦格子視為同一片背景的中位色差上限（RGB 歐氏距離，色階）
    "bg_border_bonus": 1.0,  # 碰到影像邊緣的格子在面積計分中額外加權（倍）
    "bg_flat_tol": 4.0,  # 背景可用單一顏色描述的 95 百分位殘差上限（色階）
    "bg_refit_tol": 8.0,  # 漸層擬合時納入背景格子的殘差上限（色階）
    "bg_min_coverage": 0.05,  # 背景格子至少佔全部格子的比例，否則背景視為不可觀測（比例）
    # ---------------- 前景 ----------------
    "fg_smooth_sigma": 1.0,  # 與背景取差之前的高斯平滑 σ（像素）
    "fg_min_diff": 14.0,  # 前景門檻的絕對下限（RGB 歐氏距離，色階）
    "fg_noise_k": 4.0,  # 前景門檻＝k·平滑後雜訊（倍）
    "fg_model_k": 2.0,  # 前景門檻＝k·背景模型殘差（倍）
    "min_area_rel": 1.5e-4,  # 區域最小面積（佔影像面積的比例）；更小的交給小圖示／雜點處理
    # ---------------- 平坦區塊與邊緣 ----------------
    "grad_sigma": 1.0,  # 計算梯度前的高斯平滑 σ（像素）
    "grad_abs": 5.0,  # 「平坦」的梯度上限的下限（色階／像素）
    "grad_noise_k": 1.0,  # 梯度上限＝k·頁面雜訊 σ（倍；平滑後的導數雜訊約 0.2σ）
    "flat_color_tol": 10.0,  # 平坦區塊扣掉線性漸層後的色彩離散上限（1.4826·中位距離，色階）
    "flat_noise_k": 4.0,  # 平坦區塊色彩離散上限＝k·頁面雜訊 σ（倍），與上一項取大者
    "flat_hf_abs": 2.0,  # 平坦區塊內的拉普拉斯雜訊 σ 上限的下限（色階）；超過表示有紋理
    "flat_hf_k": 2.5,  # 平坦區塊內雜訊 σ 上限＝k·頁面雜訊 σ（倍），與上一項取大者
    "edge_band_px": 3,  # 平坦區塊外緣過渡帶的半寬（像素）；混色分析範圍為其兩倍
    "mix_tol": 14.0,  # 過渡帶像素可被「區塊色與外側色混色」解釋的殘差上限（色階）
    "mix_noise_k": 3.0,  # 混色殘差上限＝k·頁面雜訊 σ（倍），與上一項取大者
    "merge_color_tol": 20.0,  # 相鄰平坦區塊中位色差小於此值就合併（色階；另與 3σ 取大者）
    "contrast_min": 12.0,  # 區塊色與外側色差小於此值時不解覆蓋率（色階）
    "ringing_max": 18.0,  # 邊緣外側「沿混色方向超出」仍算可解釋的上限（JPEG 振鈴、銳化光暈；色階）
    "ringing_frac": 0.25,  # 緊貼平坦區塊的薄紋理，若顏色離區塊色或背景色不到此比例的對比，視為邊緣殘影（比例）
    # ---------------- 分類 ----------------
    "color_bin": 24.0,  # 有效色彩數的量化格寬（色階）；實際取 max(此值, 3σ)
    "color_cover": 0.95,  # 有效色彩數：涵蓋多少比例像素所需的格數（比例）
    "photo_colors_lo": 8.0,  # 有效色彩數低於此值→像插畫（格）
    "photo_colors_hi": 24.0,  # 有效色彩數高於此值→像照片（格）
    "photo_flat_lo": 0.35,  # 平坦比低於此值→像照片（比例）
    "photo_flat_hi": 0.80,  # 平坦比高於此值→像插畫（比例）
    "photo_hf_lo": 1.5,  # 高頻比（區內雜訊 ÷ 頁面雜訊）低於此值→像插畫（倍）
    "photo_hf_hi": 4.0,  # 高頻比高於此值→像照片（倍）
    "photo_w_colors": 0.5,  # 照片分數中色彩數的權重
    "photo_w_flat": 0.25,  # 照片分數中平坦比的權重
    "photo_w_hf": 0.25,  # 照片分數中高頻比的權重
    "enclosed_by_photo": 0.85,  # 平坦區塊外圍有多少比例落在照片內時併入照片（比例）
    "illus_group_px": 4,  # 插畫碎塊合併的距離（像素）
    "shadow_max_contrast": 130.0,  # 陰影與背景的平均色差上限（色階）
    "shadow_line_rel": 0.25,  # 細長的柔邊暗帶，對比低於鄰居的此比例才當陰影（否則當線；比例）
    "shadow_soft_frac": 0.5,  # 陰影須為柔邊：區內對比的（90 百分位 − 10 百分位）至少佔 90 百分位的比例
    "shadow_max_width_rel": 0.02,  # 陰影最大厚度（佔影像短邊比例）
    # ---------------- 形狀擬合 ----------------
    "fit_tol_px": 1.5,  # 輪廓到模型的容許平均偏差下限（像素）
    "fit_tol_rel": 0.001,  # 容許偏差＝此比例·影像長邊（與上一項取大者）
    "accept_mean_k": 1.0,  # 接受模型：平均誤差 ≤ k·容許偏差（倍）
    "accept_p95_k": 2.0,  # 接受模型：95 百分位誤差 ≤ k·容許偏差（倍）
    "min_overlap": 0.93,  # 模型畫回去與遮罩的最低 IoU（比例；設計文件初值 97 %，因被遮擋區不計而放寬）
    "min_corner_radius_px": 2.0,  # 圓角半徑小於此值當直角（像素）
    "rrect_gain": 0.85,  # 圓角矩形的平均誤差須小於矩形的此倍數才採用（倍）
    "ellipse_pref": 1.15,  # 橢圓誤差 ≤ 此倍數·圓角矩形誤差時優先採橢圓（較簡單）（倍）
    "max_polygon_vertices": 12,  # 多邊形頂點上限，超過就不是簡單形狀（個）
    "poly_eps_k": 1.0,  # Douglas–Peucker 容許誤差＝k·容許偏差（倍）
    "line_max_thick_px": 3.0,  # 直線粗細上限的下限（像素）
    "line_max_thick_rel": 0.01,  # 直線粗細上限＝此比例·影像短邊（與上一項取大者）
    "line_min_aspect": 6.0,  # 直線的最小長寬比（倍）
    "stroke_max_rel": 0.03,  # 外框線寬上限（佔形狀短邊的比例，另與直線上限取大者）
    "ring_hole_frac": 0.35,  # 環狀判定：洞面積至少佔填滿後面積的比例
    "ring_fill_frac": 0.6,  # 洞內平坦區塊至少佔洞面積多少才當填色（比例）
    "std_inflate": 2.0,  # 擬合參數標準差的放大倍數（相鄰輪廓點高度相關，共變異數會低估誤差；倍）
    "radius_err_floor_px": 0.4,  # 圓角半徑誤差的下限（像素）
    "radius_model_rel_err": 0.03,  # 圓角半徑的模型誤差下限（佔半徑比例；貝茲圓角、超橢圓與正圓弧的差）
    "max_fit_points": 800,  # 擬合時最多取多少輪廓點（點）
    "occlusion_dilate_px": 2,  # 文字與其他區域旁多少像素內的輪廓點視為被遮擋（像素）
    "outline_accept_k": 2.0,  # 照片／插畫外形擬合的容許偏差放寬倍數（倍）
    # ---------------- 表格 ----------------
    "table_min_line_rel": 0.03,  # 格線最短長度（佔影像寬的比例）
    "table_min_line_px": 12,  # 格線最短長度的下限（像素）
    "table_max_thick_rel": 0.005,  # 格線最大粗細（佔影像寬的比例）
    "table_max_thick_px": 3.0,  # 格線最大粗細的下限（像素）
    "table_gap_join_k": 3.0,  # 共線斷線的接合間隙＝k·線粗（倍，至少 3 px）
    "table_pos_tol_px": 3.0,  # 判定交叉、合併同位置格線的容許偏差（像素）
    "table_min_cells": 2,  # 有框表格至少幾格（格）
    "table_rule_only_min": 3,  # 只有橫線的三線表至少幾條（條）
    # ---------------- 無框線表格（text_quads） ----------------
    "bl_row_tol": 0.5,  # 同一列的 y 中心容許差（倍字高）
    "bl_row_gap_max": 2.5,  # 相鄰列的最大垂直間距（倍字高）
    "bl_river_min": 1.0,  # 欄間空白帶最小寬度（倍字高）
    "bl_min_rows": 3,  # 最少列數（列）
    "bl_min_cols": 2,  # 最少欄數（欄）
    "bl_row_align_min": 0.8,  # 跨欄對齊的列所佔比例下限（比例）
    "bl_max_cell_frac": 0.6,  # 儲存格平均寬度上限（佔表格寬的比例；擋掉兩欄內文）
    "bl_max_col_fill": 0.75,  # 儲存格寬 ÷ 欄寬的中位數上限（比例）；分欄長文每行幾乎填滿整欄
    "bl_conf_base": 0.25,  # 無框表格信心值基數
    "bl_conf_gain": 0.35,  # 無框表格信心值隨支持度增加的幅度
    # ---------------- 裁切 ----------------
    "crop_feather_px": 0.7,  # 遮罩羽化的高斯 σ（像素）
    "crop_supersample": 4,  # 以幾何外形畫透明度時的超取樣倍數（倍）
    # ---------------- 疊在照片上的平塗形狀（設定檔裡名稱前加 region_） ----------------
    "overlay_enabled": True,  # 是否把照片或有紋理的插畫裡「看得出是後來疊上去」的平塗色塊拆成獨立形狀
    "overlay_min_area_rel": 1.0e-3,  # 面積下限（佔影像面積的比例）；更小的留在照片裡
    "overlay_hp_sigma": 2.0,  # 量顆粒（細紋理）用的高通：原圖減去這個 σ 的高斯平滑（像素）；線性漸層不留殘差
    "overlay_tile_px": 32,  # 量照片本身最平坦處顆粒的格子邊長（像素）
    "overlay_floor_pct": 25.0,  # 照片的顆粒下限：取各格顆粒的第幾百分位（最平坦的地方也有的顆粒）
    "overlay_min_texture": 1.5,  # 照片的顆粒下限至少要這麼大（色階）才比得出「形狀比照片乾淨」；照片太乾淨就不拆
    "overlay_noise_ratio": 0.35,  # 形狀內部的顆粒 ÷ 照片的顆粒下限，不超過此值才算後來疊上去的（倍）
    "overlay_noise_ratio_textured": 0.08,  # 照片沒有本身平坦的地方（整張是紋理）時改用這個更嚴的比值：下限只能取自紋理處，偏高
    "overlay_flat_tile_k": 1.5,  # 「本身平坦」的格子：低頻起伏（σ3 平滑後扣掉平面）不超過此倍數的顆粒（倍）
    "overlay_hp_sigma_thin": 1.0,  # 細長的形狀內縮後量不到顆粒時，改用這個較小的高通 σ（像素）再量
    "overlay_clip_low": 2.0,  # 三個色版都不超過此值的色塊（純黑）可能是照片欠曝截平的地方，不拆（色階）
    "overlay_clip_high": 253.0,  # 三個色版都至少此值的色塊（純白）可能是照片過曝截平的地方，不拆（色階）
    "overlay_translucent_ratio": 0.05,  # 頁面底色完全乾淨時，形狀內部的顆粒比超過此值就當成半透明（透出照片），不拆（倍）
    "overlay_translucent_page_max": 0.5,  # 上一項只在頁面底色的顆粒不超過此值（乾淨的數位畫面）時才用（色階）
    "overlay_page_grain_k": 2.0,  # 形狀內部的顆粒也不能比頁面底色多太多：上限＝此倍數 × 底色顆粒＋下一項（量得到底色時才用）
    "overlay_page_grain_abs": 1.0,  # 同上的絕對容許（色階）
    "overlay_max_gradient": 10.0,  # 形狀內部跨區的顏色變化上限（色階）；有明暗或漸層的（天空、牆面）不拆
    "overlay_edge_contrast": 12.0,  # 邊緣兩側的色差至少要這麼大（色階；另與 4 倍顆粒下限取大者）
    "overlay_edge_sharpness": 0.25,  # 邊緣的銳利度：邊緣上最大梯度 ÷ 兩側色差的下限（模糊 σ 約 1.6 px 以內的邊緣才過）
    "overlay_edge_continuity": 0.85,  # 沒被遮住的邊界中，銳利而且有對比的比例下限
    "overlay_edge_visible": 0.4,  # 邊界至少要有這個比例沒被文字或其他物件遮住，才有足夠證據
    "overlay_trace": True,  # 擬合不成基本幾何時改試描圖；描圖過關才拆成向量圖，否則留在照片裡
    "overlay_full_bleed": True,  # 滿版照片（沒有頁面底色）也找疊在上面的圖形；找不到就照原本的讀法
    "overlay_full_bleed_border_flat": 0.6,  # 影像外框一圈的格子裡平坦格子不超過此比例，才可能是滿版照片（JPEG 會把照片邊緣壓平一些：量到 0.41）
    "overlay_full_bleed_touch": 0.05,  # 選出來的「底色」佔外框格子不超過此比例（幾乎沒碰到外框），才不是真的頁面底色
    "overlay_full_bleed_coverage": 0.3,  # 而且「底色」只佔全部格子的這個比例以下（大片的是真的底色，例如有花邊框的頁面）
    "overlay_clean_grain": 1.0,  # 「完全沒有顆粒」的上限（色階；另與 2 倍頁面雜訊取大者）：照片紋理連到形狀時，靠它把形狀挖出來
    "overlay_tip_chamfer": 0.2,  # 疊在照片上的多邊形：比相鄰長邊短到此比例以下的邊，當成被 JPEG 削掉的尖角試著補回（倍）
    "overlay_tip_color_frac": 0.6,  # 補回的尖角裡，顏色比較接近形狀（而不是周圍照片）的像素至少要這個比例
    "overlay_fill_block_px": 24,  # 補照片時複製顆粒的方塊邊長（像素）
    "overlay_fill_pad_px": 64,  # 補照片時，要補的範圍外擴這麼多像素（另與下一項取大者）當成依據，不必整張照片交給背景補全
    "overlay_fill_pad_rel": 0.25,  # 外擴的比例（佔要補範圍的長邊）
    "overlay_fill_quality_warn": 0.5,  # 補出來的地方的中尺度紋理不到周圍的此比例時，在 notes 提醒看得出是補的（fill_quality；影像產線用）
    "overlay_fill_grain_sigma": 4.0,  # 複製的顆粒＝原圖減去這個 σ 的高斯平滑（像素）；比量顆粒用的大，連較粗的顆粒一起帶過去
}

_EPS = 1e-9


def _merge(params: dict | None) -> dict:
    P = dict(DEFAULT_PARAMS)
    if params:
        P.update(params)
    return P


# =====================================================================================
# 共用小工具
# =====================================================================================


def _to_rgb(c) -> RGB:
    c = np.clip(np.round(np.asarray(c, dtype=np.float64)), 0, 255)
    return (int(c[0]), int(c[1]), int(c[2]))


def _fill_unknown(img: np.ndarray, unknown: np.ndarray) -> np.ndarray:
    """未知像素（文字）以最近的已知像素填平；只供分割、分類使用。"""
    out = img.copy()
    if not unknown.any() or unknown.all():
        return out
    H, W = unknown.shape
    _, labels = cv2.distanceTransformWithLabels(unknown.astype(np.uint8), cv2.DIST_L2, 3, labelType=cv2.DIST_LABEL_PIXEL)
    ky, kx = np.nonzero(~unknown)
    lut = np.zeros(int(labels.max()) + 1, np.int64)
    lut[labels[ky, kx]] = ky * W + kx
    uy, ux = np.nonzero(unknown)
    flat = out.reshape(H * W, -1)
    flat[uy * W + ux] = flat[lut[labels[uy, ux]]]
    return out


def _nearest_fill_local(values: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """回傳每個像素「最近的 ref 像素」的 values（values 為 (h, w, c)）。"""
    h, w = ref.shape
    if not ref.any():
        return values.copy()
    _, labels = cv2.distanceTransformWithLabels((~ref).astype(np.uint8), cv2.DIST_L2, 3, labelType=cv2.DIST_LABEL_PIXEL)
    ky, kx = np.nonzero(ref)
    lut = np.zeros(int(labels.max()) + 1, np.int64)
    lut[labels[ky, kx]] = ky * w + kx
    flat = values.reshape(h * w, -1)
    return flat[lut[labels]].reshape(values.shape)


_LAP = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], np.float32)


def _laplace_abs(img: np.ndarray) -> np.ndarray:
    """Immerkær 雜訊核的絕對響應（各色版取平均）；白雜訊 σ 對應中位數約 σ·6/1.4826。"""
    r = np.zeros(img.shape[:2], np.float32)
    for c in range(img.shape[2]):
        r += np.abs(cv2.filter2D(img[..., c], cv2.CV_32F, _LAP, borderType=cv2.BORDER_REFLECT))
    return r / img.shape[2]


def _sigma_from_lap(values: np.ndarray) -> float:
    if values.size == 0:
        return 0.0
    return float(1.4826 * np.median(values) / 6.0)


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    m = mask.astype(np.uint8)
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = np.zeros_like(m)
    cv2.drawContours(out, cs, -1, 1, thickness=cv2.FILLED)
    return out.astype(bool) | mask


def _bbox_of(mask: np.ndarray) -> Optional[tuple[int, int, int, int]]:
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _disk(r: int) -> np.ndarray:
    r = max(int(r), 1)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return mask.copy()
    return cv2.dilate(mask.astype(np.uint8), _disk(r)).astype(bool)


def _ramp(x: float, lo: float, hi: float) -> float:
    """x 在 lo 以下為 0、hi 以上為 1，中間線性。"""
    if hi == lo:
        return float(x >= hi)
    return float(np.clip((x - lo) / (hi - lo), 0.0, 1.0))


def _luma(c) -> float:
    c = np.asarray(c, dtype=np.float64)
    return float(0.299 * c[..., 0] + 0.587 * c[..., 1] + 0.114 * c[..., 2])


# =====================================================================================
# 背景模型
# =====================================================================================


@dataclass
class BackgroundModel:
    """頁面底色模型：純色或二次曲面漸層（每個色版 6 個係數，座標以影像長邊正規化）。"""

    kind: str  # "flat"、"gradient"、"complex"（擬合不好）、"none"（找不到平坦背景）
    color: Optional[RGB]  # 純色時的底色；漸層時為背景格子的中位色
    coeffs: np.ndarray  # (6, 3)：1、x、y、x²、xy、y²
    noise_sigma: float  # 頁面雜訊 σ（色階）
    residual: float  # 背景格子對模型的 95 百分位殘差（色階）
    coverage: float  # 背景格子佔全部格子的比例
    confidence: float
    notes: list[str] = field(default_factory=list)
    border_flat: float = 1.0  # 影像外框一圈的格子裡，平坦格子的比例（滿版照片的外框幾乎都是紋理）
    border_touch: float = 1.0  # 影像外框一圈的格子裡，屬於選中的底色那一片的比例（0 表示底色完全沒碰到外框）

    def predict(self, height: int, width: int) -> np.ndarray:
        S = float(max(height, width))
        xs = ((np.arange(width, dtype=np.float32) + 0.5) / S)[None, :]
        ys = ((np.arange(height, dtype=np.float32) + 0.5) / S)[:, None]
        c = self.coeffs.astype(np.float32)
        out = np.empty((height, width, 3), np.float32)
        for k in range(3):
            out[..., k] = c[0, k] + c[1, k] * xs + c[2, k] * ys + c[3, k] * xs * xs + c[4, k] * xs * ys + c[5, k] * ys * ys
        return out


def _basis(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=1)


def _estimate_background_filled(filled: np.ndarray, unknown: np.ndarray, lap_abs: np.ndarray, P: dict) -> BackgroundModel:
    H, W = unknown.shape
    t = max(int(P["bg_tile_min_px"]), int(round(min(H, W) / float(P["bg_tile_div"]))))
    ny, nx = max(H // t, 1), max(W // t, 1)
    t = min(t, H, W)
    hh, ww = ny * t, nx * t
    crop = filled[:hh, :ww].reshape(ny, t, nx, t, 3).transpose(0, 2, 1, 3, 4).reshape(ny, nx, t * t, 3)
    val = (~unknown[:hh, :ww]).reshape(ny, t, nx, t).transpose(0, 2, 1, 3).reshape(ny, nx, t * t)
    lapt = lap_abs[:hh, :ww].reshape(ny, t, nx, t).transpose(0, 2, 1, 3).reshape(ny, nx, t * t)
    vfrac = val.mean(axis=2)
    valid = vfrac >= float(P["bg_tile_valid_frac"])
    arr = np.where(val[..., None], crop, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(arr, axis=2)
        mad = np.nanmedian(np.abs(arr - med[:, :, None, :]), axis=2).max(axis=2)
        lmed = np.nanmedian(np.where(val, lapt, np.nan), axis=2)
    sig_tile = 1.4826 * lmed / 6.0
    notes: list[str] = []
    if not valid.any():
        sigma = 0.0
    else:
        sigma = float(np.percentile(sig_tile[valid], 25))
    flat = valid & (mad <= max(float(P["bg_tile_flat_abs"]), float(P["bg_tile_flat_k"]) * sigma))
    n_valid = int(valid.sum())
    S = float(max(H, W))
    cy, cx = np.mgrid[0:ny, 0:nx]
    xc = ((cx + 0.5) * t / S).astype(np.float64)
    yc = ((cy + 0.5) * t / S).astype(np.float64)
    edge_t = (cy == 0) | (cy == ny - 1) | (cx == 0) | (cx == nx - 1)
    n_edge = max(int((valid & edge_t).sum()), 1)
    border_flat = float((flat & edge_t).sum()) / n_edge

    def _fallback(kind: str) -> BackgroundModel:
        ring = np.zeros((H, W), bool)
        b = max(2, min(H, W) // 50)
        ring[:b, :] = ring[-b:, :] = True
        ring[:, :b] = ring[:, -b:] = True
        ring &= ~unknown
        col = np.median(filled[ring], axis=0) if ring.any() else np.median(filled.reshape(-1, 3), axis=0)
        coeffs = np.zeros((6, 3))
        coeffs[0] = col
        return BackgroundModel(kind, _to_rgb(col), coeffs, sigma, 99.0, 0.0, 0.1, notes + ["找不到平坦背景，以影像邊緣中位色代替；前景判斷不可靠"],
                               border_flat=border_flat, border_touch=0.0)

    if flat.sum() < 2:
        return _fallback("none")
    # 平坦格子之間依色差連成圖
    idx = -np.ones((ny, nx), np.int64)
    fl_y, fl_x = np.nonzero(flat)
    idx[fl_y, fl_x] = np.arange(fl_y.size)
    tol = float(P["bg_adj_tol"]) + 2.0 * sigma
    rows, cols = [], []
    for dy, dx in ((0, 1), (1, 0)):
        a = flat[: ny - dy, : nx - dx] & flat[dy:, dx:]
        d = np.linalg.norm(med[: ny - dy, : nx - dx] - med[dy:, dx:], axis=2)
        ok = a & (d <= tol)
        yy, xx = np.nonzero(ok)
        rows.append(idx[yy, xx])
        cols.append(idx[yy + dy, xx + dx])
    r = np.concatenate(rows)
    c = np.concatenate(cols)
    n = fl_y.size
    g = coo_matrix((np.ones(r.size), (r, c)), shape=(n, n))
    ncomp, lab = connected_components(g, directed=False)
    border = (fl_y == 0) | (fl_y == ny - 1) | (fl_x == 0) | (fl_x == nx - 1)
    score = np.bincount(lab, minlength=ncomp) + float(P["bg_border_bonus"]) * np.bincount(lab, weights=border.astype(float), minlength=ncomp)
    best = int(np.argmax(score))
    sel = lab == best
    coverage = float(sel.sum()) / max(n_valid, 1)
    if coverage < float(P["bg_min_coverage"]):
        return _fallback("none")

    meds = med[fl_y, fl_x].astype(np.float64)
    A_all = _basis(xc[fl_y, fl_x], yc[fl_y, fl_x])
    flat_col = np.median(meds[sel], axis=0)
    res_flat = np.linalg.norm(meds[sel] - flat_col, axis=1)
    coeffs = np.zeros((6, 3))
    coeffs[0] = flat_col
    kind = "flat"
    if np.percentile(res_flat, 95) > float(P["bg_flat_tol"]) + 2.0 * sigma and sel.sum() >= 8:
        kind = "gradient"
        use = sel.copy()
        for _ in range(4):
            coeffs, *_ = np.linalg.lstsq(A_all[use], meds[use], rcond=None)
            res = np.linalg.norm(A_all @ coeffs - meds, axis=1)
            thr = max(float(P["bg_refit_tol"]) + 2.0 * sigma, 3.0 * 1.4826 * float(np.median(res[use])))
            new_use = (res <= thr) & (sel | (res <= float(P["bg_refit_tol"])))
            if new_use.sum() < 6 or np.array_equal(new_use, use):
                break
            use = new_use
        sel = use
        res_sel = np.linalg.norm(A_all[sel] @ coeffs - meds[sel], axis=1)
        residual = float(np.percentile(res_sel, 95))
        if residual > 2.5 * (float(P["bg_refit_tol"]) + 2.0 * sigma):
            kind = "complex"
            notes.append("背景無法以平滑漸層描述（殘差大）")
    else:
        # 純色：重新納入其他顏色相符的平坦格
        res_all = np.linalg.norm(meds - flat_col, axis=1)
        sel = sel | (res_all <= float(P["bg_flat_tol"]) + 2.0 * sigma)
        flat_col = np.median(meds[sel], axis=0)
        coeffs[0] = flat_col
        residual = float(np.percentile(np.linalg.norm(meds[sel] - flat_col, axis=1), 95))
    coverage = float(sel.sum()) / max(n_valid, 1)
    conf = float(np.clip(0.4 + coverage, 0.0, 0.95)) * (0.5 if kind == "complex" else 1.0)
    color = _to_rgb(np.median(meds[sel], axis=0))
    on_edge = edge_t[fl_y, fl_x]
    return BackgroundModel(kind, color, coeffs, sigma, residual, coverage, conf, notes, border_flat=border_flat, border_touch=float((sel & on_edge).sum()) / n_edge)


def estimate_background(image: np.ndarray, text_mask: np.ndarray | None = None, params: dict | None = None) -> BackgroundModel:
    """估計頁面底色（純色或平滑漸層）。text_mask 內的像素視為未知。"""
    P = _merge(params)
    img = np.ascontiguousarray(image[..., :3]).astype(np.float32)
    unknown = np.zeros(img.shape[:2], bool) if text_mask is None else text_mask.astype(bool)
    filled = _fill_unknown(img, unknown)
    return _estimate_background_filled(filled, unknown, _laplace_abs(filled), P)


# =====================================================================================
# 公開介面（實作在下方各節）
# =====================================================================================


def find_regions(
    image: np.ndarray,
    text_mask: np.ndarray,
    params: dict | None = None,
    text_quads: list[np.ndarray] | None = None,
) -> list[Region]:
    """找出文字以外的區域（照片、插畫、形狀、表格），遮罩可重疊，附圖層初判 z。"""
    P = _merge(params)
    return _find_regions_impl(image, text_mask, P, text_quads)


def crop_region(image: np.ndarray, region: Region, inpainted: np.ndarray | None = None, params: dict | None = None) -> np.ndarray:
    """把照片或插畫區域裁成 RGBA；非矩形外形以透明度表示，邊緣略為羽化。"""
    P = _merge(params)
    return _crop_region_impl(image, region, inpainted, P)


def fill_covered(image: np.ndarray, region: Region, cover: np.ndarray, params: dict | None = None) -> tuple[np.ndarray, np.ndarray]:
    """照片或插畫被上層元素（例如拆出來的平塗形狀）蓋住的地方，補成合理的照片內容；補出來的像素屬於生成。

    image：整張工作影像（文字已經擦掉的版本更好）。cover：整張影像大小的 bool，上層元素佔的位置（補全後的外形）。
    回傳（補好的整張影像 RGB uint8、整張影像大小的生成遮罩）。只改 region 範圍內被蓋住的像素，其餘照抄。
    做法：
      1. 要補的範圍＝cover 外擴 2 px（反鋸齒的混色邊）與 region 遮罩的交集；region 遮罩內其餘像素是已知像素。
      2. 平滑的部分沿用背景補全（background.reconstruct_background）：純色、漸層、局部擬合；都不合時用
         「薄膜內插」修補器（已知像素先平滑，再解 Laplace 方程，由粗到細；線性漸層可以完全還原，不會有 Telea 的放射條紋）。
      3. 顆粒：照片有明顯顆粒時，從已知像素中挑「顆粒大小一般」的方塊，把它們的高通殘差複製進要補的範圍
         （方塊邊長 overlay_fill_block_px；挑選用固定亂數種子，結果可重現）。邊緣、細節多的方塊不挑。
    """
    from . import background as bgmod

    P = _merge(params)
    img = np.ascontiguousarray(np.asarray(image)[..., :3]).astype(np.uint8)
    H, W = img.shape[:2]
    gen = np.zeros((H, W), bool)
    x0, y0, x1, y1 = region.bbox
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1 <= x0 or y1 <= y0:
        return img, gen
    rb = region.bbox
    amodal = np.asarray(region.mask, bool)[y0 - rb[1] : y1 - rb[1], x0 - rb[0] : x1 - rb[0]]
    cov = np.asarray(cover, bool)[y0:y1, x0:x1] & amodal
    if not cov.any():
        return img, gen
    fill = _dilate(cov, 2) & amodal
    known = amodal & ~fill
    if known.sum() < 64:
        return img, gen  # 幾乎整張都被蓋住：沒有依據可補，照原樣
    crop = img[y0:y1, x0:x1]
    bgmod.register_inpainter("membrane", _membrane_inpaint)  # 背景補全的替換點；重複註冊只是覆蓋同一個名稱
    # 只把「要補的範圍外擴一圈」交給背景補全：大張照片整張交進去，光是量雜訊就要好幾秒（4000×3000 實測 6.9 秒）
    fb = _bbox_of(fill)
    pad = max(int(P["overlay_fill_pad_px"]), int(math.ceil(float(P["overlay_fill_pad_rel"]) * max(fb[2] - fb[0], fb[3] - fb[1]))))
    wx0, wy0, wx1, wy1 = _expand_box(fb, pad, y1 - y0, x1 - x0)
    res = bgmod.reconstruct_background(crop[wy0:wy1, wx0:wx1], ~known[wy0:wy1, wx0:wx1], {"noise_match": False, "inpaint_method": "membrane"})
    out = crop.astype(np.float32)
    fw = fill[wy0:wy1, wx0:wx1]
    out[wy0:wy1, wx0:wx1][fw] = res.image[fw].astype(np.float32)
    grain = _grain_fill(crop.astype(np.float32), known, fill, P)
    if grain is not None:
        out[fill] += grain[fill]
    result = img.copy()
    result[y0:y1, x0:x1] = np.clip(np.rint(out), 0, 255).astype(np.uint8)
    gen[y0:y1, x0:x1] = fill
    return result, gen


def fill_quality(filled: np.ndarray, gen: np.ndarray, region: Region) -> Optional[float]:
    """補出來的地方像不像周圍：中尺度起伏（亮度的 σ2 減 σ8 高斯平滑，大約 4 到 16 像素的紋理）的標準差，
    補出來的地方 ÷ 周圍一圈（外擴 24 像素、屬於這張照片、不是補的）。接近 1 表示看不出來；
    遠小於 1 表示補出來的是一團平滑的色塊（多尺度紋理的照片），移動上層的圖形就看得出是補的。量不到時回傳 None。"""
    x0, y0, x1, y1 = region.bbox
    g = np.asarray(gen, bool)[y0:y1, x0:x1]
    if g.sum() < 400:
        return None
    rb = np.asarray(region.mask, bool)
    I = np.asarray(filled, np.float32)[y0:y1, x0:x1, :3]
    Y = I @ np.array([0.299, 0.587, 0.114], np.float32)
    dog = cv2.GaussianBlur(Y, (0, 0), 2.0) - cv2.GaussianBlur(Y, (0, 0), 8.0)
    inner = cv2.erode(g.astype(np.uint8), _disk(8), borderType=cv2.BORDER_CONSTANT, borderValue=0).astype(bool)
    ring = _dilate(g, 24) & ~_dilate(g, 8) & rb
    if inner.sum() < 200 or ring.sum() < 200:
        return None
    a = float(np.std(dog[inner]))
    b = float(np.std(dog[ring]))
    return a / b if b > 0.5 else None


def _membrane_inpaint(img: np.ndarray, mask: np.ndarray, p: dict) -> np.ndarray:
    """薄膜內插修補器（給 background.register_inpainter 用）：要補的像素解 Laplace 方程，已知像素（先平滑）當邊界。

    由粗到細：影像縮到最長邊 96 格以內，用稀疏矩陣直接解；每往細一層先放大當初值，再做 Jacobi 鬆弛。
    結果平滑、連續，線性漸層可以完全還原；不補紋理（顆粒另外處理）。"""
    from scipy.sparse import csr_matrix
    from scipy.sparse.linalg import spsolve

    out = img.copy()
    if not mask.any():
        return out
    H, W = mask.shape
    bb = _bbox_of(mask)
    pad = max(16, int(0.25 * max(bb[2] - bb[0], bb[3] - bb[1])))
    X0, Y0, X1, Y1 = _expand_box(bb, pad, H, W)
    m = mask[Y0:Y1, X0:X1]
    kn = ~m
    if not kn.any():
        return out
    I = img[Y0:Y1, X0:X1].astype(np.float32)
    k = kn.astype(np.float32)
    s = 2.0  # 已知像素先平滑（只用已知像素），雜訊才不會被當成邊界條件拉成條紋
    num = cv2.GaussianBlur(I * k[..., None], (0, 0), s)
    den = cv2.GaussianBlur(k, (0, 0), s)
    vals = np.where(den[..., None] > 1e-3, num / np.maximum(den, 1e-3)[..., None], I)
    pyr = [(vals * k[..., None], k)]
    while max(pyr[-1][1].shape) > 96:
        V, M = pyr[-1]
        hh, ww = M.shape
        size = (max(1, (ww + 1) // 2), max(1, (hh + 1) // 2))
        pyr.append((cv2.resize(V, size, interpolation=cv2.INTER_AREA), cv2.resize(M, size, interpolation=cv2.INTER_AREA)))
    V, M = pyr[-1]
    known_c = M >= 0.5
    X = np.where(known_c[..., None], V / np.maximum(M, 1e-6)[..., None], 0.0).astype(np.float32)
    if not known_c.any():
        X[:] = np.median(I[kn], axis=0)
    else:
        hh, ww = M.shape
        uy, ux = np.nonzero(~known_c)
        n = len(uy)
        if n:
            idx = -np.ones((hh, ww), np.int64)
            idx[uy, ux] = np.arange(n)
            rows, cols, data = [], [], []
            rhs = np.zeros((n, 3), np.float64)
            deg = np.zeros(n)
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                ny_, nx_ = uy + dy, ux + dx
                inb = (ny_ >= 0) & (ny_ < hh) & (nx_ >= 0) & (nx_ < ww)
                deg += inb
                a = np.nonzero(inb)[0]
                nb_idx = idx[ny_[a], nx_[a]]
                unk = nb_idx >= 0
                rows.append(a[unk])
                cols.append(nb_idx[unk])
                data.append(-np.ones(int(unk.sum())))
                kk = a[~unk]
                rhs[kk] += X[ny_[kk], nx_[kk]]
            rows.append(np.arange(n))
            cols.append(np.arange(n))
            data.append(np.maximum(deg, 1.0))
            A = csr_matrix((np.concatenate(data), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n))
            try:
                sol = spsolve(A.tocsc(), rhs)
                X[uy, ux] = np.asarray(sol, np.float32).reshape(n, 3)
            except Exception:  # noqa: BLE001 - 解不出來（沒有邊界的孤島）就用已知像素的平均
                X[uy, ux] = np.median(X[known_c], axis=0)
    kern = np.array([[0, 0.25, 0], [0.25, 0, 0.25], [0, 0.25, 0]], np.float32)
    for V, M in reversed(pyr[:-1]):
        hh, ww = M.shape
        X = cv2.resize(X, (ww, hh), interpolation=cv2.INTER_LINEAR)
        known_l = M >= 0.5
        Xk = V / np.maximum(M, 1e-6)[..., None]
        np.copyto(X, Xk.astype(np.float32), where=known_l[..., None])
        unk = np.repeat(~known_l[..., None], 3, axis=2)
        # 初值已經是上一層的解，只剩邊界附近的細部誤差：細的層鬆弛次數少一點
        for _ in range(12 if hh * ww > 250000 else 25):
            np.copyto(X, cv2.filter2D(X, -1, kern, borderType=cv2.BORDER_REPLICATE), where=unk)
    sub = out[Y0:Y1, X0:X1]
    sub[m] = np.clip(np.rint(X[m]), 0, 255).astype(np.uint8)
    return out


def _grain_fill(crop: np.ndarray, known: np.ndarray, fill: np.ndarray, P: dict) -> Optional[np.ndarray]:
    """要補的範圍的顆粒：從已知像素挑方塊，複製它們的高通殘差。照片太乾淨（顆粒很小）就不加，回傳 None。"""
    s = float(P["overlay_fill_grain_sigma"])
    k = known.astype(np.float32)
    num = cv2.GaussianBlur(crop * k[..., None], (0, 0), s)
    den = cv2.GaussianBlur(k, (0, 0), s)
    R = crop - num / np.maximum(den, 1e-3)[..., None]
    ok = known & (den > 0.97)  # 離要補的範圍、外框夠遠，殘差才不受影響
    B = max(8, int(P["overlay_fill_block_px"]))
    h, w = known.shape
    ny, nx = h // B, w // B
    if ny * nx == 0:
        return None
    Rb = R[: ny * B, : nx * B].reshape(ny, B, nx, B, 3).transpose(0, 2, 1, 3, 4)
    Ob = ok[: ny * B, : nx * B].reshape(ny, B, nx, B).transpose(0, 2, 1, 3)
    full = Ob.all(axis=(2, 3))
    if full.sum() < 4:
        return None
    energy = np.abs(Rb).mean(axis=(2, 3, 4))
    e = energy[full]
    grain = float(np.median(e)) * _GRAIN_K
    if grain < 1.0:
        return None  # 照片幾乎沒有顆粒：平滑補就好
    src = np.argwhere(full & (energy <= 1.5 * float(np.median(e))))  # 邊緣、細節多的方塊不挑
    if len(src) == 0:
        return None
    rng = np.random.default_rng(1729)
    out = np.zeros_like(crop)
    fb = _bbox_of(fill)
    for by in range(fb[1], fb[3], B):
        for bx in range(fb[0], fb[2], B):
            hh, ww = min(B, h - by), min(B, w - bx)
            if not fill[by : by + hh, bx : bx + ww].any():
                continue
            sy, sx = src[int(rng.integers(len(src)))]
            out[by : by + hh, bx : bx + ww] = Rb[sy, sx, :hh, :ww]
    return out


def fit_shape(mask: np.ndarray, image: np.ndarray | None = None, params: dict | None = None, unknown: np.ndarray | None = None) -> Optional[ShapeFit]:
    """單獨對一個遮罩（整張影像大小的 bool）做幾何擬合；不是簡單幾何時回傳 None。供測試與其他模組重用。"""
    P = _merge(params)
    box = _bbox_of(mask)
    if box is None:
        return None
    H, W = mask.shape
    m = 4
    X0, Y0, X1, Y1 = max(0, box[0] - m), max(0, box[1] - m), min(W, box[2] + m), min(H, box[3] + m)
    loc = mask[Y0:Y1, X0:X1]
    bad = np.zeros_like(loc) if unknown is None else _dilate(unknown[Y0:Y1, X0:X1] & ~loc, int(P["occlusion_dilate_px"]))
    tol = _fit_tol(P, H, W)
    fit = _fit_mask(loc, bad, P, tol, allow_line=True, line_thick=_line_thick(P, H, W))
    if fit is None or not fit.accepted:
        return None
    fill = None
    if image is not None:
        inner = loc & ~(np.zeros_like(loc) if unknown is None else unknown[Y0:Y1, X0:X1])
        er = cv2.erode(inner.astype(np.uint8), _disk(1)).astype(bool)
        px = image[Y0:Y1, X0:X1][er if er.sum() >= 5 else inner].reshape(-1, 3)
        if px.size:
            fill = _to_rgb(np.median(px, axis=0))
    return _make_shapefit(P, fit, (X0, Y0), fill=fill)


# =====================================================================================
# 幾何擬合
# =====================================================================================


def _fit_tol(P: dict, H: int, W: int) -> float:
    return max(float(P["fit_tol_px"]), float(P["fit_tol_rel"]) * max(H, W))


def _line_thick(P: dict, H: int, W: int) -> float:
    return max(float(P["line_max_thick_px"]), float(P["line_max_thick_rel"]) * min(H, W))


@dataclass
class _Fit:
    geometry: str
    cx: float = 0.0
    cy: float = 0.0
    th: float = 0.0
    hx: float = 0.0
    hy: float = 0.0
    r: float = 0.0
    poly: Optional[np.ndarray] = None
    err_mean: float = 99.0
    err_p95: float = 99.0
    iou: float = 0.0
    std: dict = field(default_factory=dict)
    candidates: list = field(default_factory=list)
    accepted: bool = False
    confidence: float = 0.0


def _norm_angle(th: float, hx: float, hy: float) -> tuple[float, float, float]:
    """角度正規化到 (-45°, 45°]，必要時交換長寬，讓「上邊」是最接近水平的那條。"""
    th = (th + math.pi / 2) % math.pi - math.pi / 2
    if th > math.pi / 4:
        th -= math.pi / 2
        hx, hy = hy, hx
    elif th <= -math.pi / 4:
        th += math.pi / 2
        hx, hy = hy, hx
    return th, abs(hx), abs(hy)


def _local(pts: np.ndarray, cx: float, cy: float, th: float) -> tuple[np.ndarray, np.ndarray]:
    c, s = math.cos(th), math.sin(th)
    dx = pts[:, 0] - cx
    dy = pts[:, 1] - cy
    return c * dx + s * dy, -s * dx + c * dy


def _rrect_sdf(p, pts, r_fixed=None):
    cx, cy, th, hx, hy = p[:5]
    hx, hy = abs(hx), abs(hy)
    r = p[5] if r_fixed is None else r_fixed
    r = min(max(r, 0.0), min(hx, hy))
    u, v = _local(pts, cx, cy, th)
    qx = np.abs(u) - (hx - r)
    qy = np.abs(v) - (hy - r)
    out = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0))
    ins = np.minimum(np.maximum(qx, qy), 0)
    return out + ins - r


def _ell_sampson(p, pts):
    cx, cy, th, a, b = p
    a, b = abs(a) + _EPS, abs(b) + _EPS
    u, v = _local(pts, cx, cy, th)
    F = (u / a) ** 2 + (v / b) ** 2 - 1.0
    g = 2.0 * np.hypot(u / a**2, v / b**2)
    return F / np.maximum(g, 1e-6)


def _model_poly(geom: str, cx, cy, th, hx, hy, r=0.0, poly=None) -> np.ndarray:
    """模型的外緣多邊形（影像座標）。"""
    if geom == "polygon" and poly is not None:
        return np.asarray(poly, np.float64)
    if geom == "ellipse":
        n = int(np.clip(2 * math.pi * max(hx, hy) / 2, 48, 360))
        ph = np.linspace(0, 2 * math.pi, n, endpoint=False)
        u, v = hx * np.cos(ph), hy * np.sin(ph)
    elif geom == "rounded_rect" and r > 0.05:
        r = min(r, hx, hy)
        k = int(np.clip(r, 4, 32))
        us, vs = [], []
        for (sx, sy, a0) in ((-1, -1, math.pi), (1, -1, 1.5 * math.pi), (1, 1, 0.0), (-1, 1, 0.5 * math.pi)):
            ph = np.linspace(a0, a0 + math.pi / 2, k)
            us.append(sx * (hx - r) + r * np.cos(ph))
            vs.append(sy * (hy - r) + r * np.sin(ph))
        u, v = np.concatenate(us), np.concatenate(vs)
    else:
        u = np.array([-hx, hx, hx, -hx])
        v = np.array([-hy, -hy, hy, hy])
    c, s = math.cos(th), math.sin(th)
    return np.stack([cx + u * c - v * s, cy + u * s + v * c], axis=1)


def _raster_poly(poly: np.ndarray, X0: float, Y0: float, w: int, h: int, ss: int = 1) -> np.ndarray:
    """把多邊形（連續座標）畫成覆蓋率（0–1，float32）；ss > 1 時超取樣後面積平均。"""
    img = np.zeros((h * ss, w * ss), np.uint8)
    p = (np.asarray(poly, np.float64) - [X0, Y0]) * ss - 0.5
    pts = np.round(p * 16).astype(np.int32)
    cv2.fillPoly(img, [pts], 255, lineType=cv2.LINE_8, shift=4)
    cov = img.astype(np.float32) / 255.0
    if ss > 1:
        cov = cv2.resize(cov, (w, h), interpolation=cv2.INTER_AREA)
    return cov


def _raster_full(poly: np.ndarray, H: int, W: int, ss: int = 4) -> np.ndarray:
    """多邊形畫成整張影像大小的 bool 遮罩（面積覆蓋率 ≥ 0.5），只在外接框內超取樣。"""
    out = np.zeros((H, W), bool)
    poly = np.asarray(poly, np.float64)
    x0, y0 = np.floor(poly.min(0)).astype(int) - 1
    x1, y1 = np.ceil(poly.max(0)).astype(int) + 1
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, W), min(y1, H)
    if x1 <= x0 or y1 <= y0:
        return out
    out[y0:y1, x0:x1] = _raster_poly(poly, x0, y0, x1 - x0, y1 - y0, ss) > 0.5
    return out


def _seg_dist(pts: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """點到封閉多邊形各邊的最短距離。"""
    a = poly
    b = np.roll(poly, -1, axis=0)
    ab = b - a
    L2 = np.maximum((ab**2).sum(1), _EPS)
    ap = pts[:, None, :] - a[None, :, :]
    t = np.clip((ap * ab[None]).sum(2) / L2[None], 0, 1)
    proj = a[None] + t[..., None] * ab[None]
    return np.sqrt(((pts[:, None, :] - proj) ** 2).sum(2)).min(1)


def _offset_poly(poly: np.ndarray, d: float) -> np.ndarray:
    """多邊形各邊向外平移 d（頂點沿角平分線移動）。"""
    P = np.asarray(poly, np.float64)
    n = len(P)
    if n < 3 or d == 0:
        return P
    area = 0.5 * np.sum(P[:, 0] * np.roll(P[:, 1], -1) - np.roll(P[:, 0], -1) * P[:, 1])
    sgn = 1.0 if area > 0 else -1.0  # 影像座標（y 向下）中 area > 0 為順時針
    e = np.roll(P, -1, axis=0) - P
    e /= np.maximum(np.linalg.norm(e, axis=1, keepdims=True), _EPS)
    nrm = sgn * np.stack([e[:, 1], -e[:, 0]], axis=1)  # 向外法向量
    n_prev = np.roll(nrm, 1, axis=0)
    bis = nrm + n_prev
    denom = np.maximum(1.0 + (nrm * n_prev).sum(1), 0.2)
    return P + d * bis / denom[:, None]


def _cov_std(res) -> np.ndarray:
    J = res.jac
    n, p = J.shape
    s2 = float((res.fun**2).sum()) / max(n - p, 1)
    try:
        cov = np.linalg.pinv(J.T @ J) * s2
        return np.sqrt(np.abs(np.diag(cov)))
    except np.linalg.LinAlgError:
        return np.full(p, np.inf)


def _fit_mask(mask: np.ndarray, bad: np.ndarray, P: dict, tol: float, allow_line: bool = True, line_thick: float = 3.0, allowed: Optional[np.ndarray] = None,
              tips=None) -> Optional[_Fit]:
    """對局部遮罩的外輪廓做由簡到繁的幾何擬合（座標為局部像素，像素中心框架）。

    tips：疊在照片上的形狀才給（一個函式：多邊形頂點 → 補回的尖角裡「像形狀顏色」的像素比例）。JPEG 會把銳角整塊
    （沿 8×8 區塊的邊）削掉，Douglas–Peucker 就多出幾段短邊；這時試著把短邊當成被削掉的尖角補回去（_refine_polygon 的 chamfer），
    尖角附近被破壞的輪廓點不算誤差，而且補回的尖角裡大多要是形狀的顏色，才採用。"""
    m8 = mask.astype(np.uint8)
    cs, _ = cv2.findContours(m8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cs:
        return None
    cnt = max(cs, key=lambda c: (cv2.contourArea(c), len(c)))
    if len(cnt) < 4:
        return None
    pts_all = cnt[:, 0, :].astype(np.float64) + 0.5
    pi = cnt[:, 0, :]
    ok = ~bad[pi[:, 1], pi[:, 0]]
    occl = 1.0 - float(ok.mean())
    pts = pts_all[ok] if ok.sum() >= max(12, 0.25 * len(ok)) else pts_all
    nmax = int(P["max_fit_points"])
    if len(pts) > nmax:
        pts = pts[np.linspace(0, len(pts) - 1, nmax).astype(int)]
    filled = _fill_holes(mask)
    h, w = mask.shape
    valid_area = ~bad

    may = filled | bad | (allowed if allowed is not None else False)

    def _iou(poly):
        R = _raster_poly(poly, 0, 0, w, h, 4) > 0.5
        inter = (R & filled & valid_area).sum()
        uni = ((R | filled) & valid_area).sum()
        # 補全只能延伸到遮擋物底下，不能伸進背景：模型落在「遮罩∪遮擋物」內的比例
        prec = float((R & may).sum()) / max(float(R.sum()), 1.0)
        return min(float(inter) / max(float(uni), 1.0), prec)

    # ---- 初值：最小外接矩形 ----
    (rcx, rcy), (rw, rh), rang = cv2.minAreaRect(pts_all.astype(np.float32))
    th0, hx0, hy0 = _norm_angle(math.radians(rang), rw / 2, rh / 2)
    fits: dict[str, _Fit] = {}
    # ---- 矩形（半徑 0 的圓角矩形） ----
    try:
        res = optimize.least_squares(lambda q: _rrect_sdf(q, pts, 0.0), [rcx, rcy, th0, hx0, hy0], loss="soft_l1", f_scale=1.0, max_nfev=200)
        th, hx, hy = _norm_angle(res.x[2], res.x[3], res.x[4])
        d = np.abs(_rrect_sdf([res.x[0], res.x[1], th, hx, hy], pts, 0.0))
        sd = _cov_std(res)
        f = _Fit("rect", res.x[0], res.x[1], th, hx, hy, 0.0, err_mean=float(d.mean()), err_p95=float(np.percentile(d, 95)))
        f.std = {"cx": sd[0], "cy": sd[1], "th": sd[2], "hx": sd[3], "hy": sd[4], "r": 0.0}
        f.iou = _iou(_model_poly("rect", f.cx, f.cy, f.th, f.hx + 0.5, f.hy + 0.5))
        fits["rect"] = f
    except Exception:  # noqa: BLE001 - 擬合失敗就跳過此模型
        pass
    rect = fits.get("rect")
    # ---- 直線 ----
    if allow_line and rect is not None:
        thick = 2 * min(rect.hx, rect.hy) + 1.0
        length = 2 * max(rect.hx, rect.hy) + 1.0
        if thick <= line_thick + 0.5 and length >= float(P["line_min_aspect"]) * thick:
            f = _Fit("line", rect.cx, rect.cy, rect.th, rect.hx, rect.hy, 0.0, err_mean=rect.err_mean, err_p95=rect.err_p95)
            f.std, f.iou = rect.std, max(rect.iou, 0.95)
            f.accepted = True
            f.candidates = [("line", rect.err_mean)]
            f.confidence = float(np.clip(1.0 - 0.3 * rect.err_mean / tol, 0.3, 0.95))
            return f
    # ---- 圓角矩形 ----
    if rect is not None:
        best = None
        for frac in (0.15, 0.5, 0.95):
            r0 = frac * min(rect.hx, rect.hy)
            try:
                res = optimize.least_squares(lambda q: _rrect_sdf(q, pts), [rect.cx, rect.cy, rect.th, rect.hx, rect.hy, r0], loss="soft_l1", f_scale=1.0, max_nfev=300)
            except Exception:  # noqa: BLE001
                continue
            if best is None or res.cost < best.cost:
                best = res
        if best is not None:
            th, hx, hy = _norm_angle(best.x[2], best.x[3], best.x[4])
            r = float(min(max(best.x[5], 0.0), hx, hy))
            d = np.abs(_rrect_sdf([best.x[0], best.x[1], th, hx, hy, r], pts))
            sd = _cov_std(best)
            f = _Fit("rounded_rect", best.x[0], best.x[1], th, hx, hy, r, err_mean=float(d.mean()), err_p95=float(np.percentile(d, 95)))
            f.std = {"cx": sd[0], "cy": sd[1], "th": sd[2], "hx": sd[3], "hy": sd[4], "r": sd[5]}
            f.iou = _iou(_model_poly("rounded_rect", f.cx, f.cy, f.th, f.hx + 0.5, f.hy + 0.5, f.r + 0.5))
            fits["rounded_rect"] = f
    # ---- 橢圓 ----
    if len(pts) >= 6:
        try:
            (ecx, ecy), (ew, eh), eang = cv2.fitEllipse(pts.astype(np.float32))
            x0 = [ecx, ecy, math.radians(eang), max(ew / 2, 1.0), max(eh / 2, 1.0)]
            res = optimize.least_squares(lambda q: _ell_sampson(q, pts), x0, loss="soft_l1", f_scale=1.0, max_nfev=300)
            th, a, b = _norm_angle(res.x[2], res.x[3], res.x[4])
            poly = _model_poly("ellipse", res.x[0], res.x[1], th, a, b)
            d = _seg_dist(pts, poly)
            sd = _cov_std(res)
            f = _Fit("ellipse", res.x[0], res.x[1], th, a, b, err_mean=float(d.mean()), err_p95=float(np.percentile(d, 95)))
            f.std = {"cx": sd[0], "cy": sd[1], "th": sd[2], "hx": sd[3], "hy": sd[4]}
            f.iou = _iou(_model_poly("ellipse", f.cx, f.cy, f.th, f.hx + 0.5, f.hy + 0.5))
            fits["ellipse"] = f
        except Exception:  # noqa: BLE001
            pass
    # ---- 多邊形 ----
    eps = max(1.0, float(P["poly_eps_k"]) * tol)
    approx = cv2.approxPolyDP(cnt, eps, True)[:, 0, :].astype(np.float64) + 0.5
    if 3 <= len(approx) <= int(P["max_polygon_vertices"]):
        d = _seg_dist(pts, approx)
        f = _Fit("polygon", poly=approx, err_mean=float(d.mean()), err_p95=float(np.percentile(d, 95)))
        f.iou = _iou(_offset_poly(approx, 0.5))
        fits["polygon"] = f

    if not fits:
        return None
    cand = sorted(((k, v.err_mean) for k, v in fits.items()), key=lambda kv: kv[1])
    # ---- 由簡到繁選模型 ----
    choice = None
    if rect is not None:
        choice = rect
        rr = fits.get("rounded_rect")
        if rr is not None and rr.r >= float(P["min_corner_radius_px"]) and rr.r >= 2.0 * rr.std.get("r", 0.0) and rr.err_mean < float(P["rrect_gain"]) * rect.err_mean:
            choice = rr
    ell = fits.get("ellipse")
    if ell is not None and (choice is None or ell.err_mean <= float(P["ellipse_pref"]) * choice.err_mean) and (choice is None or choice.geometry != "rect" or ell.err_mean < rect.err_mean):
        choice = ell

    def _ok(f: _Fit, k: float = 1.0) -> bool:
        return f.err_mean <= float(P["accept_mean_k"]) * tol * k and f.err_p95 <= float(P["accept_p95_k"]) * tol * k and f.iou >= float(P["min_overlap"]) - (0.02 if k > 1 else 0.0)

    def _tip_poly() -> Optional[_Fit]:
        """補回被削掉尖角的多邊形（見 docstring 的 tips）；不成就回傳 None。
        削掉的尖角會讓輪廓多出鋸齒，所以試三種簡化的容許誤差，取過關而且頂點最少的。"""
        base_eps = max(1.0, float(P["poly_eps_k"]) * tol)
        best_f: Optional[_Fit] = None
        for k_eps in (1.0, 2.0, 3.0):
            a2 = cv2.approxPolyDP(cnt, base_eps * k_eps, True)[:, 0, :].astype(np.float64) + 0.5
            if not 3 <= len(a2) <= 2 * int(P["max_polygon_vertices"]):
                continue
            r = _refine_polygon(pts_all, ok, a2, tol, chamfer=float(P["overlay_tip_chamfer"]))
            if r is None:
                continue
            verts, tip_list = r
            if not tip_list or len(verts) > int(P["max_polygon_vertices"]):
                continue
            keep = np.ones(len(pts), bool)
            for v, cut in tip_list:  # 尖角附近的輪廓點被壓縮破壞，不算誤差
                keep &= np.linalg.norm(pts - v, axis=1) > max(6.0, 2.0 * cut)
            if keep.sum() < 0.5 * len(pts):
                continue
            d = _seg_dist(pts[keep], verts)
            f = _Fit("polygon", poly=verts, err_mean=float(d.mean()), err_p95=float(np.percentile(d, 95)))
            f.iou = _iou(_offset_poly(verts, 0.5))
            if not _ok(f) or tips(verts) < float(P["overlay_tip_color_frac"]):
                continue
            f.std = {"tips_recovered": float(len(tip_list))}
            if best_f is None or (len(verts), f.err_mean) < (len(best_f.poly), best_f.err_mean):
                best_f = f
        return best_f

    if choice is None or not _ok(choice):
        pg = fits.get("polygon")
        tp = None
        if pg is not None and _ok(pg):
            choice = pg
            ref = _refine_polygon(pts_all, ok, pg.poly, tol)
            if ref is not None:
                d = _seg_dist(pts, ref)
                if float(d.mean()) <= 1.2 * pg.err_mean + 0.1:
                    pg.poly, pg.err_mean, pg.err_p95 = ref, float(d.mean()), float(np.percentile(d, 95))
            if tips is not None and len(pg.poly) > 3:
                tp = _tip_poly()
                if tp is not None and len(tp.poly) < len(pg.poly):
                    fits["polygon"] = choice = tp
        elif tips is not None and (tp := _tip_poly()) is not None:
            fits["polygon"] = choice = tp
        else:
            choice = choice or fits[cand[0][0]]
            choice.accepted = False
            choice.candidates = cand
            choice.confidence = 0.0
            choice.std = dict(choice.std, occluded_frac=occl)
            return choice
    choice.accepted = True
    choice.candidates = cand
    def _distinct(k: str) -> bool:
        if k == choice.geometry:
            return False
        f = fits[k]
        if k == "polygon":  # 多邊形只是退路，不拿來衡量基本幾何之間的歧義
            return choice.geometry == "polygon"
        if k == "rounded_rect" and choice.geometry == "rect":
            return f.r >= float(P["min_corner_radius_px"])
        if k == "rounded_rect" and choice.geometry == "ellipse":
            return f.r < 0.9 * min(f.hx, f.hy) or abs(f.hx - f.hy) > 1.0
        return True

    others = [e for k, e in cand if _distinct(k)]
    gap = (min(others) - choice.err_mean) / max(min(others), _EPS) if others else 1.0
    q = float(np.clip(1.0 - 0.4 * choice.err_mean / tol, 0.2, 1.0))
    choice.confidence = float(np.clip(q * (0.55 + 0.45 * np.clip(gap * 2.0, 0, 1)) * (1.0 - 0.5 * occl), 0.05, 0.98))
    choice.std = dict(choice.std, occluded_frac=occl)
    return choice


def _tip_color_check(I: np.ndarray, mask: np.ndarray, bad: np.ndarray, F: np.ndarray):
    """給 _fit_mask 的 tips：多邊形（局部座標）比遮罩多出來的部分（補回的尖角）裡，顏色比較接近形狀色 F、
    而不是形狀外圍照片中位色的像素比例。真的被壓縮削掉的尖角裡仍是形狀的顏色（加上振鈴）；本來就是斜角的形狀，
    多出來的地方是照片，比例很低。"""
    h, w = mask.shape
    ring = _dilate(mask, 6) & ~_dilate(mask, 3) & ~bad
    O = np.median(I[ring], axis=0) if ring.sum() >= 8 else None

    def _frac(verts: np.ndarray) -> float:
        if O is None:
            return 0.0
        R = _raster_poly(verts, 0, 0, w, h, 2) > 0.5
        add = R & ~_dilate(mask, 1)
        if add.sum() < 4:
            return 1.0
        px = I[add]
        return float((np.linalg.norm(px - F, axis=1) < np.linalg.norm(px - O, axis=1)).mean())

    return _frac


def _refine_polygon(P_all: np.ndarray, ok: np.ndarray, V: np.ndarray, tol: float, chamfer: float = 0.0):
    """Douglas–Peucker 的頂點只落在輪廓點上：反鋸齒會把銳角削鈍，在尖端留下兩三個靠得很近的頂點。
    這裡每條邊各自以直線擬合（去掉靠近兩端的點，只用沒被遮住的點），頂點改取相鄰兩條直線的交點；
    太短的邊（小於 max(4, 3 × 容許偏差)）拿掉，讓兩旁的邊直接相交。交點離原頂點太遠（幾乎平行）就保留原頂點。
    chamfer > 0（疊在照片上的形狀才用）：比相鄰的長邊短得多（不到 chamfer 倍）的邊，當成被壓縮削掉的尖角，也拿掉，
    兩旁的邊相交處容許離原頂點遠一點（削掉的長度的 3 倍）。這時回傳（頂點、補回的尖角 [(頂點, 削掉的長度)]），
    尖角是否真的屬於形狀由呼叫端看顏色決定。
    回傳精修後的頂點（與輪廓同一個座標框架）；做不到時回傳 None。"""
    n0 = len(V)
    N = len(P_all)
    if n0 < 3 or N < 3 * n0:
        return None
    idx = sorted(int(np.argmin(((P_all - v) ** 2).sum(axis=1))) for v in V)
    if len(set(idx)) < 3:
        return None
    edges = [(idx[i], idx[(i + 1) % len(idx)]) for i in range(len(idx))]  # 每條邊在輪廓上的範圍（含兩端）
    min_len = max(4.0, 3.0 * tol)
    far = max(6.0, 4.0 * tol)

    def _line(a: int, b: int):
        ii = np.arange(a, b + 1) if b > a else np.r_[np.arange(a, N), np.arange(0, b + 1)]
        seg, sok = P_all[ii], ok[ii]
        ends = np.minimum(np.linalg.norm(seg - P_all[a], axis=1), np.linalg.norm(seg - P_all[b], axis=1))
        use = (ends >= 2.0) & sok
        if use.sum() < 3:
            use = ends >= 2.0
        if use.sum() < 3:
            c, d = (P_all[a] + P_all[b]) / 2, P_all[b] - P_all[a]
        else:
            q = seg[use]
            c = q.mean(axis=0)
            d = np.linalg.svd(q - c, full_matrices=False)[2][0]
        nd = float(np.hypot(*d))
        return c, (d / nd if nd > _EPS else np.array([1.0, 0.0]))

    slack: dict[int, float] = {}  # 邊的起點（輪廓序號）→ 因為削掉尖角而容許多遠

    def _vertices(lines, base, starts):
        out = []
        for i in range(len(lines)):
            (c1, d1), (c2, d2) = lines[i - 1], lines[i]
            den = d1[0] * d2[1] - d1[1] * d2[0]
            v0 = base[i]
            if abs(den) < 1e-3:
                out.append(v0)
                continue
            t = ((c2[0] - c1[0]) * d2[1] - (c2[1] - c1[1]) * d2[0]) / den
            v = c1 + t * d1
            out.append(v if np.hypot(*(v - v0)) <= far + slack.get(starts[i], 0.0) else v0)
        return np.array(out)

    while True:
        lines = [_line(a, b) for a, b in edges]
        starts = [a for a, _ in edges]
        verts = _vertices(lines, P_all[starts], starts)
        if len(edges) <= 3:
            break
        # 幾乎共線的頂點（離前後兩頂點連線不到容許偏差）：兩條邊併成一條（邊界對比低的地方輪廓不平，會多出這種頂點）
        prev, nxt = np.roll(verts, 1, axis=0), np.roll(verts, -1, axis=0)
        d = nxt - prev
        dev = np.abs(d[:, 0] * (verts[:, 1] - prev[:, 1]) - d[:, 1] * (verts[:, 0] - prev[:, 0])) / np.maximum(np.hypot(d[:, 0], d[:, 1]), _EPS)
        k = int(np.argmin(dev))
        if dev[k] < tol:
            edges[k - 1] = (edges[k - 1][0], edges[k][1])
            del edges[k]
            continue
        L = np.linalg.norm(nxt - verts, axis=1)
        k = int(np.argmin(L))
        if chamfer > 0 and L[k] >= min_len:
            nb = np.maximum(np.roll(L, 1), np.roll(L, -1))  # 尖角常被削成兩三段短邊：拿較長的鄰邊比
            rel = L / np.maximum(nb, _EPS)
            k = int(np.argmin(rel))
            if rel[k] >= chamfer:
                break
        elif L[k] >= min_len:
            break
        if chamfer > 0:  # 被削掉的尖角：相交處容許離原頂點遠一點
            nxt_start = edges[(k + 1) % len(edges)][0]
            slack[nxt_start] = slack.get(nxt_start, 0.0) + slack.get(edges[k][0], 0.0) + 3.0 * float(L[k])
        del edges[k]  # 拿掉太短的邊：前後兩條邊直接相交
    if chamfer > 0:
        starts = [a for a, _ in edges]
        tips = [(verts[i], slack[a] / 3.0) for i, a in enumerate(starts) if slack.get(a, 0.0) > 0]
        return verts, tips
    return verts


def _est(value: float, err: float, rel_scale: float = 1.0) -> Estimate:
    err = float(abs(err)) if np.isfinite(err) else 99.0
    conf = float(np.clip(1.0 - err / max(abs(value) * rel_scale, 1.0), 0.05, 0.98))
    return Estimate(float(value), err, conf)


def _make_shapefit(P: dict, fit: _Fit, offset, fill=None, stroke=None, stroke_w: Optional[Estimate] = None, outline_only=False) -> ShapeFit:
    ox, oy = offset
    est: dict[str, Estimate] = {}
    k_std = float(P["std_inflate"])  # 相鄰輪廓點高度相關，共變異數低估誤差
    if fit.geometry == "polygon":
        poly = _offset_poly(fit.poly, 0.5) + [ox, oy]
        (cx, cy), (rw, rh), ang = cv2.minAreaRect(poly.astype(np.float32))
        th, hx, hy = _norm_angle(math.radians(ang), rw / 2, rh / 2)
        quad = _model_poly("rect", cx, cy, th, hx, hy)
        sf = ShapeFit("polygon", quad, fill=fill, stroke=stroke, polygon=poly, fit_error_px=fit.err_mean)
        est["width_px"] = _est(2 * hx, 1.0)
        est["height_px"] = _est(2 * hy, 1.0)
    else:
        cx, cy = fit.cx + ox, fit.cy + oy
        hx, hy, r = fit.hx + 0.5, fit.hy + 0.5, (fit.r + 0.5 if fit.geometry == "rounded_rect" else 0.0)
        sw = stroke_w.value if (stroke_w is not None and fit.geometry != "line") else 0.0
        if sw > 0:  # 有外框線：quad 取線的中心線
            hx, hy, r = hx - sw / 2, hy - sw / 2, max(r - sw / 2, 0.0)
        s = fit.std
        th_unobs = fit.geometry == "ellipse" and abs(fit.hx - fit.hy) <= max(0.75, 0.01 * max(fit.hx, fit.hy))
        if th_unobs:  # 近乎正圓：旋轉角不可觀測，取 0 並回報大誤差
            fit.th = 0.0
            s = dict(s, th=math.radians(22.5))
        est["width_px"] = _est(2 * hx, max(2 * k_std * s.get("hx", 0.0), 0.35))
        est["height_px"] = _est(2 * hy, max(2 * k_std * s.get("hy", 0.0), 0.35))
        est["angle_deg"] = Estimate(math.degrees(fit.th), max(math.degrees(k_std * s.get("th", 0.0)), 0.05), float(np.clip(1.0 - math.degrees(k_std * s.get("th", 0.0)) / 2.0, 0.05, 0.98)))
        est["center_x_px"] = _est(cx, max(k_std * s.get("cx", 0.0), 0.25), 0)
        est["center_y_px"] = _est(cy, max(k_std * s.get("cy", 0.0), 0.25), 0)
        if fit.geometry == "line":
            c, sn = math.cos(fit.th), math.sin(fit.th)
            if fit.hy > fit.hx:  # 長軸在 v 方向
                c, sn = -sn, c
            L = max(fit.hx, fit.hy) + 0.5
            p0 = np.array([cx - L * c, cy - L * sn])
            p1 = np.array([cx + L * c, cy + L * sn])
            if (p1[0], p1[1]) < (p0[0], p0[1]) and abs(p1[0] - p0[0]) > abs(p1[1] - p0[1]) or (abs(p1[0] - p0[0]) <= abs(p1[1] - p0[1]) and p1[1] < p0[1]):
                p0, p1 = p1, p0
            quad = np.stack([p0, p1, p1, p0])
            width = stroke_w.value if stroke_w is not None else 2 * min(fit.hx, fit.hy) + 1.0
            sf = ShapeFit("line", quad, fill=None, stroke=stroke if stroke is not None else fill, stroke_width_px=float(width), fit_error_px=fit.err_mean)
            est["length_px"] = _est(2 * L, max(2 * k_std * max(s.get("hx", 0.0), s.get("hy", 0.0)), 0.5))
            est.pop("width_px", None)
            est.pop("height_px", None)
            if stroke_w is not None:
                est["stroke_width_px"] = stroke_w
        else:
            quad = _model_poly("rect", cx, cy, fit.th, hx, hy)
            sf = ShapeFit(fit.geometry, quad, fill=fill, stroke=stroke, fit_error_px=fit.err_mean)
            if fit.geometry == "rounded_rect":
                sf.corner_radius_px = float(r)
                # 真實設計的圓角常是貝茲近似或超橢圓，不是正圓弧：加上相對的模型誤差下限
                est["corner_radius_px"] = _est(r, max(k_std * s.get("r", 0.0), float(P["radius_err_floor_px"]), float(P["radius_model_rel_err"]) * r))
            if stroke_w is not None and sw > 0:
                sf.stroke_width_px = float(sw)
                est["stroke_width_px"] = stroke_w
    sf.confidence = fit.confidence
    sf.candidates = [(k, float(e)) for k, e in fit.candidates]
    sf.estimates = est
    if outline_only:
        sf.fill = None
    return sf


def _shapefit_outer_poly(sf: ShapeFit) -> np.ndarray:
    """由 ShapeFit 重建外緣多邊形（有外框線時向外補半個線寬）。"""
    q = np.asarray(sf.quad, np.float64)
    if sf.geometry == "polygon" and sf.polygon is not None:
        return np.asarray(sf.polygon, np.float64)
    cx, cy = q.mean(axis=0)
    hx = np.linalg.norm(q[1] - q[0]) / 2
    hy = np.linalg.norm(q[3] - q[0]) / 2
    th = math.atan2(q[1, 1] - q[0, 1], q[1, 0] - q[0, 0])
    if sf.geometry == "line":
        w = (sf.stroke_width_px or 1.0) / 2
        return _model_poly("rect", cx, cy, th, hx, w)
    sw = (sf.stroke_width_px or 0.0) / 2 if sf.stroke is not None else 0.0
    return _model_poly(sf.geometry, cx, cy, th, hx + sw, hy + sw, (sf.corner_radius_px or 0.0) + sw)


# =====================================================================================
# 候選區塊
# =====================================================================================


@dataclass(eq=False)
class _Cand:
    id: int
    source: str  # "flat"、"texture"
    box: tuple[int, int, int, int]  # 局部框 X0, Y0, X1, Y1
    vis: np.ndarray  # 局部 bool：看得到、屬於此候選的像素
    color: np.ndarray  # 中位色（filled 影像）
    spread: float = 0.0
    grad_amp: float = 0.0
    role: str = ""  # shadow、line、ring、photo、illus、shape、complex、merged、consumed
    parent: int = -1
    feats: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


def _crop_bool(full: np.ndarray, box) -> np.ndarray:
    X0, Y0, X1, Y1 = box
    return full[Y0:Y1, X0:X1]


def _paste(full_shape, box, local) -> np.ndarray:
    out = np.zeros(full_shape, bool)
    X0, Y0, X1, Y1 = box
    out[Y0:Y1, X0:X1] = local
    return out


def _expand_box(box, m, H, W):
    x0, y0, x1, y1 = box
    return max(0, x0 - m), max(0, y0 - m), min(W, x1 + m), min(H, y1 + m)


def _refine_flat(core_full_lab: np.ndarray, k: int, box, filled, smooth, unknown, F, P, sigma, pred=None, fg_thr: float = 14.0):
    """以混色模型精修平坦區塊的外緣，回傳（局部框、可見遮罩、已解釋遮罩）。"""
    H, W = unknown.shape
    band = int(P["edge_band_px"])
    bx = _expand_box(box, 2 * band + 5, H, W)
    X0, Y0, X1, Y1 = bx
    core = core_full_lab[Y0:Y1, X0:X1] == k
    I = filled[Y0:Y1, X0:X1]
    dist = cv2.distanceTransform((~core).astype(np.uint8), cv2.DIST_L2, 3)
    zone = (dist > 0) & (dist <= 2 * band)
    ref = (dist > 2 * band) & (dist <= 2 * band + 3) & smooth[Y0:Y1, X0:X1] & ~unknown[Y0:Y1, X0:X1]
    if ref.sum() < 4:
        ref = dist > 2 * band
    if not ref.any():
        return bx, core, core.copy()
    O = _nearest_fill_local(I, ref)
    D = F[None, None, :] - O
    den = (D * D).sum(2)
    a_raw = ((I - O) * D).sum(2) / np.maximum(den, _EPS)
    alpha = np.clip(a_raw, 0.0, 1.0)
    # 混色殘差：到「O 與 F 連線」的距離；沿連線方向的小幅超出（JPEG 振鈴、銳化）也算可解釋
    over = np.abs(a_raw - alpha) * np.sqrt(den)
    resid = np.linalg.norm(I - (O + a_raw[..., None] * D), axis=2) + np.maximum(over - float(P["ringing_max"]), 0.0)
    lowc = den < float(P["contrast_min"]) ** 2
    mix_tol = max(float(P["mix_tol"]), float(P["mix_noise_k"]) * sigma)
    # 只收「看得出是區塊色與外側色混色」的像素；顏色不同的外框線、相鄰物件不併入
    take = np.where(lowc, dist <= max(1.0, band / 2.0), (alpha >= 0.5) & (resid <= 2.0 * mix_tol))
    vis = core | (zone & take)
    # 只保留與核心相連的部分
    n, lab = cv2.connectedComponents(vis.astype(np.uint8), connectivity=8)
    if n > 2:
        keep = np.unique(lab[core])
        vis = np.isin(lab, keep[keep > 0])
    # 已解釋：混色殘差小，而且含有區塊色成分（α > 0.1）或外側本來就是背景（背景側的振鈴）
    o_is_bg = np.linalg.norm(O - pred[Y0:Y1, X0:X1], axis=2) <= fg_thr if pred is not None else np.ones(core.shape, bool)
    expl = core | (zone & (resid <= mix_tol) & ~lowc & ((a_raw > 0.1) | o_is_bg)) | (zone & lowc & (dist <= band))
    return bx, vis, expl


def _refine_texture(vis: np.ndarray, I: np.ndarray, fg_loc: np.ndarray, P: dict) -> np.ndarray:
    """紋理區塊（照片、細線）的外緣精修：平滑後的前景會向外擴，改以「內側色與外側色的混色」α ≥ 0.5 取界。"""
    out_ref = ~_dilate(vis, 1) & ~fg_loc
    if out_ref.sum() < 4 or not vis.any():
        return vis
    dist_in = cv2.distanceTransform(np.pad(vis, 1).astype(np.uint8), cv2.DIST_L2, 3)[1:-1, 1:-1]
    core = vis & (dist_in >= 3)
    if core.sum() < 0.3 * vis.sum():  # 細長：以對比找核心
        O0 = np.median(I[out_ref], axis=0)
        dd = np.linalg.norm(I - O0, axis=2)
        c95 = float(np.percentile(dd[vis], 95))
        core = vis & (dd >= 0.6 * c95)
    if not core.any():
        return vis
    zone = vis & ~core
    F = _nearest_fill_local(I, core)
    O = _nearest_fill_local(I, out_ref)
    D = F - O
    den = (D * D).sum(2)
    alpha = ((I - O) * D).sum(2) / np.maximum(den, _EPS)
    lowc = den < float(P["contrast_min"]) ** 2
    nearer = np.linalg.norm(I - F, axis=2) < np.linalg.norm(I - O, axis=2)
    keep = np.where(lowc, nearer & (dist_in >= 1.0), alpha >= 0.5)
    out = core | (zone & keep)
    n, lab = cv2.connectedComponents(out.astype(np.uint8), connectivity=8)
    if n > 2:
        ids = np.unique(lab[core])
        out = np.isin(lab, ids[ids > 0])
    return out


def _thickness(mask: np.ndarray) -> float:
    if not mask.any():
        return 0.0
    dt = cv2.distanceTransform(np.pad(mask, 1).astype(np.uint8), cv2.DIST_L2, 3)
    return float(2.0 * dt.max())


def _color_count(px: np.ndarray, bin_w: float, cover: float) -> int:
    if len(px) == 0:
        return 0
    best = None
    for off in (0.0, bin_w / 2):
        q = np.floor((px + off) / bin_w).astype(np.int64)
        keys = q[:, 0] * 1_000_000 + q[:, 1] * 1000 + q[:, 2]
        _, cnt = np.unique(keys, return_counts=True)
        cnt = np.sort(cnt)[::-1]
        n = int(np.searchsorted(np.cumsum(cnt) / len(px), cover) + 1)
        best = n if best is None else min(best, n)
    return int(best)


# 【替換點 B】照片／插畫分類器：輸入局部影像與遮罩，輸出照片分數（0–1）與特徵。
def _classify_texture(I: np.ndarray, m: np.ndarray, grad: np.ndarray, lap: np.ndarray, g_thr: float, sigma: float, P: dict) -> tuple[float, dict]:
    er = cv2.erode(m.astype(np.uint8), _disk(1)).astype(bool)
    use = er if er.sum() >= 30 else m
    px = I[use]
    if len(px) > 60000:
        px = px[np.linspace(0, len(px) - 1, 60000).astype(int)]
    bin_w = max(float(P["color_bin"]), 3.0 * sigma)
    ncol = _color_count(px, bin_w, float(P["color_cover"]))
    flat = float((grad[use] < g_thr).mean()) if use.any() else 0.0
    hf = _sigma_from_lap(lap[use]) / max(sigma, 0.5)
    s = (
        float(P["photo_w_colors"]) * _ramp(ncol, float(P["photo_colors_lo"]), float(P["photo_colors_hi"]))
        + float(P["photo_w_flat"]) * (1.0 - _ramp(flat, float(P["photo_flat_lo"]), float(P["photo_flat_hi"])))
        + float(P["photo_w_hf"]) * _ramp(hf, float(P["photo_hf_lo"]), float(P["photo_hf_hi"]))
    )
    wsum = float(P["photo_w_colors"]) + float(P["photo_w_flat"]) + float(P["photo_w_hf"])
    return s / max(wsum, _EPS), {"n_colors": float(ncol), "flat_ratio": flat, "hf_ratio": float(hf)}


# =====================================================================================
# 疊在照片上的平塗形狀
# =====================================================================================

_GRAIN_K = 1.25  # 高通殘差絕對值的中位數換成「約等於雜訊標準差」的倍數（常態分布的平均絕對值約 0.8σ）


class _OverlayContext:
    """判斷照片（或有紋理的插畫）裡的平坦色塊，是不是後來疊上去的平塗形狀。

    每一項都是量測值，門檻在 DEFAULT_PARAMS 的 overlay_*：
      1. 面積夠大。
      2. 顆粒：高通殘差（原圖減去高斯平滑）的大小。形狀內部的顆粒 ÷ 照片最平坦處的顆粒（各格顆粒的低百分位）要夠小。
         真照片裡的平坦處（天空、牆）也帶著照片本身的顆粒（感光雜訊、壓縮），比值接近 1；照片本身太乾淨時比不出來，就不拆。
         比的是「照片最平坦處」而不是「緊鄰的周圍」：周圍若是樹葉、建築，紋理本身就大，拿來比會把天空誤判成乾淨。
      3. 顏色均勻：內部跨區的顏色變化（線性擬合的幅度）要小；天空、牆面常有明暗。
      4. 邊緣：沒被文字或其他物件遮住的邊界上，兩側色差夠大、而且銳利（邊緣上的最大梯度 ÷ 兩側色差），
         這樣的點要佔大多數（連續）。模糊的色塊、柔邊的光斑過不了。
    外形（基本幾何的擬合誤差）在之後的擬合步驟判斷；擬合不成時改試描圖（_overlay_trace），都不成就留在照片裡。
    判斷結果與量到的數值寫進候選的 feats（overlay_*），通過的另在 notes 用白話說明依據。
    """

    def __init__(self, filled, unknown, fg, L, cands, P, H, W):
        self.filled, self.unknown, self.fg, self.L, self.cands, self.P, self.H, self.W = filled, unknown, fg, L, cands, P, H, W
        self._page: Optional[float] = None
        self.s = float(P["overlay_hp_sigma"])
        self.m = int(math.ceil(3 * self.s)) + 1  # 高斯平滑的影響範圍：離邊界這麼近的像素不拿來量顆粒
        self.floors: dict = {}
        self.rejects: dict[int, dict[str, int]] = {}  # 照片 id → 沒拆的原因 → 塊數（照片收尾時合併成一句 notes）
        self.flat_all = np.zeros((H, W), bool)
        for c in cands:
            if c.source == "flat":
                X0, Y0, X1, Y1 = c.box
                self.flat_all[Y0:Y1, X0:X1] |= c.vis

    def _hp(self, box, s: Optional[float] = None) -> np.ndarray:
        """框內每個像素的高通殘差大小（各色版的絕對值平均；框外多取一圈避免邊界效應）。s：高通的 σ，預設 overlay_hp_sigma。"""
        s = self.s if s is None else float(s)
        X0, Y0, X1, Y1 = _expand_box(box, int(math.ceil(3 * s)) + 1, self.H, self.W)
        I = self.filled[Y0:Y1, X0:X1]
        r = np.abs(I - cv2.GaussianBlur(I, (0, 0), s)).mean(axis=2)
        x0, y0, x1, y1 = box
        return r[y0 - Y0 : y1 - Y0, x0 - X0 : x1 - X0]

    def floor(self, host: _Cand, s: Optional[float] = None) -> tuple[float, bool]:
        """照片本身的顆粒下限，回傳（下限、是不是取自「本身平坦」的格子）。

        照片可見像素（避開平坦候選與文字）切成格子，每格量兩件事：顆粒（高通殘差的中位數）與低頻起伏
        （σ3 平滑後扣掉平面的穩健標準差）。起伏不超過 overlay_flat_tile_k 倍顆粒的格子是「本身平坦」的地方（天空、
        漸層、牆面），下限取這些格子顆粒的低百分位。照片裡找不到這種格子（整張都是紋理：磚牆、樹葉）時，
        只好取所有格子，但紋理會漏進高通、壓縮又把均勻處的顆粒壓得比紋理處少，下限因此偏高——呼叫端要用更嚴的比值。"""
        s = self.s if s is None else float(s)
        key = (host.id, round(s, 3))
        if key in self.floors:
            return self.floors[key]
        X0, Y0, X1, Y1 = host.box
        a = self._hp(host.box, s)
        valid = host.vis & ~self.unknown[Y0:Y1, X0:X1] & ~_dilate(self.flat_all[Y0:Y1, X0:X1], int(math.ceil(3 * s)) + 1)
        t = max(8, int(self.P["overlay_tile_px"]))
        h, w = a.shape
        ny, nx = h // t, w // t
        f, from_flat = 0.0, False
        if ny * nx >= 4:
            A = a[: ny * t, : nx * t].reshape(ny, t, nx, t).transpose(0, 2, 1, 3).reshape(ny * nx, t * t)
            V = valid[: ny * t, : nx * t].reshape(ny, t, nx, t).transpose(0, 2, 1, 3).reshape(ny * nx, t * t)
            ok = V.mean(axis=1) >= 0.5
            if ok.sum() >= 4:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    med = np.nanmedian(np.where(V[ok], A[ok], np.nan), axis=1) * _GRAIN_K
                # 低頻起伏：σ3 平滑後，每格以平面（線性漸層）擬合，殘差（各色版取大）的穩健標準差
                lp = cv2.GaussianBlur(self.filled[Y0:Y1, X0:X1], (0, 0), 3.0)[: ny * t, : nx * t]
                Lt = lp.reshape(ny, t, nx, t, 3).transpose(0, 2, 1, 3, 4).reshape(ny * nx, t * t, 3)[ok]
                yy, xx = np.mgrid[0:t, 0:t]
                B = np.stack([np.ones(t * t), xx.ravel(), yy.ravel()], 1).astype(np.float32)
                coef = np.einsum("pk,nkc->npc", np.linalg.pinv(B), Lt)
                res = np.abs(Lt - np.einsum("kp,npc->nkc", B, coef)).max(axis=2)
                lf = 1.4826 * np.median(res, axis=1)
                flat = lf <= float(self.P["overlay_flat_tile_k"]) * np.maximum(med, 0.5)
                if flat.sum() >= 4:
                    f, from_flat = float(np.percentile(med[flat], float(self.P["overlay_floor_pct"]))), True
                else:
                    f = float(np.percentile(med, float(self.P["overlay_floor_pct"])))
            elif valid.any():
                f = float(np.median(a[valid])) * _GRAIN_K
        elif valid.any():
            f = float(np.median(a[valid])) * _GRAIN_K
        self.floors[key] = (f, from_flat)
        return f, from_flat

    def page_grain(self) -> float:
        """頁面底色本身的顆粒（掃描、壓縮的雜訊）：取完全是背景的格子量高通殘差，各格的中位數。量不到時回傳 -1。
        文件裡畫上去的形狀和頁面底色一樣乾淨；照片裡的天空、牆面則帶著照片自己的顆粒。"""
        if self._page is not None:
            return self._page
        bgm = ~self.fg & ~self.unknown
        t = max(8, int(self.P["overlay_tile_px"]))
        ny, nx = self.H // t, self.W // t
        self._page = -1.0
        if ny * nx == 0:
            return self._page
        frac = bgm[: ny * t, : nx * t].reshape(ny, t, nx, t).mean(axis=(1, 3))
        cand = np.argwhere(frac >= 0.98)
        if len(cand) < 8:
            return self._page
        pick = cand[np.linspace(0, len(cand) - 1, min(len(cand), 200)).astype(int)]
        vals = []
        for ty, tx in pick:
            box = (int(tx * t), int(ty * t), int(tx * t + t), int(ty * t + t))
            m = bgm[box[1] : box[3], box[0] : box[2]]
            vals.append(float(np.median(self._hp(box)[m])))
        self._page = float(np.median(vals)) * _GRAIN_K
        return self._page

    def _gradient(self, box) -> np.ndarray:
        """框內的梯度大小（Sobel，各色版合成；色階／像素）。"""
        X0, Y0, X1, Y1 = box
        I = self.filled[Y0:Y1, X0:X1]
        gx = cv2.Sobel(I, cv2.CV_32F, 1, 0, ksize=3) / 8.0
        gy = cv2.Sobel(I, cv2.CV_32F, 0, 1, ksize=3) / 8.0
        return np.sqrt((gx * gx + gy * gy).sum(axis=2))

    def _edges(self, c: _Cand, host: _Cand, floor: float) -> tuple[float, float]:
        """回傳（沒被遮住的邊界中「有對比而且銳利」的比例、沒被遮住的邊界佔全部邊界的比例）。"""
        P = self.P
        bx = _expand_box(c.box, 12, self.H, self.W)
        X0, Y0, X1, Y1 = bx
        vis = np.zeros((Y1 - Y0, X1 - X0), bool)
        cx0, cy0, cx1, cy1 = c.box
        vis[cy0 - Y0 : cy1 - Y0, cx0 - X0 : cx1 - X0] = c.vis
        I = self.filled[Y0:Y1, X0:X1]
        Ls = self.L[Y0:Y1, X0:X1]
        other = ((Ls > 0) & (Ls != c.id + 1) & (Ls != host.id + 1)) | self.unknown[Y0:Y1, X0:X1]
        dist_out = cv2.distanceTransform((~vis).astype(np.uint8), cv2.DIST_L2, 3)
        ob = (dist_out >= 3) & (dist_out <= 7) & ~_dilate(other, 2)
        k = 15
        num = cv2.boxFilter(I * ob[..., None].astype(np.float32), -1, (k, k), normalize=False, borderType=cv2.BORDER_CONSTANT)
        den = cv2.boxFilter(ob.astype(np.float32), -1, (k, k), normalize=False, borderType=cv2.BORDER_CONSTANT)
        bpx = vis & ~cv2.erode(vis.astype(np.uint8), np.ones((3, 3), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0).astype(bool)
        occl = _dilate(other, int(P["occlusion_dilate_px"]) + 1)
        use = bpx & ~occl & (den >= 8)
        n_b, n_u = int(bpx.sum()), int(use.sum())
        if n_u == 0:
            return 0.0, 0.0
        O = num[use] / den[use][:, None]
        step = np.linalg.norm(O - np.asarray(c.color, np.float32)[None, :], axis=1)
        G = cv2.dilate(self._gradient(bx), np.ones((5, 5), np.uint8))
        sharp = G[use] / np.maximum(step, _EPS)
        cmin = max(float(P["overlay_edge_contrast"]), 4.0 * floor)
        good = (step >= cmin) & (sharp >= float(P["overlay_edge_sharpness"]))
        return float(good.mean()), n_u / max(n_b, 1)

    def reject(self, host: _Cand, labels: list[str], detail: str) -> None:
        """記一塊沒拆的平坦色塊：照片收尾時合併成一句 notes（summary）。labels 是簡短的原因，detail 是只有一塊時用的完整說明。"""
        rec = self.rejects.setdefault(host.id, {"n": 0, "why": {}, "detail": detail})
        rec["n"] += 1
        for lb in labels:
            rec["why"][lb] = rec["why"].get(lb, 0) + 1

    def summary(self, host_id: int) -> Optional[str]:
        """這張照片（或有紋理的插畫）裡沒拆出來的平坦色塊，合併成一句白話說明；沒有就回傳 None。"""
        rec = self.rejects.get(host_id)
        if not rec:
            return None
        if rec["n"] == 1:
            return "照片裡有一塊平坦色塊沒有拆出來：" + rec["detail"]
        why = "、".join(f"{k} {v} 塊" for k, v in sorted(rec["why"].items(), key=lambda kv: -kv[1]))
        return f"照片裡有 {rec['n']} 塊平坦色塊沒有拆出來，當成照片的內容（{why}；一塊可能有幾個原因）"

    def judge(self, c: _Cand, host: _Cand, min_area: float) -> bool:
        """量證據並判定；通過的在 c.feats 記 overlay=1 與 overlay_host，並在 notes 說明依據。沒過的原因記在 self.rejects。"""
        P = self.P
        X0, Y0, X1, Y1 = c.box
        area = float(c.vis.sum())
        need_area = max(float(P["overlay_min_area_rel"]) * self.H * self.W, 4.0 * min_area)
        fe = {"overlay": 0.0}
        if area < need_area:
            c.feats.update(fe)
            return False  # 太小：照片裡的小色塊，不值得量（也最容易誤判）
        floor, from_flat = self.floor(host)
        known = ~self.unknown[Y0:Y1, X0:X1]
        inner = cv2.erode(c.vis.astype(np.uint8), _disk(self.m), borderType=cv2.BORDER_CONSTANT, borderValue=0).astype(bool) & known
        s_used, floor_cmp, flat_cmp, thin = self.s, floor, from_flat, False
        if inner.sum() < 50:
            # 細長的形狀內縮後剩不到幾個像素：改用較小的高通 σ 與較小的內縮再量；照片的下限也用同一個 σ 重量，才比得起來
            s_used = float(P["overlay_hp_sigma_thin"])
            inner = cv2.erode(c.vis.astype(np.uint8), _disk(int(math.ceil(3 * s_used)) + 1), borderType=cv2.BORDER_CONSTANT, borderValue=0).astype(bool) & known
            if inner.sum() >= 50:
                floor_cmp, flat_cmp = self.floor(host, s_used)
            else:
                thin = True
        grain = float(np.median(self._hp(c.box, s_used)[inner])) * _GRAIN_K if not thin else float("nan")
        ratio = grain / max(floor_cmp, _EPS) if not thin else float("nan")
        limit = float(P["overlay_noise_ratio"]) if flat_cmp else float(P["overlay_noise_ratio_textured"])
        cont, vis_frac = self._edges(c, host, floor)
        page = self.page_grain()
        # 截平：三個色版都是 0（或 255）附近的色塊，可能是照片欠曝（或過曝）到底的地方，內部一樣沒有顆粒、邊緣一樣銳利
        col = np.asarray(c.color, np.float64)
        lo, hi = float(P["overlay_clip_low"]), float(P["overlay_clip_high"])
        clipped = "純黑" if (col <= lo).all() else ("純白" if (col >= hi).all() else "")
        fe.update({"overlay_grain": grain if not thin else -1.0, "overlay_host_grain": floor_cmp, "overlay_grain_ratio": float(min(ratio, 99.0)) if not thin else -1.0,
                   "overlay_ratio_limit": limit, "overlay_floor_flat": float(flat_cmp), "overlay_hp_sigma": s_used, "overlay_page_grain": page,
                   "overlay_color_change": float(c.grad_amp), "overlay_edge_continuity": cont, "overlay_edge_visible": vis_frac, "overlay_clipped": float(bool(clipped))})
        why: list[str] = []
        labels: list[str] = []
        if floor < float(P["overlay_min_texture"]):
            why.append(f"照片本身很乾淨（顆粒 {floor:.1f}），比不出色塊是不是後來疊上去的")
            labels.append("照片太乾淨、比不出來")
        elif thin:
            why.append("形狀太細，內縮之後量不到內部的顆粒")
            labels.append("太細、量不到顆粒")
        elif ratio > limit:
            where = "最平坦處" if flat_cmp else "（整張都是紋理，只能拿紋理處比，所以要求更嚴）"
            why.append(f"色塊內部的顆粒（{grain:.1f}）與照片{where}（{floor_cmp:.1f}）相比不夠乾淨（比值 {ratio:.2f}，要 ≤ {limit:.2f}），像照片的一部分")
            labels.append("顆粒與照片相近")
        elif page >= 0 and grain > float(P["overlay_page_grain_k"]) * page + float(P["overlay_page_grain_abs"]):
            why.append(f"色塊內部的顆粒（{grain:.1f}）比頁面底色（{page:.1f}）明顯多，像照片自己的平坦處")
            labels.append("顆粒比頁面底色多")
        elif 0 <= page <= float(P["overlay_translucent_page_max"]) and ratio > float(P["overlay_translucent_ratio"]):
            why.append(f"頁面底色完全乾淨，色塊內部卻透出一點照片的顆粒（是照片的 {ratio:.2f} 倍），像半透明的色塊")
            labels.append("像半透明")
        if clipped:
            why.append(f"顏色是{clipped}，可能是照片{'欠曝' if clipped == '純黑' else '過曝'}截平的地方（內部同樣沒有顆粒），保守起見不拆")
            labels.append("純黑或純白（疑似欠曝、過曝）")
        if c.grad_amp > float(P["overlay_max_gradient"]):
            why.append(f"色塊內部有明暗變化（約 {c.grad_amp:.0f} 色階）")
            labels.append("有明暗變化")
        if vis_frac < float(P["overlay_edge_visible"]):
            why.append(f"邊界大多被遮住（只看得到 {vis_frac:.0%}），證據不足")
            labels.append("邊界大多被遮住")
        elif cont < float(P["overlay_edge_continuity"]):
            why.append(f"邊緣不夠銳利或不連續（只有 {cont:.0%} 的邊界清楚）")
            labels.append("邊緣不清楚")
        if why:
            c.feats.update(fe)
            self.reject(host, labels, "；".join(why))
            return False
        fe.update({"overlay": 1.0, "overlay_host": float(host.id)})
        c.feats.update(fe)
        what = "照片" if host.role == "photo" else "有紋理的插畫"
        c.notes.append(f"疊在{what}上的平塗圖形：內部顆粒 {grain:.1f}，{what}{'最平坦處' if flat_cmp else '（整張是紋理）'} {floor_cmp:.1f}（比值 {ratio:.2f}）；"
                       f"{cont:.0%} 的邊界銳利而且有對比；內部顏色變化約 {c.grad_amp:.0f} 色階")
        return True


def isolate_overlay(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """疊在照片上的圖形要描圖時用：遮罩外（留 1 px 反鋸齒的混色邊）換成形狀外圍一圈照片像素的中位色。
    形狀外面本來就不描；照片的顆粒留著，描圖會把外面切出上百個碎塊而判定「色塊太多」。
    rgb：H×W×3 uint8；mask：H×W bool（True 是圖形）。回傳新的陣列，不改動輸入。"""
    out = np.array(rgb[..., :3], dtype=np.uint8, copy=True)
    m = np.asarray(mask, bool)
    ring = _dilate(m, 6) & ~_dilate(m, 2)
    if ring.any():
        out[~_dilate(m, 1)] = np.rint(np.median(out[ring], axis=0)).astype(np.uint8)
    return out


def _overlay_trace(img: np.ndarray, vis_full: np.ndarray, P: dict) -> Optional[dict]:
    """疊在照片上、但不是基本幾何的平塗色塊：交給描圖。重畫驗證過關才回傳說明，否則回傳 None（留在照片裡）。"""
    from .vectorize import vectorize

    bb = _bbox_of(vis_full)
    if bb is None:
        return None
    H, W = vis_full.shape
    X0, Y0, X1, Y1 = _expand_box(bb, 3, H, W)
    m = vis_full[Y0:Y1, X0:X1]
    crop = isolate_overlay(np.clip(np.rint(img[Y0:Y1, X0:X1]), 0, 255).astype(np.uint8), m)
    try:
        res = vectorize(crop, m)
    except Exception:  # noqa: BLE001 - 描圖出錯就當成描不成
        return None
    if not res.accepted or not res.paths:
        return None
    return {"feats": {"overlay_trace_error": float(res.render_error), "overlay_trace_paths": float(len(res.paths))},
            "notes": [f"外形不是基本幾何，描圖過關（{len(res.paths)} 條路徑，重畫色差 ΔE {res.render_error:.2f}）"]}


# =====================================================================================
# 表格
# =====================================================================================


def _line_segments(op: np.ndarray, diff: np.ndarray, horizontal: bool, max_th: float):
    n, lab, st, _ = cv2.connectedComponentsWithStats(op, connectivity=8)
    segs = []
    for k in range(1, n):
        x, y, w, h, a = st[k]
        th = h if horizontal else w
        if th > 3 * max_th + 4:  # 粗略上限；精確判斷用下面的有效線粗
            continue
        sub = lab[y : y + h, x : x + w] == k
        wts = diff[y : y + h, x : x + w] * sub
        tot = float(wts.sum()) + _EPS
        # 有效線粗：Σ對比 ÷（峰值對比 × 長度），不受模糊加寬影響
        peak = float(np.median(wts.max(axis=0 if horizontal else 1)))
        th_eff = tot / max(peak * (w if horizontal else h), _EPS)
        if th_eff > max_th + 0.5:
            continue
        if horizontal:
            pos = float((wts.sum(1) * (np.arange(h) + y + 0.5)).sum() / tot)
            segs.append({"pos": pos, "a0": float(x), "a1": float(x + w), "th": th_eff, "k": k, "peak": peak})
        else:
            pos = float((wts.sum(0) * (np.arange(w) + x + 0.5)).sum() / tot)
            segs.append({"pos": pos, "a0": float(y), "a1": float(y + h), "th": th_eff, "k": k, "peak": peak})
    return segs, lab


def _merge_segments(segs, tol, gap_k):
    segs = sorted(segs, key=lambda s: (s["pos"], s["a0"]))
    out = []
    for s in segs:
        for o in out:
            gap = max(s["a0"] - o["a1"], o["a0"] - s["a1"], 0.0)
            if abs(o["pos"] - s["pos"]) <= tol and gap <= max(3.0, gap_k * max(o["th"], s["th"])):
                L1, L2 = o["a1"] - o["a0"], s["a1"] - s["a0"]
                o["pos"] = (o["pos"] * L1 + s["pos"] * L2) / max(L1 + L2, _EPS)
                o["a0"], o["a1"] = min(o["a0"], s["a0"]), max(o["a1"], s["a1"])
                o["th"] = max(o["th"], s["th"])
                o["peak"] = max(o.get("peak", 0.0), s.get("peak", 0.0))
                o["ks"].append(s["k"])
                break
        else:
            out.append(dict(s, ks=[s["k"]]))
    return out


def _cluster_pos(vals, tol):
    vals = sorted(vals)
    groups: list[list[float]] = []
    for v in vals:
        if groups and v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [float(np.mean(g)) for g in groups]


# 【替換點 D】有框表格偵測：可加表格偵測模型的候選框交叉驗證。
def _detect_ruled_tables(fg: np.ndarray, diff: np.ndarray, P: dict, text_quads):
    H, W = fg.shape
    Lmin = int(max(float(P["table_min_line_px"]), float(P["table_min_line_rel"]) * W))
    max_th = max(float(P["table_max_thick_px"]), float(P["table_max_thick_rel"]) * W)
    tol = float(P["table_pos_tol_px"])
    fg8 = fg.astype(np.uint8)
    hop = cv2.morphologyEx(fg8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (Lmin, 1)))
    vop = cv2.morphologyEx(fg8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, Lmin)))
    hs, hlab = _line_segments(hop, diff, True, max_th)
    vs, vlab = _line_segments(vop, diff, False, max_th)
    hs = _merge_segments(hs, tol, float(P["table_gap_join_k"]))
    vs = _merge_segments(vs, tol, float(P["table_gap_join_k"]))
    nh, nv = len(hs), len(vs)
    tables = []
    px = np.zeros((H, W), bool)
    if nh == 0:
        return tables, px
    rows, cols = [], []
    for i, h in enumerate(hs):
        for j, v in enumerate(vs):
            if h["a0"] - tol <= v["pos"] <= h["a1"] + tol and v["a0"] - tol <= h["pos"] <= v["a1"] + tol:
                rows.append(i)
                cols.append(nh + j)
    for i in range(nh):  # 只有橫線的三線表：彼此 x 範圍重疊、間距不大的平行橫線
        for i2 in range(i + 1, nh):
            a, b = hs[i], hs[i2]
            ov = min(a["a1"], b["a1"]) - max(a["a0"], b["a0"])
            if ov >= 0.9 * max(a["a1"] - a["a0"], b["a1"] - b["a0"]) and abs(a["pos"] - b["pos"]) <= 0.15 * H:
                rows.append(i)
                cols.append(i2)
    g = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(nh + nv, nh + nv))
    ncomp, lab = connected_components(g, directed=False)
    for c in range(ncomp):
        idx = np.nonzero(lab == c)[0]
        hh = [hs[i] for i in idx if i < nh]
        vv = [vs[i - nh] for i in idx if i >= nh]
        pk = max([x["peak"] for x in hh + vv] + [_EPS])
        hh = [x for x in hh if x["peak"] >= 0.4 * pk]  # 振鈴光暈、殘邊的對比遠低於真正的線
        vv = [x for x in vv if x["peak"] >= 0.4 * pk]
        n_int = sum(1 for h in hh for v in vv if h["a0"] - tol <= v["pos"] <= h["a1"] + tol and v["a0"] - tol <= h["pos"] <= v["a1"] + tol)
        hl = _cluster_pos([h["pos"] for h in hh], tol)
        vl = _cluster_pos([v["pos"] for v in vv], tol)
        notes = []
        if len(hl) >= 2 and len(vl) >= 2 and (len(hl) - 1) * (len(vl) - 1) >= int(P["table_min_cells"]) and n_int >= 4:
            conf = float(np.clip(0.5 + 0.45 * n_int / max(len(hh) * len(vv), 1), 0.0, 0.95))
            ruled = True
        elif not vv and len(hl) >= int(P["table_rule_only_min"]):
            conf = 0.35
            ruled = True
            notes.append("只有橫線（三線表候選），欄位置未知")
            if text_quads:
                y0, y1 = min(hl), max(hl)
                x0, x1 = min(h["a0"] for h in hh), max(h["a1"] for h in hh)
                inside = sum(1 for q in text_quads if x0 <= np.mean(np.asarray(q)[:, 0]) <= x1 and y0 <= np.mean(np.asarray(q)[:, 1]) <= y1)
                if inside >= 2 * (len(hl) - 1):
                    conf = 0.5
                else:
                    continue
        else:
            continue
        thk = max([h["th"] for h in hh] + [v["th"] for v in vv])
        x0 = min([h["a0"] for h in hh] + [v["pos"] - thk / 2 for v in vv])
        x1 = max([h["a1"] for h in hh] + [v["pos"] + thk / 2 for v in vv])
        y0 = min([h["pos"] - thk / 2 for h in hh] + [v["a0"] for v in vv])
        y1 = max([h["pos"] + thk / 2 for h in hh] + [v["a1"] for v in vv])
        tpx = np.isin(hlab, [k for h in hh for k in h["ks"]]) | (np.isin(vlab, [k for v in vv for k in v["ks"]]) if vv else False)
        tpx = _dilate(tpx, 1) & fg
        px |= tpx
        n_exp = len(hl) * max(len(vl), 1)
        tables.append({
            "box": (int(math.floor(x0)), int(math.floor(y0)), int(math.ceil(x1)), int(math.ceil(y1))),
            "grid": TableGrid([round(v, 2) for v in hl], [round(v, 2) for v in vl], ruled),
            "conf": conf, "px": tpx, "notes": notes + [f"交點 {n_int}／應有 {n_exp}", f"線粗約 {thk:.1f} px"],
            "line_width": thk,
        })
    return tables, px


def _borderless_tables(text_quads, taken, P: dict, H: int, W: int):
    boxes = []
    for q in text_quads or []:
        q = np.asarray(q, np.float64).reshape(4, 2)
        ang = math.degrees(math.atan2(q[1, 1] - q[0, 1], q[1, 0] - q[0, 0]))
        if abs(ang) > 5:
            continue
        x0, y0 = q.min(0)
        x1, y1 = q.max(0)
        if y1 - y0 < 2:
            continue
        boxes.append((x0, y0, x1, y1))
    out = []
    if len(boxes) < int(P["bl_min_rows"]) * int(P["bl_min_cols"]):
        return out
    hmed = float(np.median([b[3] - b[1] for b in boxes]))
    boxes.sort(key=lambda b: (b[1] + b[3]) / 2)
    rows: list[list] = []
    for b in boxes:
        yc = (b[1] + b[3]) / 2
        if rows:
            r = rows[-1]
            ryc = np.mean([(c[1] + c[3]) / 2 for c in r])
            if abs(yc - ryc) <= float(P["bl_row_tol"]) * hmed:
                r.append(b)
                continue
        rows.append([b])
    river = float(P["bl_river_min"]) * hmed
    rcells = []
    for r in rows:
        r.sort(key=lambda b: b[0])
        cells = [list(r[0])]
        for b in r[1:]:
            if b[0] - cells[-1][2] < river:
                cells[-1][2] = max(cells[-1][2], b[2])
                cells[-1][1] = min(cells[-1][1], b[1])
                cells[-1][3] = max(cells[-1][3], b[3])
            else:
                cells.append(list(b))
        rcells.append(cells)
    runs, cur = [], []
    for cells in rcells:
        y0 = min(c[1] for c in cells)
        if len(cells) >= 2 and (not cur or y0 - max(c[3] for c in cur[-1]) <= float(P["bl_row_gap_max"]) * hmed):
            cur.append(cells)
        else:
            if len(cur) >= int(P["bl_min_rows"]):
                runs.append(cur)
            cur = [cells] if len(cells) >= 2 else []
    if len(cur) >= int(P["bl_min_rows"]):
        runs.append(cur)
    for run in runs:
        allc = [c for cells in run for c in cells]
        X0, X1 = int(math.floor(min(c[0] for c in allc))), int(math.ceil(max(c[2] for c in allc)))
        Y0, Y1 = min(c[1] for c in allc), max(c[3] for c in allc)
        cov = np.zeros(max(X1 - X0, 1), bool)
        for c in allc:
            cov[int(c[0]) - X0 : int(math.ceil(c[2])) - X0] = True
        rivers, i = [], 0
        while i < len(cov):
            if not cov[i]:
                j = i
                while j < len(cov) and not cov[j]:
                    j += 1
                if j - i >= river:
                    rivers.append((X0 + i, X0 + j))
                i = j
            else:
                i += 1
        ncols = len(rivers) + 1
        if ncols < int(P["bl_min_cols"]):
            continue
        edges = [X0] + [(a + b) / 2 for a, b in rivers] + [X1]
        aligned = 0
        for cells in run:
            occ = {int(np.searchsorted(edges, (c[0] + c[2]) / 2) - 1) for c in cells}
            aligned += len(occ) >= 2
        frac = aligned / len(run)
        mean_w = float(np.mean([c[2] - c[0] for c in allc]))
        fills = []
        for c in allc:
            ci = int(np.clip(np.searchsorted(edges, (c[0] + c[2]) / 2) - 1, 0, len(edges) - 2))
            fills.append((c[2] - c[0]) / max(edges[ci + 1] - edges[ci], 1.0))
        fill_med = float(np.median(fills))
        if frac < float(P["bl_row_align_min"]) or mean_w > float(P["bl_max_cell_frac"]) * (X1 - X0) or fill_med > float(P["bl_max_col_fill"]):
            continue  # 儲存格填滿整欄（長文分欄）不算表格
        box = (X0, int(math.floor(Y0)), X1, int(math.ceil(Y1)))
        if any(_box_iou(box, t) > 0.3 for t in taken):
            continue
        ys = [Y0] + [(max(c[3] for c in run[i]) + min(c[1] for c in run[i + 1])) / 2 for i in range(len(run) - 1)] + [Y1]
        conf = float(P["bl_conf_base"]) + float(P["bl_conf_gain"]) * frac * min(1.0, len(run) / 5.0)
        out.append({"box": box, "grid": TableGrid([round(float(y), 2) for y in ys], [round(float(x), 2) for x in edges], False), "conf": conf,
                    "notes": [f"由文字對齊推得的無框線表格：{len(run)} 列 × {ncols} 欄，跨欄對齊比例 {frac:.2f}"]})
    return out


def _box_iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / max(ua, 1)


# =====================================================================================
# 主流程
# =====================================================================================


@dataclass(eq=False)
class _Out:
    kind: str
    amodal: np.ndarray  # 整張影像大小 bool
    visible: np.ndarray  # 整張影像大小 bool
    conf: float
    kind_cands: list
    shape: Optional[ShapeFit] = None
    table: Optional[TableGrid] = None
    outline: Optional[ShapeFit] = None
    feats: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


# 【替換點 A】候選分割：目前是「平坦區塊＋紋理區塊」的經典演算法；之後可把版面模型或 SAM 的遮罩併入候選。
def _find_regions_impl(image, text_mask, P, text_quads):
    img = np.ascontiguousarray(np.asarray(image)[..., :3]).astype(np.float32)
    H, W = img.shape[:2]
    unknown = np.zeros((H, W), bool) if text_mask is None else np.asarray(text_mask).astype(bool)
    filled = _fill_unknown(img, unknown)
    lap = _laplace_abs(filled)
    bg = _estimate_background_filled(filled, unknown, lap, P)
    # 滿版照片：選出來的「底色」完全沒碰到影像外框、只佔一小部分，外框一圈又幾乎都是紋理——那片「底色」其實是疊在照片上的
    # 色塊（或照片裡的平坦處）。這時整張影像都是前景、整張當成照片候選，疊加判斷照常進行。
    full_bleed = bool(P.get("overlay_enabled", True) and P.get("overlay_full_bleed", True)
                      and bg.border_flat <= float(P["overlay_full_bleed_border_flat"]) and bg.border_touch <= float(P["overlay_full_bleed_touch"])
                      and (bg.kind == "none" or bg.coverage < float(P["overlay_full_bleed_coverage"])))
    if full_bleed:
        ring = np.zeros((H, W), bool)
        b = max(2, min(H, W) // 50)
        ring[:b, :] = ring[-b:, :] = True
        ring[:, :b] = ring[:, -b:] = True
        col = np.median(filled[ring & ~unknown], axis=0) if (ring & ~unknown).any() else np.median(filled.reshape(-1, 3), axis=0)
        coeffs = np.zeros((6, 3))
        coeffs[0] = col
        bg = BackgroundModel("none", None, coeffs, bg.noise_sigma, 0.0, 0.0, 0.1,
                             ["整張影像是照片（滿版，沒有頁面底色）：整張當成照片，再找疊在上面的平塗圖形"], border_flat=bg.border_flat, border_touch=bg.border_touch)
    sigma = bg.noise_sigma
    s_fg = float(P["fg_smooth_sigma"])
    gain = 1.0 / (2.0 * math.sqrt(math.pi) * s_fg) if s_fg > 0 else 1.0
    pred = bg.predict(H, W)
    sm = cv2.GaussianBlur(filled, (0, 0), s_fg) if s_fg > 0 else filled
    diff = np.linalg.norm(sm - pred, axis=2)
    thr = max(float(P["fg_min_diff"]), float(P["fg_noise_k"]) * sigma * gain * math.sqrt(3), float(P["fg_model_k"]) * bg.residual)
    fg = np.ones((H, W), bool) if full_bleed else diff > thr
    min_area = max(8.0, float(P["min_area_rel"]) * H * W)
    n, lab, st, _ = cv2.connectedComponentsWithStats(fg.astype(np.uint8), connectivity=8)
    small = np.nonzero(st[:, cv2.CC_STAT_AREA] < 0.25 * min_area)[0]
    if len(small):
        fg &= ~np.isin(lab, small[small > 0])
    # 梯度
    gs = cv2.GaussianBlur(filled, (0, 0), float(P["grad_sigma"])) if float(P["grad_sigma"]) > 0 else filled
    gx = cv2.Sobel(gs, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    gy = cv2.Sobel(gs, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    grad = np.sqrt(gx * gx + gy * gy).max(axis=2)
    g_thr = max(float(P["grad_abs"]), float(P["grad_noise_k"]) * sigma)
    smooth = grad < g_thr
    # ---- 表格 ----
    diff_raw = np.linalg.norm(filled - pred, axis=2)
    fg_raw = fg & (diff_raw > max(float(P["fg_min_diff"]), float(P["fg_noise_k"]) * sigma * math.sqrt(3)))
    if full_bleed:
        fg_raw = np.zeros((H, W), bool)  # 滿版照片上不找有框線的表格（照片的紋理會被當成格線）
    tables, table_px = _detect_ruled_tables(fg_raw, diff_raw, P, text_quads)
    table_px = _dilate(table_px, 1) & fg
    work = fg & ~_dilate(table_px, int(math.ceil(2 * s_fg)) + 1)
    # ---- 平坦區塊 ----
    nf, flab, fst, _ = cv2.connectedComponentsWithStats((work & smooth).astype(np.uint8), connectivity=4)
    flat_tol = max(float(P["flat_color_tol"]), float(P["flat_noise_k"]) * sigma)
    S = float(max(H, W))
    cands: list[_Cand] = []
    explained = np.zeros((H, W), bool)
    acc: list = []

    def _flat_stats(sub: np.ndarray, x: int, y: int):
        """局部遮罩 sub（左上角在 x, y）是不是平坦區塊：回傳（中位色、離散度、跨區顏色變化）或 None。"""
        h, w = sub.shape
        a = int(sub.sum())
        known = sub & ~unknown[y : y + h, x : x + w]
        use = known if known.sum() >= 0.2 * a else sub
        yy, xx = np.nonzero(use)
        cols = filled[y : y + h, x : x + w][use].astype(np.float64)
        if len(cols) > 20000:
            sel = np.linspace(0, len(cols) - 1, 20000).astype(int)
            yy, xx, cols = yy[sel], xx[sel], cols[sel]
        A = np.stack([np.ones(len(yy)), (xx + x) / S, (yy + y) / S], 1)
        coef, *_ = np.linalg.lstsq(A, cols, rcond=None)
        res = np.linalg.norm(cols - A @ coef, axis=1)
        spread = float(1.4826 * np.median(res))
        if spread > flat_tol:
            return None
        loc_sigma = _sigma_from_lap(lap[y : y + h, x : x + w][use])
        if loc_sigma > max(float(P["flat_hf_abs"]), float(P["flat_hf_k"]) * sigma):
            return None  # 區內有細紋理（雜訊比頁面高）：是照片的平滑部分，不是色塊
        return np.median(cols, axis=0), spread, float(np.linalg.norm(coef[1:], axis=1).max() * max(w, h) / S)

    grain_cache: list = []

    def _clean_parts(sub: np.ndarray, x: int, y: int) -> list:
        """平坦測試沒過的平滑區裡，找「完全沒有顆粒、顏色一致」的大塊（疊在照片上的平塗形狀）。

        照片紋理裡與形狀顏色相近的地方梯度很小，平滑區會穿過形狀的邊界連成一片，整片就不平坦了。
        這裡把顆粒（高通殘差的局部平均）幾乎為零的像素挑出來，取夠大的連通塊，再沿著同色像素長回邊緣（最多幾個像素）。
        整片都乾淨（沒有顆粒的插畫漸層）或乾淨的部分太小，就回傳空清單、維持原判。"""
        h, w = sub.shape
        if not grain_cache:
            # 顆粒的局部平均只算前景像素（頁面底色是乾淨的，混進來會讓照片邊緣一圈看起來也很乾淨）
            s_hp = float(P["overlay_hp_sigma"])
            r = np.abs(filled - cv2.GaussianBlur(filled, (0, 0), s_hp)).max(axis=2)  # 取各色版最大：照片過曝、欠曝時只有部分色版被截平
            m = fg.astype(np.float32)
            grain_cache.append(cv2.blur(r * m, (7, 7)) / np.maximum(cv2.blur(m, (7, 7)), 1e-3))
        g = grain_cache[0][y : y + h, x : x + w]
        clean = sub & (g <= max(float(P["overlay_clean_grain"]), 2.0 * sigma)) & ~unknown[y : y + h, x : x + w]
        if clean.sum() > 0.9 * sub.sum():
            return []
        clean = cv2.erode(clean.astype(np.uint8), _disk(2), borderType=cv2.BORDER_CONSTANT, borderValue=0).astype(bool)  # 細長的一條不算
        need = 0.5 * max(float(P["overlay_min_area_rel"]) * H * W, 4.0 * min_area)
        n, lab_c, st, _ = cv2.connectedComponentsWithStats(clean.astype(np.uint8), connectivity=4)
        Fl = filled[y : y + h, x : x + w]
        taken = np.zeros_like(sub)
        parts = []
        for j in sorted(range(1, n), key=lambda j: -int(st[j, cv2.CC_STAT_AREA])):
            if st[j, cv2.CC_STAT_AREA] < need:
                break
            core = (lab_c == j) & ~taken
            col = np.median(Fl[core], axis=0)
            near = sub & ~taken & (np.abs(Fl - col).max(axis=2) <= flat_tol)
            grow = core
            for _ in range(8):
                grow = cv2.dilate(grow.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & near
            taken |= grow
            # 只收被前景整個圍住的（疊在照片中間）；碰到頁面底色的多半是照片邊緣的平滑處，維持原判
            X0, Y0, X1, Y1 = _expand_box((x, y, x + w, y + h), 3, H, W)
            ring = _dilate(_paste((Y1 - Y0, X1 - X0), (x - X0, y - Y0, x - X0 + w, y - Y0 + h), grow), 3)
            if (ring & ~fg[Y0:Y1, X0:X1] & ~unknown[Y0:Y1, X0:X1]).any():
                continue
            parts.append(grow)
        return parts

    for k in range(1, nf):
        x, y, w, h, a = (int(v) for v in fst[k])
        if a < 0.3 * min_area:
            continue
        sub = flab[y : y + h, x : x + w] == k
        r = _flat_stats(sub, x, y)
        if r is not None:
            acc.append([k, r[0], r[1], r[2], a])
            continue
        if not P.get("overlay_enabled", True) or a < max(float(P["overlay_min_area_rel"]) * H * W, 4.0 * min_area):
            continue
        parts = _clean_parts(sub, x, y)
        if not parts:
            continue
        fsub = flab[y : y + h, x : x + w]
        fsub[sub] = 0  # 原本那一片拆開：乾淨的部分各自成為平坦候選，其餘留給紋理
        for i, part in enumerate(parts):
            px0, py0, px1, py1 = _bbox_of(part)
            lab_id = k if i == 0 else len(fst)
            row = np.array([[x + px0, y + py0, px1 - px0, py1 - py0, int(part.sum())]], dtype=fst.dtype)
            if i == 0:
                fst[k] = row[0]
            else:
                fst = np.vstack([fst, row])
            fsub[part] = lab_id
            r = _flat_stats(part[py0:py1, px0:px1], x + px0, y + py0)
            if r is not None:
                acc.append([lab_id, r[0], r[1], r[2], int(part.sum())])
    # 相鄰而且顏色相近的平坦區塊合併（JPEG 區塊邊界、細縫會把一塊切開）
    mtol = max(float(P["merge_color_tol"]), 3.0 * sigma)
    parent = {a[0]: a[0] for a in acc}
    info = {a[0]: a for a in acc}

    def _root(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for a in acc:
        k = a[0]
        x, y, w, h, _ = fst[k]
        bx = _expand_box((x, y, x + w, y + h), 3, H, W)
        sub = flab[bx[1] : bx[3], bx[0] : bx[2]]
        nb = np.unique(sub[_dilate(sub == k, 3)])
        for j in nb:
            if j in info and j != k and np.linalg.norm(info[j][1] - a[1]) <= mtol:
                parent[_root(j)] = _root(k)
    groups: dict[int, list] = {}
    for a in acc:
        groups.setdefault(_root(a[0]), []).append(a)
    for root, members in groups.items():
        if len(members) > 1:
            for m in members:
                if m[0] != root:
                    flab[flab == m[0]] = root
        xs0 = min(fst[m[0]][0] for m in members)
        ys0 = min(fst[m[0]][1] for m in members)
        xs1 = max(fst[m[0]][0] + fst[m[0]][2] for m in members)
        ys1 = max(fst[m[0]][1] + fst[m[0]][3] for m in members)
        big = max(members, key=lambda m: m[4])
        F = big[1]
        bx, vis, expl = _refine_flat(flab, root, (xs0, ys0, xs1, ys1), filled, smooth, unknown, F.astype(np.float32), P, sigma, None if full_bleed else pred, thr)
        X0, Y0, X1, Y1 = bx
        explained[Y0:Y1, X0:X1] |= expl
        cands.append(_Cand(len(cands), "flat", bx, vis, F, spread=big[2], grad_amp=big[3]))
    flat_union = np.zeros((H, W), bool)
    for c in cands:
        X0, Y0, X1, Y1 = c.box
        flat_union[Y0:Y1, X0:X1] |= c.vis
    # ---- 紋理區塊 ----
    tex = work & ~flat_union & ~explained
    tex = cv2.morphologyEx(tex.astype(np.uint8), cv2.MORPH_CLOSE, _disk(1)).astype(bool) & ~flat_union
    nt, tlab, tst, _ = cv2.connectedComponentsWithStats(tex.astype(np.uint8), connectivity=8)
    for k in range(1, nt):
        x, y, w, h, a = tst[k]
        if a < min_area:
            continue
        bx = _expand_box((int(x), int(y), int(x + w), int(y + h)), 2, H, W)
        vis = tlab[bx[1] : bx[3], bx[0] : bx[2]] == k
        vis = _refine_texture(vis, filled[bx[1] : bx[3], bx[0] : bx[2]], fg[bx[1] : bx[3], bx[0] : bx[2]], P)
        if vis.sum() < 0.5 * min_area:
            continue
        col = np.median(filled[bx[1] : bx[3], bx[0] : bx[2]][vis], axis=0)
        cands.append(_Cand(len(cands), "texture", bx, vis, col))
    # 標籤圖
    L = np.zeros((H, W), np.int32)
    for c in cands:
        X0, Y0, X1, Y1 = c.box
        sub = L[Y0:Y1, X0:X1]
        sub[c.vis & (sub == 0)] = c.id + 1
    line_thick = _line_thick(P, H, W)
    tol = _fit_tol(P, H, W)
    band_px = int(P["edge_band_px"])
    bgL = _luma(bg.color) if bg.color is not None else 128.0
    # ---- 基本特徵 ----
    for c in cands:
        X0, Y0, X1, Y1 = c.box
        area = int(c.vis.sum())
        filled_m = _fill_holes(c.vis)
        hole = filled_m & ~c.vis
        pts = np.column_stack(np.nonzero(c.vis)[::-1]).astype(np.float32)
        (_, _), (rw, rh), _ = cv2.minAreaRect(pts) if len(pts) >= 3 else ((0, 0), (1, 1), 0)
        short, long_ = min(rw, rh) + 1.0, max(rw, rh) + 1.0
        dmean = float(diff[Y0:Y1, X0:X1][c.vis].mean())
        raw_med = float(np.median(diff_raw[Y0:Y1, X0:X1][c.vis])) if area else 0.0
        if raw_med < thr and not full_bleed:
            c.role = "artifact"  # 平滑造成的殘影：像素本身其實是背景色（滿版照片沒有頁面底色，不判斷）
        c.feats.update({"raw_diff_med": raw_med, "area_px": float(area), "thickness_px": _thickness(c.vis), "short_px": short, "long_px": long_,
                        "hole_frac": float(hole.sum()) / max(float(filled_m.sum()), 1.0), "mean_diff_bg": dmean,
                        "darker_than_bg": float(_luma(c.color) < bgL)})
    # ---- 細碎片組環：外框線常被切成好幾段（平坦段＋轉角紋理），連起來是環狀就併成一個候選 ----
    stroke_cap = max(line_thick, float(P["stroke_max_rel"]) * min(H, W)) + 1.0
    thin = [c for c in cands if not c.role and c.feats["thickness_px"] <= stroke_cap]
    if len(thin) >= 2:
        tu = np.zeros((H, W), bool)
        for c in thin:
            X0, Y0, X1, Y1 = c.box
            tu[Y0:Y1, X0:X1] |= c.vis
        tl = cv2.connectedComponents(_dilate(tu, 2).astype(np.uint8), connectivity=8)[1]
        grp: dict[int, list] = {}
        for c in thin:
            X0, Y0, X1, Y1 = c.box
            g = int(np.bincount(tl[Y0:Y1, X0:X1][c.vis]).argmax())
            grp.setdefault(g, []).append(c)
        for g, mem in grp.items():
            if len(mem) < 2:
                continue
            merged = np.zeros((H, W), bool)
            for c in mem:
                X0, Y0, X1, Y1 = c.box
                merged[Y0:Y1, X0:X1] |= c.vis
            merged = cv2.morphologyEx(merged.astype(np.uint8), cv2.MORPH_CLOSE, _disk(2)).astype(bool)
            bb = _bbox_of(merged)
            loc = merged[bb[1] : bb[3], bb[0] : bb[2]]
            fh = _fill_holes(loc)
            if (fh & ~loc).sum() < float(P["ring_hole_frac"]) * fh.sum():
                continue
            areas = np.array([c.feats["area_px"] for c in mem])
            cols = np.array([c.color for c in mem])
            main = cols[int(areas.argmax())]
            same = np.linalg.norm(cols - main, axis=1) <= 2 * max(float(P["merge_color_tol"]), 3 * sigma)
            if areas[same].sum() < 0.8 * areas.sum():
                continue
            nc = _Cand(len(cands), "texture", bb, loc, main.copy())
            for c in mem:
                c.role, c.parent = "consumed", nc.id
            cands.append(nc)
            sub = L[bb[1] : bb[3], bb[0] : bb[2]]
            sub[loc] = nc.id + 1
            pts = np.column_stack(np.nonzero(loc)[::-1]).astype(np.float32)
            (_, _), (rw, rh), _ = cv2.minAreaRect(pts)
            nc.feats.update({"raw_diff_med": float(np.median(diff_raw[bb[1] : bb[3], bb[0] : bb[2]][loc])), "area_px": float(loc.sum()),
                             "thickness_px": max(c.feats["thickness_px"] for c in mem), "short_px": min(rw, rh) + 1, "long_px": max(rw, rh) + 1,
                             "hole_frac": float((fh & ~loc).sum()) / max(float(fh.sum()), 1.0), "mean_diff_bg": float(diff[bb[1] : bb[3], bb[0] : bb[2]][loc].mean()),
                             "darker_than_bg": float(_luma(main) < bgL), "n_parts": float(len(mem))})
    # ---- 邊緣殘影：緊貼平坦區塊的薄紋理，顏色接近該區塊色或背景色（JPEG 振鈴、模糊殘邊） ----
    near_flat = _dilate(flat_union, 2 * band_px + 2) if flat_union.any() else flat_union
    for c in cands:
        if c.role or c.source != "texture" or c.feats["thickness_px"] > 2 * band_px + 2:
            continue
        X0, Y0, X1, Y1 = c.box
        if near_flat[Y0:Y1, X0:X1][c.vis].mean() < 0.8:
            continue
        bx = _expand_box(c.box, 2 * band_px + 3, H, W)
        loc = _paste((H, W), c.box, c.vis)[bx[1] : bx[3], bx[0] : bx[2]]
        nb = L[bx[1] : bx[3], bx[0] : bx[2]][_dilate(loc, 2 * band_px + 2) & ~loc]
        nb = nb[(nb > 0) & (nb != c.id + 1)]
        if nb.size == 0:
            continue
        o = cands[int(np.bincount(nb).argmax()) - 1]
        if o.source != "flat":
            continue
        Ob = pred[Y0:Y1, X0:X1][c.vis].mean(0)
        cF = float(np.linalg.norm(c.color - o.color))
        cO = float(np.linalg.norm(c.color - Ob)) if not full_bleed else float("inf")
        if min(cF, cO) <= max(float(P["ringing_max"]), float(P["ringing_frac"]) * float(np.linalg.norm(o.color - Ob))):
            c.role = "artifact"
    # ---- 陰影：緊貼較大區塊、比背景暗、對比低、很薄 ----
    sh_w = float(P["shadow_max_width_rel"]) * min(H, W)
    for c in cands:
        if full_bleed:
            break  # 陰影是投在頁面底色上的；滿版照片沒有頁面底色
        f = c.feats
        if not (f["mean_diff_bg"] <= float(P["shadow_max_contrast"]) and f["darker_than_bg"] > 0 and f["thickness_px"] <= sh_w):
            continue
        X0, Y0, X1, Y1 = c.box
        dv = diff_raw[Y0:Y1, X0:X1][c.vis]
        p10, p90 = np.percentile(dv, [10, 90])
        soft = p90 - p10 >= float(P["shadow_soft_frac"]) * p90  # 柔邊（對比由內往外遞減）
        line_like = f["short_px"] <= line_thick + 1 and f["long_px"] >= float(P["line_min_aspect"]) * f["short_px"]
        bx = _expand_box(c.box, 3, H, W)
        ring = _dilate(_paste((H, W), c.box, c.vis)[bx[1] : bx[3], bx[0] : bx[2]], 3)
        nb = L[bx[1] : bx[3], bx[0] : bx[2]][ring]
        nb = nb[(nb > 0) & (nb != c.id + 1)]
        if nb.size == 0:
            continue
        other = cands[int(np.bincount(nb).argmax()) - 1]
        faint = f["mean_diff_bg"] <= float(P["shadow_line_rel"]) * other.feats["mean_diff_bg"]  # 比鄰居淡得多
        if (line_like and not faint) or not (soft or faint):
            continue  # 細長而且對比不弱：是線；對比均勻又不淡：是實心的細東西
        if other.feats["area_px"] > f["area_px"]:
            c.role = "shadow"
            c.parent = other.id
            other.notes.append("有附屬陰影（已併入，不另成區域）")
    # ---- 細長：直線 ----
    for c in cands:
        if c.role:
            continue
        f = c.feats
        if f["short_px"] <= line_thick + 1.0 and f["long_px"] >= float(P["line_min_aspect"]) * f["short_px"] and f["hole_frac"] < 0.05:
            c.role = "line"
    # ---- 紋理：照片或插畫 ----
    for c in cands:
        if c.role or c.source != "texture":
            continue
        X0, Y0, X1, Y1 = c.box
        s, feats = _classify_texture(filled[Y0:Y1, X0:X1], c.vis & ~unknown[Y0:Y1, X0:X1] if (c.vis & ~unknown[Y0:Y1, X0:X1]).sum() > 30 else c.vis,
                                     grad[Y0:Y1, X0:X1], lap[Y0:Y1, X0:X1], g_thr, sigma, P)
        c.feats.update(feats)
        c.feats["photo_score"] = s
        if c.feats["hole_frac"] >= float(P["ring_hole_frac"]) and c.feats["thickness_px"] <= max(line_thick, float(P["stroke_max_rel"]) * c.feats["short_px"]) + 1:
            c.role = "ring"
        else:
            c.role = "photo" if s >= 0.5 else "illus"
    # ---- 環狀平坦區塊（外框線） ----
    for c in cands:
        if c.role or c.source != "flat":
            continue
        if c.feats["hole_frac"] >= float(P["ring_hole_frac"]) and c.feats["thickness_px"] <= max(line_thick, float(P["stroke_max_rel"]) * c.feats["short_px"]) + 1:
            c.role = "ring"
    # 照片的填滿外形（判斷包圍用）；有紋理的插畫另外記（疊在上面的形狀也要判斷）
    photo_hull = np.zeros((H, W), np.int32)
    illus_hull = np.zeros((H, W), np.int32)
    for c in cands:
        if c.role in ("photo", "illus"):
            X0, Y0, X1, Y1 = c.box
            sub = (photo_hull if c.role == "photo" else illus_hull)[Y0:Y1, X0:X1]
            sub[_fill_holes(c.vis)] = c.id + 1
    # ---- 被照片包圍的平坦區塊：疊在照片上的平塗形狀就拆出來，其餘併回照片 ----
    band = int(P["edge_band_px"])
    ov = _OverlayContext(filled, unknown, fg, L, cands, P, H, W) if P.get("overlay_enabled", True) else None
    for c in cands:
        if c.role or c.source != "flat":
            continue
        full = _paste((H, W), c.box, c.vis)
        bx = _expand_box(c.box, 2 * band + 6, H, W)
        loc = full[bx[1] : bx[3], bx[0] : bx[2]]
        ring = _dilate(loc, 2 * band + 4) & ~_dilate(loc, 2 * band + 1)
        ph = photo_hull[bx[1] : bx[3], bx[0] : bx[2]][ring]
        if ph.size and (ph > 0).mean() >= float(P["enclosed_by_photo"]):
            pid = int(np.bincount(ph[ph > 0]).argmax()) - 1
            touches = c.box[0] <= 0 or c.box[1] <= 0 or c.box[2] >= W or c.box[3] >= H
            if full_bleed and touches:
                pass  # 滿版照片裡碰到影像外框的平坦處（天空、地面）：看不到完整的邊界，當成照片內容
            elif ov is not None and ov.judge(c, cands[pid], min_area):
                continue  # 疊在照片上的形狀：照常擬合，圖層在照片之上
            c.role = "merged"
            c.parent = pid
            continue
        ih = illus_hull[bx[1] : bx[3], bx[0] : bx[2]][ring]
        if ov is not None and ih.size and (ih > 0).mean() >= float(P["enclosed_by_photo"]):
            ov.judge(c, cands[int(np.bincount(ih[ih > 0]).argmax()) - 1], min_area)  # 通過的不會被插畫吸收
    # ---- 環狀：找填色 ----
    for c in cands:
        if c.role != "ring":
            continue
        hole = _fill_holes(c.vis) & ~c.vis
        X0, Y0, X1, Y1 = c.box
        ids = L[Y0:Y1, X0:X1][hole]
        ids = ids[ids > 0]
        c.feats["fill_id"] = -1
        if ids.size:
            cid = int(np.bincount(ids).argmax()) - 1
            o = cands[cid]
            if o.source == "flat" and o.role in ("",) and (ids == cid + 1).sum() >= float(P["ring_fill_frac"]) * hole.sum():
                o.role = "consumed"
                o.parent = c.id
                c.feats["fill_id"] = cid
    # ---- 平坦區塊擬合 ----
    outs: list[_Out] = []

    hulls: dict[int, np.ndarray] = {}

    def _not_occluders(c: _Cand) -> list[int]:
        """不算遮擋物的鄰居：陰影、殘影、以及把 c 包在裡面的（在 c 底下）。回傳標籤值。"""
        ids = [o.id + 1 for o in cands if o.role in ("shadow", "artifact")]
        for o in cands:
            if o.id == c.id or o.role in ("shadow", "artifact"):
                continue
            a, b = o.box, c.box
            if a[0] <= b[0] and a[1] <= b[1] and a[2] >= b[2] and a[3] >= b[3]:
                if o.id not in hulls:
                    hulls[o.id] = _fill_holes(o.vis)
                sub = hulls[o.id][b[1] - a[1] : b[3] - a[1], b[0] - a[0] : b[2] - a[0]]
                if (sub & c.vis).sum() >= 0.9 * max(c.vis.sum(), 1):
                    ids.append(o.id + 1)
        return ids

    def _bad_zone(c: _Cand, box, extra_ids=()) -> np.ndarray:
        X0, Y0, X1, Y1 = box
        Ls = L[Y0:Y1, X0:X1]
        own = np.isin(Ls, [c.id + 1] + [i + 1 for i in extra_ids] + _not_occluders(c))
        other = ((Ls > 0) & ~own) | unknown[Y0:Y1, X0:X1] | table_px[Y0:Y1, X0:X1]
        return _dilate(other, int(P["occlusion_dilate_px"])) & ~_crop_bool(_paste((H, W), c.box, c.vis), box) | _dilate(other, 1)

    def _fill_of(vis_full: np.ndarray, erode_px: int) -> tuple[Optional[RGB], float]:
        inner = vis_full & ~unknown
        bb = _bbox_of(inner)
        if bb is None:
            return None, 99.0
        X0, Y0, X1, Y1 = _expand_box(bb, 1, H, W)
        loc = inner[Y0:Y1, X0:X1]
        er = cv2.erode(loc.astype(np.uint8), _disk(erode_px)).astype(bool) if erode_px > 0 else loc
        use = er if er.sum() >= 20 else loc
        px = img[Y0:Y1, X0:X1][use]
        med = np.median(px, axis=0)
        mad = float(1.4826 * np.median(np.linalg.norm(px - med, axis=1)))
        return _to_rgb(med), mad

    for c in cands:
        if c.role not in ("", "ring", "line"):
            continue
        box = _expand_box(c.box, 3, H, W)
        X0, Y0, X1, Y1 = box
        vis_loc = _crop_bool(_paste((H, W), c.box, c.vis), box)
        extra = [c.feats["fill_id"]] if c.role == "ring" and c.feats.get("fill_id", -1) >= 0 else []
        for e in extra:
            vis_loc = vis_loc | _crop_bool(_paste((H, W), cands[e].box, cands[e].vis), box)
        # 洞：被其他東西或文字佔住的補起來；背景色的洞（中空外形）保留
        holes = _fill_holes(vis_loc) & ~vis_loc
        fill_mask = vis_loc | (holes & (fg[Y0:Y1, X0:X1] | unknown[Y0:Y1, X0:X1]))
        if c.role == "ring":
            fill_mask = _fill_holes(vis_loc)
        bad = _bad_zone(c, box, extra)
        Ls = L[Y0:Y1, X0:X1]
        occluders = ((Ls > 0) & ~np.isin(Ls, [c.id + 1] + [e + 1 for e in extra] + _not_occluders(c))) | unknown[Y0:Y1, X0:X1]
        tips = None
        if c.feats.get("overlay") == 1.0:
            tips = _tip_color_check(filled[Y0:Y1, X0:X1], fill_mask, bad, np.asarray(c.color, np.float32))
        fit = _fit_mask(fill_mask, bad, P, tol, allow_line=(c.role == "line"), line_thick=line_thick, allowed=occluders, tips=tips)
        vis_full = _paste((H, W), box, vis_loc)
        if fit is None or not fit.accepted:
            if c.role == "line" and fit is not None:
                fit.accepted = True
            else:
                c.role = "complex"
                c.feats["fit_error_px"] = fit.err_mean if fit is not None else 99.0
                continue
        notes = list(c.notes)
        if c.role == "line":
            # 線寬：以覆蓋率積分（Σα ÷ 長度）
            zone = _dilate(vis_loc, 2)
            ringo = _dilate(vis_loc, 5) & ~_dilate(vis_loc, 3) & ~unknown[Y0:Y1, X0:X1] & (~fg[Y0:Y1, X0:X1] | full_bleed)
            I = filled[Y0:Y1, X0:X1]
            O = np.median(I[ringo], axis=0) if ringo.sum() >= 5 else pred[Y0:Y1, X0:X1][zone].mean(0)
            dd = np.linalg.norm(I - O, axis=2)
            core = float(np.percentile(dd[vis_loc], 90)) if vis_loc.any() else 1.0
            alpha = np.clip(dd / max(core, _EPS), 0, 1)
            length = 2 * max(fit.hx, fit.hy) + 1.0
            width = float(alpha[zone].sum() / max(length, 1.0))
            strong = vis_loc & (dd >= 0.8 * core)
            col = _to_rgb(np.median(I[strong], axis=0)) if strong.any() else _to_rgb(c.color)
            thin = width < 2.0
            sw = Estimate(width, 0.5 if thin else 0.3, 0.3 if thin else 0.8)
            if thin:
                notes.append("細線（< 2 px）：線寬與顏色無法分離，數值僅供參考")
            sf = _make_shapefit(P, fit, (X0, Y0), stroke=col, stroke_w=sw)
            amodal = _raster_full(_shapefit_outer_poly(sf), H, W)
            if amodal.sum() < 0.5 * vis_full.sum():
                amodal |= vis_full
            outs.append(_Out("shape", amodal, vis_full & ~unknown, sf.confidence, [("shape", sf.confidence), ("illustration", 1 - sf.confidence)], shape=sf, feats=dict(c.feats, fit_error_px=fit.err_mean), notes=notes))
            continue
        if c.role == "ring":
            ring_full = _paste((H, W), c.box, c.vis)
            # 線寬＝環面積 ÷ 中心線長；環面積＝外輪廓內面積 − 最大內洞面積（不受碎縫影響）
            outer_fill = _fill_holes(c.vis)
            hl = cv2.connectedComponentsWithStats((outer_fill & ~c.vis).astype(np.uint8), connectivity=4)
            inner = hl[1] == (1 + int(np.argmax(hl[2][1:, cv2.CC_STAT_AREA]))) if hl[0] > 1 else np.zeros_like(c.vis)
            co = cv2.findContours(outer_fill.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0]
            ci = cv2.findContours(inner.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)[0]
            p_out = max((cv2.arcLength(t, True) for t in co), default=0.0)
            p_in = max((cv2.arcLength(t, True) for t in ci), default=0.0)
            sw_val = float((outer_fill.sum() - inner.sum()) / max((p_out + p_in) / 2.0, 1.0))
            sw = Estimate(sw_val, 0.5, float(np.clip(1.0 - 0.5 / max(sw_val, 0.5), 0.1, 0.9)))
            stroke_col, smad = _fill_of(ring_full & ~_dilate(~ring_full, 0), 0)
            fill_col = None
            if c.feats.get("fill_id", -1) >= 0:
                fill_col, fmad = _fill_of(_paste((H, W), cands[c.feats["fill_id"]].box, cands[c.feats["fill_id"]].vis), band)
            sf = _make_shapefit(P, fit, (X0, Y0), fill=fill_col, stroke=stroke_col, stroke_w=sw)
            sf.estimates["stroke_mad"] = Estimate(smad, smad, float(np.clip(1 - smad / 30, 0.05, 0.98)))
            notes.append("有外框線" + ("，無填色（中空）" if fill_col is None else ""))
        else:
            fill_col, fmad = _fill_of(vis_full, band)
            sf = _make_shapefit(P, fit, (X0, Y0), fill=fill_col)
            sf.estimates["fill_mad"] = Estimate(fmad, fmad, float(np.clip(1 - fmad / 30, 0.05, 0.98)))
            if c.grad_amp > 12.0:
                notes.append(f"填色有漸層（跨區約 {c.grad_amp:.0f} 色階），fill 為中位色")
        amodal = _raster_full(_shapefit_outer_poly(sf), H, W)
        if fit.std.get("tips_recovered"):
            notes.append(f"有 {int(fit.std['tips_recovered'])} 個尖角被壓縮削掉了（輪廓沿 8×8 區塊被切平），已依兩旁的邊補回；尖角附近的輪廓點不算誤差")
        occ = fit.std.get("occluded_frac", 0.0)
        if occ > 0.02:
            notes.append(f"外形有 {occ:.0%} 被文字或其他區域遮住，已依可見部分補全")
        vis_all = vis_full
        conf = sf.confidence * (0.85 if c.feats["area_px"] < 4 * min_area else 1.0)
        outs.append(_Out("shape", amodal, vis_all & ~unknown, conf, [("shape", conf), ("illustration", 1 - conf)], shape=sf, feats=dict(c.feats, fit_error_px=fit.err_mean), notes=notes))
    # ---- 疊在照片上、但不是基本幾何的平塗色塊：描圖過關才拆成獨立的插畫（之後描成向量圖），否則留在照片裡 ----
    for c in cands:
        if c.role != "complex" or not c.feats.get("overlay"):
            continue
        host = cands[int(c.feats["overlay_host"])]
        vis_full = _paste((H, W), c.box, c.vis)
        traced = _overlay_trace(img, vis_full & ~unknown, P) if P.get("overlay_trace", True) else None
        if traced is not None:
            c.role = "overlay_traced"
            fe = dict(c.feats, **traced["feats"])
            outs.append(_Out("illustration", vis_full, vis_full & ~unknown, 0.7, [("illustration", 0.7), ("photo", 0.1), ("shape", 0.2)],
                             feats=fe, notes=list(c.notes) + traced["notes"]))
        elif host.role == "photo":
            c.role, c.parent = "merged", host.id
            c.feats["overlay"] = 0.0
            if ov is not None:
                ov.reject(host, ["外形不是基本幾何、也描不成向量"], "色塊很乾淨，但外形不是基本幾何、也描不成向量，留在照片裡")
        else:
            c.feats["overlay"] = 0.0
    # ---- 插畫：擬合失敗的平坦區塊與插畫型紋理就近合併 ----
    illus_ids = [c.id for c in cands if c.role in ("complex", "illus")]
    if illus_ids:
        gmask = np.zeros((H, W), bool)
        for i in illus_ids:
            X0, Y0, X1, Y1 = cands[i].box
            gmask[Y0:Y1, X0:X1] |= cands[i].vis
        gl = cv2.connectedComponents(_dilate(gmask, int(P["illus_group_px"])).astype(np.uint8), connectivity=8)[1]
        groups: dict[int, list[int]] = {}
        for i in illus_ids:
            c = cands[i]
            X0, Y0, X1, Y1 = c.box
            g = int(np.bincount(gl[Y0:Y1, X0:X1][c.vis]).argmax())
            groups.setdefault(g, []).append(i)
        for g, ids in groups.items():
            vis = np.zeros((H, W), bool)
            for i in ids:
                X0, Y0, X1, Y1 = cands[i].box
                vis[Y0:Y1, X0:X1] |= cands[i].vis
            hull = _fill_holes(vis | (_dilate(vis, 2) & explained & fg & (L == 0)))
            # 吸收完全落在插畫外形內的小形狀，以及緊貼插畫的多邊形碎塊
            near = _dilate(hull, int(P["illus_group_px"]) + 1)
            for o in list(outs):
                if o.kind != "shape" or o.visible.sum() >= 0.5 * hull.sum() or o.feats.get("overlay"):
                    continue  # 疊在上面的平塗形狀（已判定）不吸收
                inside = (o.visible & hull).sum() >= 0.9 * max(o.visible.sum(), 1)
                touching_poly = o.shape is not None and o.shape.geometry == "polygon" and (o.visible & near).any()
                if inside or touching_poly:
                    vis |= o.visible
                    outs.remove(o)
            for o2 in [c2 for c2 in cands if c2.role == "photo" and c2.feats["area_px"] < 0.5 * hull.sum()]:
                v2 = _paste((H, W), o2.box, o2.vis)
                if (v2 & near).any():
                    vis |= v2
                    o2.role = "merged_illus"
            ill_px = vis & ~unknown
            bb = _bbox_of(hull)
            if bb is None or hull.sum() < min_area:
                continue
            X0, Y0, X1, Y1 = bb
            s, feats = _classify_texture(filled[Y0:Y1, X0:X1], ill_px[Y0:Y1, X0:X1] if ill_px[Y0:Y1, X0:X1].sum() > 30 else hull[Y0:Y1, X0:X1],
                                         grad[Y0:Y1, X0:X1], lap[Y0:Y1, X0:X1], g_thr, sigma, P)
            if s >= 0.5:  # 合併後看起來更像照片
                kind, conf = "photo", s
            else:
                kind, conf = "illustration", 1.0 - s
            outline, amodal, onotes = _outline_fit(hull, ill_px, unknown, fg, L, ids, P, tol, H, W)
            if ov is not None:
                onotes = onotes + [t for t in (ov.summary(i) for i in ids) if t]
            fe = dict(feats, photo_score=s, area_px=float(hull.sum()), n_parts=float(len(ids)))
            outs.append(_Out(kind, amodal, ill_px, float(np.clip(0.5 + 0.5 * abs(conf - 0.5) * 2, 0.5, 0.95)) * (0.85 if len(ids) == 1 else 1.0),
                             sorted([("illustration", 1 - s), ("photo", s), ("shape", 0.0 if len(ids) > 1 else 0.15)], key=lambda t: -t[1]),
                             outline=outline, feats=fe, notes=onotes + ["不是簡單幾何（擬合失敗）的色塊組成"]))
    # ---- 照片 ----
    for c in cands:
        if c.role != "photo":
            continue
        vis = _paste((H, W), c.box, c.vis)
        merged = [m for m in cands if m.role == "merged" and m.parent == c.id]
        for m in merged:
            vis |= _paste((H, W), m.box, m.vis)
        notes = list(c.notes)
        if merged:
            notes.append(f"內含 {len(merged)} 個平坦色塊，視為照片內容（也可能是疊在照片上的形狀）")
        n_over = sum(1 for m in cands if m.feats.get("overlay") and int(m.feats.get("overlay_host", -1)) == c.id and m.role not in ("merged", "complex"))
        summ = ov.summary(c.id) if ov is not None else None
        if summ:
            notes.append(summ)
        if n_over:
            notes.append(f"上面疊了 {n_over} 個平塗圖形，已拆成獨立的元素；照片被蓋住的部分要補（屬於生成）")
        hull = _fill_holes(vis | (_dilate(vis, 2 * band) & explained & fg & (L == 0)))
        outline, amodal, onotes = _outline_fit(hull, vis & ~unknown, unknown, fg, L, [c.id] + [m.id for m in merged], P, tol, H, W)
        s = c.feats["photo_score"]
        conf = float(np.clip(0.5 + abs(s - 0.5), 0.5, 0.95)) * (0.85 if c.feats["area_px"] < 4 * min_area else 1.0)
        outs.append(_Out("photo", amodal, vis & ~unknown, conf, sorted([("photo", s), ("illustration", 1 - s)], key=lambda t: -t[1]),
                         outline=outline, feats=dict(c.feats), notes=notes + onotes))
    # ---- 表格 ----
    for t in tables:
        x0, y0, x1, y1 = t["box"]
        am = np.zeros((H, W), bool)
        am[max(0, y0) : min(H, y1), max(0, x0) : min(W, x1)] = True
        outs.append(_Out("table", am, t["px"], t["conf"], [("table", t["conf"]), ("shape", 1 - t["conf"])], table=t["grid"],
                         feats={"line_width_px": float(t["line_width"])}, notes=t["notes"]))
    for t in _borderless_tables(text_quads, [t["box"] for t in tables], P, H, W):
        x0, y0, x1, y1 = t["box"]
        am = np.zeros((H, W), bool)
        am[max(0, y0) : min(H, y1), max(0, x0) : min(W, x1)] = True
        outs.append(_Out("table", am, np.zeros((H, W), bool), t["conf"], [("table", t["conf"]), ("text", 1 - t["conf"])], table=t["grid"], notes=t["notes"]))
    near_text = _dilate(unknown, 4) if unknown.any() else unknown
    for o in list(outs):
        if o.kind == "table":
            continue
        va = int(o.visible.sum())
        if va < min_area or (va < 6 * min_area and (o.visible & near_text).sum() >= 0.6 * va):
            outs.remove(o)  # 太小，或是緊貼文字的殘渣（文字遮罩沒蓋到的筆畫、JPEG 光暈）
    for t_out in [o for o in outs if o.kind == "table" and o.table is not None and o.table.has_ruling]:
        tb = _bbox_of(t_out.amodal)
        small = [o for o in outs if o.kind in ("photo", "illustration") and o.amodal.sum() < 0.02 * t_out.amodal.sum()
                 and (o.amodal & t_out.amodal).sum() >= 0.95 * o.amodal.sum()]
        for o in small:
            outs.remove(o)
        if small:
            t_out.notes.append(f"表格內有 {len(small)} 個未被文字遮罩涵蓋的小塊（可能是漏抓的文字），未另成區域")
    if full_bleed and not any(o.feats.get("overlay") == 1.0 for o in outs):
        # 滿版照片上沒有找到疊上去的圖形：照原本的讀法（不當成滿版照片）重來，結果與沒有這個功能時相同
        return _find_regions_impl(image, text_mask, dict(P, overlay_full_bleed=False), text_quads)
    regions = _assemble(outs, unknown, bg, H, W)
    dbg = P.get("_debug")
    if isinstance(dbg, list):  # 除錯／調參工具用：各候選的來源、角色與特徵
        dbg.append({"background": bg, "fg_threshold": thr, "grad_threshold": g_thr,
                    "candidates": [{"id": c.id, "source": c.source, "role": c.role, "parent": c.parent, "box": c.box, "color": _to_rgb(c.color),
                                    "spread": c.spread, "feats": dict(c.feats), "vis": c.vis} for c in cands]})
    return regions


def _outline_fit(hull, vis_known, unknown, fg, L, own_ids, P, tol, H, W):
    """照片與插畫的外形擬合；擬合得好就以幾何外形當遮罩（補全被遮住的部分）。"""
    bb = _bbox_of(hull)
    X0, Y0, X1, Y1 = _expand_box(bb, 3, H, W)
    loc = hull[Y0:Y1, X0:X1]
    Ls = L[Y0:Y1, X0:X1]
    own = np.isin(Ls, [i + 1 for i in own_ids])
    other = ((Ls > 0) & ~own & ~loc) | unknown[Y0:Y1, X0:X1]
    bad = _dilate(other, int(P["occlusion_dilate_px"]))
    k = float(P["outline_accept_k"])
    fit = _fit_mask(loc, bad, dict(P, max_polygon_vertices=0), tol * k, allow_line=False, allowed=other)
    notes = []
    if fit is not None and fit.accepted and fit.geometry in ("rect", "rounded_rect", "ellipse"):
        sf = _make_shapefit(P, fit, (X0, Y0))
        amodal = _raster_full(_shapefit_outer_poly(sf), H, W)
        notes.append(f"外形：{fit.geometry}（平均誤差 {fit.err_mean:.2f} px）")
        return sf, amodal, notes
    notes.append("外形不是簡單幾何，以原始遮罩表示")
    return None, hull.copy(), notes


def _assemble(outs: list[_Out], unknown, bg: BackgroundModel, H, W) -> list[Region]:
    n = len(outs)
    if n == 0:
        return []
    areas = np.array([o.amodal.sum() for o in outs], np.float64)
    boxes = [_bbox_of(o.amodal) or (0, 0, 1, 1) for o in outs]
    above: dict[int, set] = {i: set() for i in range(n)}  # i -> 在 i 之上的
    indeg = np.zeros(n, int)
    for i in range(n):
        for j in range(i + 1, n):
            bi, bj = boxes[i], boxes[j]
            x0, y0 = max(bi[0], bj[0]), max(bi[1], bj[1])
            x1, y1 = min(bi[2], bj[2]), min(bi[3], bj[3])
            if x1 <= x0 or y1 <= y0:
                continue
            O = outs[i].amodal[y0:y1, x0:x1] & outs[j].amodal[y0:y1, x0:x1]
            no = int(O.sum())
            if no < 10:
                continue
            vi = int((outs[i].visible[y0:y1, x0:x1] & O).sum())
            vj = int((outs[j].visible[y0:y1, x0:x1] & O).sum())
            if max(vi, vj) < 0.05 * no:
                top, bot = (i, j) if areas[i] < areas[j] else (j, i)
            else:
                top, bot = (i, j) if vi > vj else (j, i)
            if top not in above[bot]:
                above[bot].add(top)
                indeg[top] += 1
    # 拓撲排序：沒有約束時面積大的先（在下）
    z = np.zeros(n, int)
    remaining = set(range(n))
    level = 0
    while remaining:
        ready = [i for i in remaining if indeg[i] == 0]
        if not ready:  # 有環：拿面積最大的打斷
            ready = [max(remaining, key=lambda i: areas[i])]
        i = max(ready, key=lambda i: areas[i])
        z[i] = level
        level += 1
        remaining.discard(i)
        for t in above[i]:
            indeg[t] -= 1
    regions = []
    for i, o in enumerate(outs):
        x0, y0, x1, y1 = boxes[i]
        mask = o.amodal[y0:y1, x0:x1].copy()
        occ = mask & ~o.visible[y0:y1, x0:x1]
        notes = list(o.notes)
        if bg.kind != "flat":
            notes.append(f"背景模型：{bg.kind}")
        regions.append(Region(kind=o.kind, bbox=(int(x0), int(y0), int(x1), int(y1)), mask=mask, confidence=float(np.clip(o.conf, 0.0, 1.0)), z=int(z[i]),
                              shape=o.shape, table=o.table, notes=notes, kind_candidates=[(k, float(round(v, 4))) for k, v in o.kind_cands],
                              features={k: float(v) for k, v in o.feats.items() if isinstance(v, (int, float, np.floating, np.integer))},
                              outline=o.outline, occluded_mask=occ))
    regions.sort(key=lambda r: r.z)
    return regions


# =====================================================================================
# 裁切
# =====================================================================================


def _crop_region_impl(image, region: Region, inpainted, P):
    H, W = image.shape[:2]
    src = inpainted if (inpainted is not None and inpainted.shape[:2] == (H, W)) else image
    x0, y0, x1, y1 = region.bbox
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    w, h = x1 - x0, y1 - y0
    rgb = np.asarray(src)[y0:y1, x0:x1, :3].astype(np.uint8)
    geom = region.outline if region.outline is not None else (region.shape if region.kind == "shape" else None)
    if geom is not None and geom.geometry in ("rect", "rounded_rect", "ellipse", "polygon"):
        alpha = _raster_poly(_shapefit_outer_poly(geom), x0, y0, w, h, int(P["crop_supersample"]))
    else:
        rb = region.bbox
        m = region.mask[y0 - rb[1] : y0 - rb[1] + h, x0 - rb[0] : x0 - rb[0] + w].astype(np.float32)
        s = float(P["crop_feather_px"])
        alpha = cv2.GaussianBlur(m, (0, 0), s, borderType=cv2.BORDER_REPLICATE) if s > 0 else m
    a8 = np.clip(np.round(alpha * 255), 0, 255).astype(np.uint8)
    return np.dstack([rgb, a8])

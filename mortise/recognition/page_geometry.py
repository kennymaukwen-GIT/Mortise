"""頁面幾何（②頁面用）：在照片裡找出頁面四角、由透視反推真實長寬比、透視攤平、影像增強。

座標慣例
    一律是「連續像素座標」：原點在影像左上角的「角」，y 向下；第 i 列第 j 行像素涵蓋
    [j, j+1) × [i, i+1)，中心在 (j+0.5, i+0.5)。所以整張圖的四角是 (0,0)、(W,0)、(W,H)、(0,H)，
    與 truth 的「頁寬為 1」相對座標乘上影像寬度的結果一致。呼叫 OpenCV（像素中心在整數）時
    在內部換算（平移 0.5），對外不暴露。

做法
    find_page_quad
      0. 上游若已判定是截圖或掃描（params["input_type"]），直接以整張圖為頁面。
      1. 候選（縮圖上做，長邊 detect_max_side）：
         a. 邊緣輪廓：Lab 三通道的 Canny（多組門檻）→ 膨脹 → 所有輪廓的凸包；
         b. 顏色區域：以中央區塊的眾數色為種子，取 Lab 色差小於門檻、且連到中央的區域的外輪廓凸包。
         凸包用 approxPolyDP 化成多邊形；超過四個頂點時，反覆拿掉最短的邊（以相鄰兩邊延長相交），
         這樣被遮住或被裁掉的角也能補回來。
         c. 直線組合：Lab 三通道 LSD（長邊 lsd_max_side）→ 合併共線線段 → 以文字中心分上下左右，各取最長幾條，
         另加畫面邊（代表紙超出畫面）→ 窮舉四線組合，以幾何條件剪枝（凸、內角、面積、角落出界距離）。
         所有候選用同一套評分 _cand_score：邊緣支持率 × 面積 × 文字先驗 × 角落延伸。文字先驗來自便宜的
         形態學找字（_text_map，不辨識）：包住與它相交的字群越完整越好、邊不可穿過字、邊外緊貼著還有字要扣分；
         「相鄰兩邊越過角落後仍有邊緣」也扣分（框到紙上的色帶或圖框，例如海報的標題帶被切掉）。
         直線組合的評分用每條直線預先做的沿線累加和（_line_profiles），數千個組合也是 O(1) 查表。
      2. 精修（原圖解析度）：對每條邊等距取樣，沿法線方向用雙線性內插取剖面，
         以「內側→外側」的色差方向投影的一階導數找邊緣峰值，拋物線內插到次像素；
         每條邊對這些邊緣點做 Tukey 雙權重的迭代加權總體最小平方直線擬合（排除手指、文字等離群點），
         四條直線兩兩相交得四角。搜尋半徑由大到小迭代四輪；每輪由導數剖面的半高寬量出邊緣模糊 σ，
         嚴重模糊時自動放大搜尋半徑（radius_per_sigma × σ），並把 blur_err_coef × σ 計入四角誤差。
         候選先粗修（前兩輪），分數接近最佳的再細修完；精修後再用同一套評分（支持率改用精修量到的），
         另乘「邊內外色差」（表格線、印刷框線兩側同色，不是紙邊）。
      2b. 彎曲的邊（_measure_bow）：沿法線較大範圍以動態規劃找連續的邊緣路徑，擬合二次曲線量弓高，
         並看角落附近的路徑是否翹起。判為彎曲時角點不動（避免誤判時把角拉走），弓高計入四角誤差，外框另附（boundary）。
      3. 判定與信心值：四邊都要有足夠的邊緣支持率；四角誤差取「直線擬合誤差（協方差傳播到交點，含模糊）」
         「相鄰邊弓高 × bow_err_factor」「同一張紙其他框法的四角差異」「角落外邊緣延伸的長度」的最大值。
         信心值 = 0.05 + 0.93 × 支持率 × √對比 × 四角誤差（像素）× 角落在畫面外 × 直線度 × 與次佳不同區域候選的差距
         × 長寬比合理度 × 邊外內容 × 角落延伸 × 四角誤差（相對對角線 1%）。
         status：cut_off（角落在畫面外或一條邊貼著畫面邊）、multiple_pages（另一區域的候選分數接近）、
         curved_edge（紙邊彎曲）、low_contrast（信心 < review_conf 或某角誤差 > 對角線 1%）、not_found（照片卻找不到紙）、ok。
         不是 ok 時，上層應請使用者拖曳四角確認；alternatives 列出其他候選四角（第二張紙、被切掉的紙的三邊＋畫面邊）。
         合成照片校準（193 張，見研究筆記與 tests/recognition/test_page_detect_real.py）：信心 ≥ 0.6 的 88 張中，
         四角誤差超過對角線 1% 的 1 張（捲邊的角往外翹，直線交點切掉翹出的部分）。
         四邊都與影像軸平行（axis_tol_deg 內）且影像幾乎沒有感光雜訊時，視為截圖裡的色塊或表格而不是紙張。
         精修全部失敗但縮圖輪廓的四邊梯度支持率都夠高時，退回輪廓近似（method="contour"，低信心、誤差數像素）。
         畫面中有傾斜的長直邊（透視線索）卻湊不成四邊時，判為照片且頁面超出畫面或被遮住。
         找不到可信的四邊：回傳 method="full_image"。若影像像截圖或掃描（無雜訊，或四周邊條是均勻的頁邊色）
         給高信心；否則給低信心（交給上層請使用者手動拉四角），並在 notes 記下找到了哪幾邊。
      4. 長寬比：Zhang 與 He（2007, "Whiteboard scanning and image enhancement"）的方法——
         假設方形像素、主點在影像中心，由四角解出焦距 f，再得真實長寬比。
         以四角誤差做蒙地卡羅傳播得誤差範圍。焦距解不出來或不穩定時：
         近乎正面 → 退回邊長比（aspect_method="side_ratio"）；有明顯單向透視 → 用典型手機焦距的先驗範圍
         （aspect_method="focal_prior"），誤差放大。params["focal_px"]（例如由 EXIF 換算）可直接指定焦距。
    rectify
      由四角與長寬比建立單應矩陣，輸出尺寸依「最長的那一組對邊」決定（那條邊 1:1 取樣，不放大超過原圖密度），
      cv2.warpPerspective 雙線性內插。params["dewarp"] 為真時先交給攤平函數（set_dewarper，預設 dewarp.dewarp），
      判定紙是彎的、而且攤平後的直線度驗證比透視好才採用攤平；找不到四角時也採用它由紙邊量出的四角（至少三邊）。
    enhance
      去陰影與光照不均：縮圖上以形態學閉運算（淺底）或開運算（深底）去掉文字，再中值與高斯平滑得照度圖，
      原圖除以照度圖；白平衡：淺底時把背景參考色拉成中性灰白（增益有上下限）。只供偵測與辨識用，
      量顏色時請用未增強的原圖。

已知限制
    - 鏡頭畸變未校正：廣角近拍時邊不是直線，擬合殘差變大（會反映在誤差與信心值）。
    - 主點假設在影像中心；照片若被裁切過，透視反推的長寬比會有偏差。
    - 紙張與背景完全同色、沒有陰影時，邊緣不可觀測，只能回報 full_image 低信心。
    - 完全正面、四邊與影像軸平行且沒有感光雜訊的照片，會被當成截圖（整張圖就是頁面）。
    - 截圖裡若有非軸向（旋轉過）的大色塊，可能被誤認為紙張。
    - find_page_quad 只給四角：捲起、翹起的角，四角取直線交點，會切掉翹出去的部分（status 會標出、誤差放大）；
      彎曲的攤平在 dewarp.py（rectify 的 params["dewarp"]）。
    - 文字先驗是形態學找字，杯緣、強紋理偶爾會被當成字；所以只當輔助權重，不單獨決定答案。

可單獨升級的點
    - 初值偵測可換成角點熱圖模型（例如 DocAligner 類的 ONNX），精修仍用本模組的直線擬合：_initial_candidates()；
      新來源的四邊形只要放進候選池，就走同一個 _refine 與同一套評分。
    - 文字先驗可換成 OCR 引擎的偵測模型輸出：_text_map() 回傳格式不變即可。
    - 去彎曲：set_dewarper(fn)，fn(原照片, PageQuad 或 None, params) -> DewarpResult 或 None；預設是 dewarp.dewarp
      （UVDoc 只給外框候選，外框與頁內彎曲都由量測決定，驗證比透視好才採用）。
    - enhance 可換成去陰影模型；介面不變。
"""

from __future__ import annotations

import math
from typing import Callable, Optional

import cv2
import numpy as np

from .types import Estimate, PageQuad

DEFAULT_PARAMS: dict = {
    # ---- 上游提示 ----
    "input_type": None,  # ①給的影像類型："photo"、"screenshot"、"scan" 或 None（自行判斷）
    "focal_px": None,  # 已知焦距（像素，以這張影像的尺度）；None 表示由四角反推
    # ---- 初值偵測（縮圖上）----
    "detect_max_side": 900,  # 偵測初值時縮圖的長邊（像素）
    "detect_blur_sigma": 1.2,  # 初值偵測前的高斯模糊 σ（縮圖像素）
    "canny_thresholds": [(10, 30), (25, 70), (50, 140)],  # Canny 的（低、高）門檻組（Lab 梯度）
    "seed_window_frac": 0.3,  # 顏色區域法：中央種子區塊的邊長佔影像的比例
    "region_de_thresholds": [6.0, 10.0, 16.0, 26.0],  # 顏色區域法：與種子色的 Lab 色差門檻（ΔE）
    "approx_eps_fracs": [0.008, 0.015, 0.025, 0.04],  # approxPolyDP 的容差（佔周長的比例），由小到大嘗試
    "max_hull_vertices": 10,  # 多邊形頂點數超過這個就放棄化成四邊形
    "max_candidates_refined": 4,  # 送去原圖精修的候選數上限
    "fine_refine_ratio": 0.6,  # 粗修後分數至少是最佳的這個比例，才接著細修（其餘只做粗修，用於列出其他候選）
    "min_area_frac": 0.10,  # 頁面面積至少佔整張圖的比例
    "max_area_frac": 0.985,  # 超過這個比例視同整張圖
    "min_corner_angle_deg": 30.0,  # 四角的內角下限（度）；太尖的四邊形不可能是透視下的紙張
    "corner_out_max_frac": 0.12,  # 允許角落落在畫面外的距離上限（佔影像對角線的比例）
    # ---- 精修（原圖解析度）----
    "refine_sigma": 1.0,  # 精修前的高斯模糊 σ（像素）
    "refine_radii": [None, 4.0, 2.5, 2.5],  # 各輪沿法線的搜尋半徑（像素）；None 表示依縮圖比例自動決定
    "radius_per_sigma": 2.5,  # 搜尋半徑至少是上一輪量到的邊緣模糊 σ 的幾倍
    "max_search_radius": 48.0,  # 搜尋半徑上限（像素）
    "blur_err_coef": 0.06,  # 四角誤差另加「邊緣模糊 σ × 這個係數」（像素）
    "err_conf_px": 1.5,  # 四角平均誤差達到這個（像素）時，信心值的誤差項降為一半
    "profile_step": 0.5,  # 剖面取樣間距（像素）
    "sample_step": 4.0,  # 沿邊取樣間距（像素）
    "max_samples_per_side": 400,  # 每條邊取樣點上限
    "corner_margin_frac": 0.04,  # 每條邊兩端各略過的比例（避開角落）
    "min_edge_grad": 1.5,  # 邊緣點的最小梯度（色階／像素）
    "rel_edge_grad": 0.3,  # 邊緣點梯度至少是該邊中位數的幾倍
    "min_side_contrast": 4.0,  # 內外色差低於這個（色階）就改用不分方向的梯度大小
    "tukey_c": 4.685,  # Tukey 雙權重常數（以 MAD 尺度為單位）
    "min_resid_scale": 0.25,  # 殘差尺度的下限（像素），避免過度自信
    "fit_iters": 12,  # 每輪直線擬合的 IRLS 次數
    "inlier_px": 1.5,  # 與擬合直線距離在這個之內（或 2.5σ 內）算邊緣支持
    "corr_samples": 4.0,  # 相鄰邊緣點誤差相關的長度（樣本數），用來折算有效樣本數
    "corner_err_floor": 0.08,  # 角點誤差下限（像素）
    # ---- 判定 ----
    "min_side_support": 0.4,  # 每條邊至少要有這個比例的長度有邊緣支持
    "frame_tol_frac": 0.01,  # 四角都在影像四角這個距離內（佔短邊）→ 這張圖本身就是頁面
    "axis_tol_deg": 0.35,  # 四邊與影像軸的夾角都在這之內 → 可能是截圖內的色塊
    "persp_edge_support": 0.6,  # 透視線索：一條傾斜長直邊至少要有這個邊緣支持率
    "persp_edge_min_deg": 1.0,  # 透視線索：與影像軸的夾角至少這麼多（度）
    "persp_edge_min_len_frac": 0.35,  # 透視線索：邊長至少佔影像短邊的比例
    "digital_noise_max": 0.6,  # 雜訊 σ（色階）低於這個視為數位影像（截圖、軟體匯出）
    "border_strip_frac": 0.02,  # 判斷頁邊時取的影像四周邊條寬度（佔短邊）
    "border_uniform_de": 12.0,  # 邊條內像素與邊條中位色差在這之內算均勻（ΔE）
    "border_uniform_frac": 0.92,  # 邊條至少這個比例的像素均勻
    "good_contrast": 20.0,  # 內外色差達到這個（色階）時，對比項的信心值滿分
    "conf_full_digital": 0.9,  # 判為截圖（無雜訊）時 full_image 的信心值
    "conf_full_margin": 0.7,  # 四周是均勻頁邊色時 full_image 的信心值
    "conf_full_fail": 0.15,  # 找不到可信四邊時 full_image 的信心值
    "contour_min_support": 0.6,  # 退路：縮圖上四邊的快速梯度支持率都達到這個，才可用輪廓近似當答案
    "contour_corner_err_px": 2.0,  # 退路：輪廓近似的四角誤差（縮圖像素）
    "conf_contour": 0.35,  # 退路（method="contour"）的信心值
    "glare_note_frac": 0.02,  # 找不到紙時，最大一片過曝區佔畫面這個比例以上，就提示可能有反光、請換角度重拍
    # ---- 直線候選（LSD，縮圖上）----
    "lsd_max_side": 640,  # 跑 LSD 的縮圖長邊（像素）；頁面的邊是長直線，不需要高解析度
    "lsd_min_len_frac": 0.04,  # 線段長度至少佔縮圖短邊的比例
    "lsd_merge_deg": 3.0,  # 合併共線線段：角度差上限（度）
    "lsd_merge_px": 4.0,  # 合併共線線段：線段兩端到直線的距離上限（縮圖像素）
    "lsd_lines_per_side": 7,  # 上下左右各取最長的幾條直線（另加一條畫面邊，代表紙超出畫面）
    "lsd_axis_tol_deg": 35.0,  # 與水平（或垂直）夾角在這之內才算橫線（或直線）
    "lsd_frame_support": 0.35,  # 用畫面邊當作頁面的一條邊時，假設的邊緣支持率
    "lsd_frame_penalty": 0.7,  # 每用一條畫面邊，分數乘上這個
    "lsd_top_combos": 3,  # 直線組合取前幾名（彼此不同的）送去精修
    "cand_min_angle_deg": 40.0,  # 直線組合的內角下限（度）
    "frame_side_px": 3.0,  # 一條邊兩端都在畫面邊這個距離內（縮圖像素）就當成「畫面邊」，不是紙邊
    "text_center_min_blobs": 20,  # 字塊至少這麼多，才用文字中心分上下左右；否則用畫面中心
    # ---- 文字先驗（縮圖上，形態學找字塊，不辨識）----
    "text_kernel_frac": 0.02,  # 黑帽／白帽的核大小（佔縮圖長邊）：要大於筆畫寬
    "text_min_contrast": 16.0,  # 筆畫與周圍至少差這麼多（色階）
    "text_noise_k": 6.0,  # 也至少是雜訊反應（中位數＋這麼多倍的 MAD 尺度）
    "text_peak_min": 40.0,  # 字塊內最強的筆畫至少差這麼多（色階）：印刷字對紙通常遠超過，布紋、木紋達不到
    "text_peak_rel": 0.45,  # 也至少是全圖筆畫強度第 95 百分位的這個比例
    "text_strong_frac": 0.5,  # 一群字塊裡，強筆畫字塊（以面積計）至少佔這個比例才算文字
    "text_max_h_frac": 0.05,  # 字塊高度上限（佔縮圖短邊）
    "text_min_blobs": 5,  # 一群至少幾個字塊才算文字（排除背景紋理的零星雜點）
    "text_line_len_frac": 0.08,  # 壓在這麼長（佔縮圖短邊）以上的直線段上的字塊不算字：紙邊的陰影帶、表格線
    "text_cluster_h": 2.5,  # 文字分群：字塊外擴幾個字高後相連就算同一群
    "text_band_h": 4.0,  # 檢查「邊外緊鄰有沒有字」的帶狀區深度（字高的倍數）
    "text_band_frac": 0.1,  # 也至少是縮圖短邊的這個比例
    "cross_full": 0.25,  # 一條邊穿過字的長度比例到這個值時，該邊扣到下限
    "cross_floor": 0.2,  # 上一項的下限
    "out_text_full": 0.3,  # 邊外帶狀區有字的比例到這個值時，算「邊外緊貼著還有字」
    "out_text_w": 0.35,  # 邊外緊貼著還有字時，該邊乘上（1 − 這個值）
    "ext_len_frac": 0.08,  # 檢查相鄰兩邊越過角落後還有沒有邊緣（佔該邊長）
    "corner_ext_w": 0.6,  # 某個角外還有邊緣延伸時，信心值乘上（1 − 這個值 × 程度）
    "ext_bg_px": 10.0,  # 量背景隨機支持率的平行線往外平移的距離（縮圖像素）
    "ext_w": 0.5,  # 相鄰兩邊越過角落後都還有邊緣（紙還沒結束，例如海報的色帶被切掉）時，該邊乘上（1 − 這個值）
    "step_band_px": 6.0,  # 量邊內外色差的帶狀區寬度（縮圖像素）
    "step_full": 16.0,  # 邊內外帶狀區的色差（ΔE）到這個值才算明確的紙邊；表格線、印刷框線兩側同色，陰影外緣的落差也小
    "step_floor": 0.3,  # 上一項的下限
    "cut_off_end_frac": 0.15,  # 判定「紙超出畫面」時，相鄰兩條邊靠畫面邊這一段（佔邊長）……
    "cut_off_end_support": 0.5,  # ……至少這個比例有邊緣支持（紙邊一路延伸到畫面邊）
    "cut_off_min_step": 8.0,  # 判定「紙超出畫面」時，另外三條邊的內外色差（ΔE）至少要這麼多（確定是紙邊，不是紙上的線）
    # ---- 彎曲紙邊（精修後，原圖解析度）----
    "bow_radius_frac": 0.06,  # 沿法線找邊緣路徑的範圍（佔邊長）
    "bow_radius_min": 6.0,  # 上一項的下限（像素）
    "bow_radius_max": 150.0,  # 上一項的上限（像素）
    "bow_max_slope": 1.0,  # 邊緣路徑每走一步，偏移最多變化「步長 × 這個值」（捲起的角落會快速往外翹）
    "bow_sample_step": 8.0,  # 沿邊取樣間距（像素）
    "bow_min_px": 2.0,  # 弓高（二次曲線與弦的最大距離）超過這個（像素）才可能算彎曲
    "bow_rel": 0.003,  # 也要超過邊長的這個比例
    "bow_err_factor": 3.0,  # 彎曲的邊：相鄰角的誤差至少是弓高的這個倍數（合成捲邊實測：直線交點與實體角相差可達量到弓高的 4 倍）
    "bow_end_valid": 0.6,  # 角落附近的路徑至少這個比例的點可見（有夠強的邊緣），角落偏移才算數
    "bow_end_frac": 0.15,  # 角落附近這個比例的路徑另擬直線，看角落是否翹起（只在一端捲起時，整條的二次曲線看不出來）
    "bow_line_support_g": 0.4,  # 直線（精修的紙邊）上的邊緣強度至少是該邊中位峰值的這個比例，才算「直線這裡有邊緣」
    "bow_line_support_frac": 0.8,  # 直線整段（或角落那一段）有這個比例以上都有邊緣：紙邊是直的，路徑偏開是跟到紙上的印刷，不算彎曲
    # ---- 角點落在背景上（_snap_corners_to_paper）----
    "snap_same_de": 12.0,  # 角內外兩側的色差（ΔE）小於這個……
    "snap_not_paper_de": 25.0,  # ……而且角內側與紙色的色差大於這個，就判定角點不在紙邊上
    "snap_toward_paper": 0.4,  # 沿邊往內走時，邊內側的顏色從角內側的顏色往紙色走了這個比例以上，算「到紙了」
    "snap_max_frac": 0.08,  # 最多沿邊走這個比例的邊長
    # ---- 信心值與狀態 ----
    "review_conf": 0.6,  # 信心值低於這個時 status 不會是 ok（介面據此請使用者拖曳四角；與設定檔 [page] quad_review_confidence 相同）
    "max_corner_err_frac": 0.01,  # 任一角誤差超過影像對角線的這個比例，status 不會是 ok
    "gap_lo": 0.6,  # 次佳的「不同區域」候選分數 ÷ 最佳 ≤ 這個時，信心不扣分
    "gap_hi": 0.9,  # 達到這個時信心乘 0.5（照片裡有兩張差不多可信的紙）
    "alt_iou": 0.3,  # 與最佳候選重疊（IoU）低於這個，算「不同區域」
    "alt_min_ratio": 0.4,  # 分數至少是最佳的這個比例，才列為其他候選
    "max_alternatives": 3,  # 其他候選最多列幾個
    "split_min_ratio": 0.5,  # 兩張並排的紙：框內兩個完整紙張候選的分數至少是外圍大框的這個比例
    "split_min_area": 0.2,  # ……各自至少佔大框面積的這個比例
    "split_min_cover": 0.7,  # ……合起來至少佔大框面積的這個比例
    "same_iou": 0.8,  # 重疊（IoU）高於這個、分數也接近（disagree_ratio）的候選，視為同一張紙的另一種框法
    "disagree_ratio": 0.7,  # 同一張紙的其他框法，分數至少是最佳的這個比例，其四角差異才計入誤差
    "aspect_common": (1.0, 1.294, 1.333, 1.414, 1.647, 1.7, 1.778),  # 常見長寬比（長 ÷ 短）：正方、Letter、4:3、A 系列、Legal、名片、16:9
    "aspect_tol": 0.02,  # 與常見長寬比相差在這個比例內算吻合
    "aspect_odd_factor": 0.85,  # 不吻合時信心值乘上這個（海報尺寸多樣，只輕微加權）
    # ---- 長寬比 ----
    "mc_samples": 64,  # 蒙地卡羅傳播的樣本數
    "mc_min_corner_sigma": 0.3,  # 蒙地卡羅擾動四角時的 σ 下限（像素）
    "focal_min_frac": 0.3,  # 合理焦距下限（佔影像對角線）
    "focal_max_frac": 4.0,  # 合理焦距上限（佔影像對角線）
    "persp_max_rel_err": 0.03,  # 透視反推長寬比的相對誤差超過這個就視為不穩定
    "frontal_k_tol": 0.015,  # 透視強度（|k−1|）低於這個視為近乎正面，可用邊長比
    "focal_prior_fracs": (0.45, 0.6, 1.0),  # 焦距先驗（佔對角線）：下限、典型、上限（手機主鏡頭等效 26 mm 約 0.6）
    "side_ratio_sys_coef": 0.5,  # 邊長比的系統誤差係數：乘上對邊長度比的對數差
    "aspect_conf_scale": 0.02,  # 長寬比相對誤差為這個值時信心值是 0.5
    # ---- 攤平 ----
    "rectify_interp": "linear",  # 內插："linear" 或 "cubic"
    "max_output_side": 12000,  # 攤平輸出長邊上限（像素）
    "dewarp": False,  # rectify 是否先交給攤平函數（set_dewarper；預設 dewarp.dewarp：判定彎曲才攤平，平的紙維持透視）
    # ---- 增強 ----
    "enhance_work_side": 1000,  # 估照度圖時的縮圖長邊（像素）
    "bg_kernel_frac": 0.035,  # 形態學核大小（佔縮圖長邊）：要大於筆畫寬度
    "dark_bg_luma": 100.0,  # 背景亮度中位數低於這個（0 到 255）視為深色底
    "max_gain": 3.0,  # 照度補償的最大增益
    "min_gain": 0.5,  # 照度補償的最小增益
    "white_target": 245.0,  # 淺底時背景要拉到的亮度
    "wb_max_chroma": 40.0,  # 背景參考色的彩度（Lab a、b 的距離）超過這個就不做白平衡（可能是彩色紙）
}

_DEWARPER: Optional[Callable] = None


def set_dewarper(fn: Optional[Callable]) -> None:
    """替換點：註冊攤平函數 fn(原照片, PageQuad 或 None, params) -> DewarpResult（或 None 表示不處理）。
    rectify 在 params["dewarp"] 為真時呼叫它（拿的是原照片，不是透視攤平後的影像，對應圖才能回推到原照片）；
    沒有註冊（或傳 None 恢復預設）時用 mortise.recognition.dewarp.dewarp：判定彎曲才攤平，平的紙維持透視攤平。"""
    global _DEWARPER
    _DEWARPER = fn


def _p(params: dict | None) -> dict:
    p = dict(DEFAULT_PARAMS)
    if params:
        p.update(params)
    return p


# ============================================================ 小工具


def _order_corners(pts: np.ndarray) -> np.ndarray:
    """排成左上、右上、右下、左下（影像方向，順時針），起點取 x+y 最小者。"""
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    c = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    pts = pts[np.argsort(ang)]  # y 向下時 atan2 遞增即順時針
    start = int(np.argmin(pts[:, 0] + pts[:, 1]))
    return np.roll(pts, -start, axis=0)


def _line_through(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, float]:
    d = b - a
    n = np.array([-d[1], d[0]], dtype=np.float64)
    n /= max(np.linalg.norm(n), 1e-12)
    return n, float(n @ a)


def _intersect(l1: tuple[np.ndarray, float], l2: tuple[np.ndarray, float]) -> Optional[np.ndarray]:
    A = np.array([l1[0], l2[0]])
    det = np.linalg.det(A)
    if abs(det) < 1e-9:
        return None
    return np.linalg.solve(A, np.array([l1[1], l2[1]]))


def _poly_area(q: np.ndarray) -> float:
    x, y = q[:, 0], q[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _is_convex(q: np.ndarray) -> bool:
    s = []
    for i in range(4):
        a, b, c = q[i], q[(i + 1) % 4], q[(i + 2) % 4]
        u, v = b - a, c - b
        s.append(u[0] * v[1] - u[1] * v[0])
    s = np.array(s)
    return bool(np.all(s > 0) or np.all(s < 0))


def _corner_angles(q: np.ndarray) -> np.ndarray:
    out = []
    for i in range(4):
        a, b, c = q[i - 1], q[i], q[(i + 1) % 4]
        u, v = a - b, c - b
        cosv = float(u @ v) / max(np.linalg.norm(u) * np.linalg.norm(v), 1e-12)
        out.append(math.degrees(math.acos(max(-1.0, min(1.0, cosv)))))
    return np.array(out)


def _shift_h(h: np.ndarray) -> np.ndarray:
    """連續座標的單應矩陣 → OpenCV（像素中心在整數）的單應矩陣。"""
    t_in = np.array([[1, 0, 0.5], [0, 1, 0.5], [0, 0, 1]], dtype=np.float64)
    t_out = np.array([[1, 0, -0.5], [0, 1, -0.5], [0, 0, 1]], dtype=np.float64)
    return t_out @ h @ t_in


def homography_from_points(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """四組對應點的單應矩陣（float64 直接解 8×8 線性方程，比 cv2.getPerspectiveTransform 的 float32 精確）。"""
    A, b = [], []
    for (x, y), (u, v) in zip(np.asarray(src, dtype=np.float64), np.asarray(dst, dtype=np.float64)):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        b.append(u)
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b.append(v)
    h = np.linalg.solve(np.array(A), np.array(b))
    return np.append(h, 1.0).reshape(3, 3)


def _noise_sigma(gray: np.ndarray) -> float:
    """平坦區的雜訊 σ（色階）：高通殘差在低梯度像素上的 MAD。用原圖中央區塊，避免縮放影響。"""
    h, w = gray.shape
    cs = 512
    y0, x0 = max(0, h // 2 - cs // 2), max(0, w // 2 - cs // 2)
    g = gray[y0 : y0 + cs, x0 : x0 + cs].astype(np.float32)
    if g.size < 64:
        return 0.0
    hp = g - cv2.blur(g, (3, 3))
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.hypot(gx, gy)
    # 排除飽和像素（過曝的白紙、全黑）：雜訊在那裡被截掉，會低估
    sat = (cv2.dilate(g, np.ones((3, 3), np.uint8)) >= 252) | (cv2.erode(g, np.ones((3, 3), np.uint8)) <= 3)
    ok = ~sat
    if ok.mean() < 0.3:  # 大部分飽和（例如白底文件截圖）：飽和像素也一起算
        ok = np.ones_like(ok)
    flat = ok & (mag <= np.percentile(mag[ok], 40))
    if flat.sum() < 32:
        return 0.0
    # 3×3 平均的高通殘差：白雜訊 σ 的 sqrt(8/9) 倍
    return float(1.4826 * np.median(np.abs(hp[flat])) / math.sqrt(8.0 / 9.0))


def _border_uniform(lab: np.ndarray, p: dict) -> bool:
    """影像四周邊條是否都是均勻且一致的顏色（頁邊）。"""
    h, w = lab.shape[:2]
    b = max(2, int(round(p["border_strip_frac"] * min(h, w))))
    strips = [lab[:b], lab[-b:], lab[:, :b], lab[:, -b:]]
    meds = []
    for s in strips:
        s = s.reshape(-1, 3).astype(np.float32)
        m = np.median(s, axis=0)
        frac = float(np.mean(np.linalg.norm(s - m, axis=1) <= p["border_uniform_de"]))
        if frac < p["border_uniform_frac"]:
            return False
        meds.append(m)
    meds = np.array(meds)
    return bool(np.max(np.linalg.norm(meds - meds.mean(axis=0), axis=1)) <= p["border_uniform_de"] * 0.8)


# ============================================================ 初值偵測


def _reduce_polygon(poly: np.ndarray) -> Optional[np.ndarray]:
    """多邊形（>4 頂點）反覆拿掉最短的邊：以它前後兩邊延長相交取代兩個端點。"""
    poly = [np.asarray(v, dtype=np.float64) for v in poly]
    while len(poly) > 4:
        n = len(poly)
        lens = [np.linalg.norm(poly[(i + 1) % n] - poly[i]) for i in range(n)]
        i = int(np.argmin(lens))
        a0, a1 = poly[i - 1], poly[i]  # 前一邊
        b0, b1 = poly[(i + 1) % n], poly[(i + 2) % n]  # 後一邊
        x = _intersect(_line_through(a0, a1), _line_through(b0, b1))
        if x is None:
            return None
        j = (i + 1) % n
        if j == 0:
            poly = [x] + poly[1:i]
        else:
            poly = poly[:i] + [x] + poly[j + 1 :]
    return np.array(poly)


def _hull_to_quads(hull: np.ndarray, p: dict) -> list[np.ndarray]:
    out = []
    peri = cv2.arcLength(hull, True)
    for eps in p["approx_eps_fracs"]:
        ap = cv2.approxPolyDP(hull, eps * peri, True).reshape(-1, 2).astype(np.float64)
        if len(ap) < 4:
            break
        if len(ap) > p["max_hull_vertices"]:
            continue
        q = ap if len(ap) == 4 else _reduce_polygon(ap)
        if q is not None and len(q) == 4:
            out.append(_order_corners(q))
        if len(ap) == 4:
            break
    return out


def _quick_support(mag: np.ndarray, q: np.ndarray, thr: float) -> np.ndarray:
    """縮圖上快速估每條邊的梯度支持率（取樣點附近 ±1.5 像素內的最大梯度是否過門檻）。"""
    h, w = mag.shape
    sup = np.zeros(4)
    for k in range(4):
        a, b = q[k], q[(k + 1) % 4]
        L = np.linalg.norm(b - a)
        if L < 4:
            return np.zeros(4)
        n = int(min(200, max(16, L / 2)))
        t = np.linspace(0.05, 0.95, n)[:, None]
        pts = a + t * (b - a)
        d = (b - a) / L
        nrm = np.array([-d[1], d[0]])
        best = np.zeros(n)
        inside = np.ones(n, dtype=bool)
        for o in (-1.5, -0.75, 0.0, 0.75, 1.5):
            pp = pts + o * nrm - 0.5
            xi = np.round(pp[:, 0]).astype(int)
            yi = np.round(pp[:, 1]).astype(int)
            ok = (xi >= 1) & (xi < w - 1) & (yi >= 1) & (yi < h - 1)
            inside &= ok
            v = np.zeros(n)
            v[ok] = mag[yi[ok], xi[ok]]
            best = np.maximum(best, v)
        if inside.sum() < 4:
            sup[k] = 0.0
        else:
            sup[k] = float(np.mean(best[inside] > thr)) * float(inside.mean())
    return sup


def _initial_candidates(small: np.ndarray, p: dict) -> list[np.ndarray]:
    """縮圖上產生頁面四邊形候選（連續座標，縮圖尺度）。替換點：可改用角點熱圖模型。"""
    h, w = small.shape[:2]
    blur = cv2.GaussianBlur(small, (0, 0), p["detect_blur_sigma"])
    lab = cv2.cvtColor(blur, cv2.COLOR_RGB2LAB)
    area_img = float(h * w)
    hulls = []
    # a. 邊緣輪廓
    kernel = np.ones((3, 3), np.uint8)
    for lo, hi in p["canny_thresholds"]:
        e = cv2.Canny(lab, lo, hi, L2gradient=True)
        e = cv2.dilate(e, kernel)
        cnts, _ = cv2.findContours(e, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        min_a = p["min_area_frac"] * area_img
        for c in cnts:
            if len(c) < 8:
                continue
            _, _, bw_, bh_ = cv2.boundingRect(c)
            if bw_ * bh_ < min_a:  # 凸包面積不會超過外接矩形：先用外接矩形排除小輪廓（結果相同，省時間）
                continue
            hull = cv2.convexHull(c)
            if cv2.contourArea(hull) >= p["min_area_frac"] * area_img:
                hulls.append(hull)
    # b. 顏色區域（由中央的眾數色長出去）
    labf = cv2.cvtColor(cv2.medianBlur(small, 5), cv2.COLOR_RGB2LAB).astype(np.float32)
    sw = p["seed_window_frac"]
    y0, y1 = int(h * (0.5 - sw / 2)), int(h * (0.5 + sw / 2))
    x0, x1 = int(w * (0.5 - sw / 2)), int(w * (0.5 + sw / 2))
    win = labf[y0:y1, x0:x1].reshape(-1, 3)
    qz = (win // 8).astype(np.int32)
    keys = qz[:, 0] * 1024 + qz[:, 1] * 32 + qz[:, 2]
    vals, counts = np.unique(keys, return_counts=True)
    mode = vals[np.argmax(counts)]
    seed = win[keys == mode].mean(axis=0)
    dist = np.linalg.norm(labf - seed, axis=2)
    k7 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    for t in p["region_de_thresholds"]:
        m = (dist < t).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k7)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k7)
        n, lbl, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=4)
        if n <= 1:
            continue
        sub = lbl[y0:y1, x0:x1]
        ids, cnt = np.unique(sub[sub > 0], return_counts=True)
        if len(ids) == 0:
            continue
        cid = ids[np.argmax(cnt)]
        comp = (lbl == cid).astype(np.uint8)
        cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            continue
        c = max(cnts, key=cv2.contourArea)
        hull = cv2.convexHull(c)
        if cv2.contourArea(hull) >= p["min_area_frac"] * area_img:
            hulls.append(hull)
    quads = []
    for hull in hulls:
        for q in _hull_to_quads(hull, p):
            q = q + 0.5  # 輪廓座標（像素中心在整數）→ 連續座標
            quads.append(q)
    return quads


def _dedup(quads: list[np.ndarray], tol: float) -> list[np.ndarray]:
    out: list[np.ndarray] = []
    for q in quads:
        if all(np.max(np.linalg.norm(q - o, axis=1)) > tol for o in out):
            out.append(q)
    return out


# ============================================================ 文字先驗與直線候選（縮圖上）
#
# 所有候選（輪廓、色塊、LSD 直線組合）都用同一套評分 _cand_score：
#   邊緣支持率 × 面積 × 文字先驗（包住多少字、邊有沒有穿過字、邊外緊貼著有沒有字）× 相鄰邊越過角落是否還有邊緣。
# 為了讓 LSD 的數千個四線組合也能快速評分，每條直線先沿線取樣一次、做累加和（_line_profiles），
# 之後任何一段邊的支持率、穿過字的比例、外側帶狀區有字的比例都是 O(1) 查表。
# 單一四邊形（輪廓候選、精修結果）用同一個函數：把它的四條邊當成四條直線來取樣。


def _text_map(small: np.ndarray, p: dict, segs: Optional[np.ndarray] = None) -> dict:
    """便宜的文字偵測（不辨識）：黑帽（淺底深字）與白帽（深底淺字）找出筆畫，連通區依大小與細長度篩成字塊，
    再以字塊外擴後相連分群；字塊少於 text_min_blobs 的群視為背景紋理的雜點丟掉。

    segs：長直線段（縮圖連續座標 x1, y1, x2, y2）；壓在長直線上的字塊（紙邊的陰影帶、表格線碎片）不算字。
    回傳：mask（字塊遮罩）、solid（字塊外擴約半個字高：「這裡有字」）、blobs（N×2 字塊中心，連續座標）、
    mass（N 字塊面積）、cluster（N 群編號 0..C−1）、cmass（C 各群總面積）、h（字高中位數，像素）。
    """
    h, w = small.shape[:2]
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    g = cv2.GaussianBlur(gray, (0, 0), 0.7)
    hp = g.astype(np.float32) - cv2.blur(g, (3, 3)).astype(np.float32)
    noise = float(1.4826 * np.median(np.abs(hp))) / math.sqrt(8.0 / 9.0)
    thr = max(p["text_min_contrast"], p["text_noise_k"] * noise)
    k = max(5, int(round(p["text_kernel_frac"] * max(h, w))) | 1)
    ker = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    hmax = max(6, int(p["text_max_h_frac"] * min(h, w)))
    keep = np.zeros((h, w), np.uint8)
    cents, masses, heights, strongs = [], [], [], []
    line_mask = np.zeros((h, w), np.uint8)
    if segs is not None and len(segs):
        long_ = segs[np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1]) >= p["text_line_len_frac"] * min(h, w)]
        for x1, y1, x2, y2 in long_ - 0.5:
            cv2.line(line_mask, (int(round(x1)), int(round(y1))), (int(round(x2)), int(round(y2))), 1, 5)
    for op in (cv2.MORPH_BLACKHAT, cv2.MORPH_TOPHAT):
        resp = cv2.morphologyEx(g, op, ker)
        # 雜訊造成的反應（JPEG 後的雜訊是成塊的，高通殘差量不到）：以反應本身的中位數與 MAD 估計
        sub = resp[::4, ::4].astype(np.float32)
        med = float(np.median(sub))
        thr_op = max(thr, med + p["text_noise_k"] * 1.4826 * float(np.median(np.abs(sub - med))))
        m = (resp > thr_op).astype(np.uint8)
        n, lbl, st, cen = cv2.connectedComponentsWithStats(m, connectivity=8)
        if n <= 1:
            continue
        # 強筆畫：至少一個像素達到 max(text_peak_min, 全圖筆畫強度第 95 百分位 × text_peak_rel)
        if not m.any():
            continue
        hi = max(p["text_peak_min"], 2.0 * thr_op, p["text_peak_rel"] * float(np.percentile(resp[m > 0], 95)))
        strong = np.zeros(n, bool)
        strong[np.unique(lbl[resp >= hi])] = True
        ww, hh, ar = st[1:, 2], st[1:, 3], st[1:, 4]
        ok = (hh >= 3) & (hh <= hmax) & (ar >= 6) & (ww <= 10 * hh + 2) & (hh <= 6 * ww + 2) & (ar >= 0.1 * ww * hh)
        if line_mask.any():
            on_line = np.bincount(lbl[(line_mask > 0) & (m > 0)], minlength=n)[1:]
            ok &= on_line <= 0.3 * ar
        idx = np.nonzero(ok)[0] + 1
        if len(idx) == 0:
            continue
        lut = np.zeros(n, np.uint8)
        lut[idx] = 1
        keep |= lut[lbl]
        cents.append(cen[idx] + 0.5)
        masses.append(ar[idx - 1].astype(np.float64))
        heights.append(hh[idx - 1].astype(np.float64))
        strongs.append(strong[idx])
    empty = {"mask": np.zeros((h, w), np.uint8), "solid": np.zeros((h, w), np.uint8), "blobs": np.zeros((0, 2)), "mass": np.zeros(0), "cluster": np.zeros(0, int), "cmass": np.zeros(0), "h": 0.0}
    if not cents:
        return empty
    cents, masses, heights, strongs = np.concatenate(cents), np.concatenate(masses), np.concatenate(heights), np.concatenate(strongs)
    hmed = float(np.median(heights[strongs])) if strongs.any() else float(np.median(heights))
    r = max(2, int(round(p["text_cluster_h"] * hmed)))
    dil = cv2.dilate(keep, cv2.getStructuringElement(cv2.MORPH_RECT, (2 * r + 1, 2 * r + 1)))
    nc, clab = cv2.connectedComponents(dil, connectivity=8)
    xi = np.clip((cents[:, 0] - 0.5).astype(int), 0, w - 1)
    yi = np.clip((cents[:, 1] - 0.5).astype(int), 0, h - 1)
    cid = clab[yi, xi]
    cnt = np.bincount(cid, minlength=nc)
    tot = np.bincount(cid, weights=masses, minlength=nc)
    smass = np.bincount(cid, weights=masses * strongs, minlength=nc)
    # 一群要有足夠字塊，而且大部分（以面積計）是強筆畫：印刷字整群都清楚，布紋、木紋只有零星幾個夠強
    good = (cnt >= p["text_min_blobs"]) & (np.arange(nc) > 0) & (smass >= p["text_strong_frac"] * np.maximum(tot, 1e-9))
    sel = good[cid]
    if not sel.any():
        return empty
    keep = keep & good[clab].astype(np.uint8)
    _, remap = np.unique(cid[sel], return_inverse=True)
    rs = max(1, int(round(0.6 * hmed)))
    solid = cv2.dilate(keep, cv2.getStructuringElement(cv2.MORPH_RECT, (2 * rs + 1, 2 * rs + 1)))
    mass = masses[sel]
    return {
        "mask": keep,
        "solid": solid,
        "blobs": cents[sel],
        "mass": mass,
        "cluster": remap,
        "cmass": np.bincount(remap, weights=mass),
        "h": hmed,
    }


def _lsd_segments(small: np.ndarray, p: dict) -> np.ndarray:
    """Lab 三通道各跑一次 LSD（在長邊 lsd_max_side 的縮圖上）。回傳線段 (M, 4)：x1, y1, x2, y2（縮圖連續座標）。"""
    h, w = small.shape[:2]
    s = min(1.0, p["lsd_max_side"] / max(h, w))
    img = cv2.resize(small, (max(8, int(round(w * s))), max(8, int(round(h * s)))), interpolation=cv2.INTER_AREA) if s < 1 else small
    sx, sy = img.shape[1] / w, img.shape[0] / h
    lab = cv2.cvtColor(cv2.GaussianBlur(img, (0, 0), 0.8), cv2.COLOR_RGB2LAB)
    lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
    segs = []
    for ch in range(3):
        found = lsd.detect(np.ascontiguousarray(lab[..., ch]))[0]
        if found is not None:
            segs.append(found.reshape(-1, 4).astype(np.float64))
    if not segs:
        return np.zeros((0, 4))
    S = np.concatenate(segs) + 0.5
    S[:, [0, 2]] /= sx
    S[:, [1, 3]] /= sy
    return S


def _merge_segments(S: np.ndarray, shape: tuple, p: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """依角度與距離合併共線線段成直線（線段長度加權的總體最小平方）。

    回傳（法向量 N (L,2)、偏移 C (L,)：直線為 N·x = C，縮圖連續座標；總長度 (L,)），依長度由大到小。
    """
    h, w = shape
    if len(S) == 0:
        return np.zeros((0, 2)), np.zeros(0), np.zeros(0)
    ln = np.hypot(S[:, 2] - S[:, 0], S[:, 3] - S[:, 1])
    ok = ln >= p["lsd_min_len_frac"] * min(h, w)
    S, ln = S[ok], ln[ok]
    order = np.argsort(-ln)
    S, ln = S[order], ln[order]
    th = np.arctan2(S[:, 3] - S[:, 1], S[:, 2] - S[:, 0]) % math.pi
    tol_th = math.radians(p["lsd_merge_deg"])
    tol_d = p["lsd_merge_px"]
    ln_th: list[float] = []
    ln_n: list[np.ndarray] = []
    ln_c: list[float] = []
    members: list[list[int]] = []
    for i in range(len(S)):
        e1, e2 = S[i, :2], S[i, 2:]
        j_hit = -1
        if ln_th:
            dth = np.abs(np.array(ln_th) - th[i])
            dth = np.minimum(dth, math.pi - dth)
            Nn = np.array(ln_n)
            Cc = np.array(ln_c)
            dd = np.maximum(np.abs(Nn @ e1 - Cc), np.abs(Nn @ e2 - Cc))
            hit = np.nonzero((dth < tol_th) & (dd < tol_d))[0]
            if len(hit):
                j_hit = int(hit[0])
        if j_hit < 0:
            n = np.array([-math.sin(th[i]), math.cos(th[i])])
            ln_th.append(float(th[i]))
            ln_n.append(n)
            ln_c.append(float(n @ (0.5 * (e1 + e2))))
            members.append([i])
        else:
            members[j_hit].append(i)
    N, C, Lt = [], [], []
    for mem in members:
        P = np.concatenate([S[mem, :2], S[mem, 2:], 0.5 * (S[mem, :2] + S[mem, 2:])])
        wts = np.tile(ln[mem], 3)
        c0 = (wts[:, None] * P).sum(axis=0) / wts.sum()
        X = P - c0
        cov = (wts[:, None, None] * (X[:, :, None] * X[:, None, :])).sum(axis=0)
        _, evecs = np.linalg.eigh(cov)
        n = evecs[:, 0]
        N.append(n)
        C.append(float(n @ c0))
        Lt.append(float(ln[mem].sum()))
    N, C, Lt = np.array(N), np.array(C), np.array(Lt)
    o = np.argsort(-Lt)
    return N[o], C[o], Lt[o]


def _line_profiles(N: np.ndarray, C: np.ndarray, frame: np.ndarray, tc: np.ndarray, ctx: dict, p: dict, half_len: Optional[float] = None) -> dict:
    """每條直線沿線（間距 1 縮圖像素）取樣一次，做累加和：邊緣支持、穿過字、外側（離 tc 較遠那側）帶狀區有字。

    參數座標以 tc 在線上的垂足為 t = 0；方向向量 D = (n_y, −n_x)。frame 標記畫面邊（不取樣，給假設值）。
    half_len：只取樣 |t| ≤ 這個值的範圍（單一四邊形用，省時間）；None 表示涵蓋整張圖。
    """
    h, w = ctx["shape"]
    T = int(math.ceil(1.25 * math.hypot(w, h) if half_len is None else half_len))
    ts = np.arange(-T, T + 1, dtype=np.float64)
    D = np.stack([N[:, 1], -N[:, 0]], axis=1)
    tcl = np.broadcast_to(np.asarray(tc, dtype=np.float64), N.shape)  # 每條線各自的參考點（單一四邊形時是它的中心）
    sd = np.sum(N * tcl, axis=1) - C
    P0 = tcl - sd[:, None] * N
    nin = np.where(sd[:, None] >= 0, N, -N)  # 指向 tc 那側（內側）
    X = P0[:, 0:1] + ts[None, :] * D[:, 0:1]
    Y = P0[:, 1:2] + ts[None, :] * D[:, 1:2]

    def look(arr, xx, yy):
        xi = np.rint(xx - 0.5).astype(np.int32)
        yi = np.rint(yy - 0.5).astype(np.int32)
        ok = (xi >= 1) & (xi < w - 1) & (yi >= 1) & (yi < h - 1)
        v = np.zeros(xx.shape, np.float32)
        v[ok] = arr[yi[ok], xi[ok]]
        return v, ok

    mag, thr = ctx["mag"], ctx["thr"]
    best = np.zeros(X.shape, np.float32)
    inside = np.ones(X.shape, bool)
    for o in (-1.5, -0.75, 0.0, 0.75, 1.5):
        v, ok = look(mag, X + o * nin[:, 0:1], Y + o * nin[:, 1:2])
        best = np.maximum(best, v)
        inside &= ok
    sup = ((best > thr) & inside).astype(np.float32)
    # 延長段（越過角落）用「有方向」的支持：梯度要大致垂直於這條線（紙邊），木紋、布紋的斜向紋理不算
    gxm, gym = ctx["gvec"]

    def dir_sup(xx, yy):
        gxv, ok = look(gxm, xx, yy)
        gyv, _ = look(gym, xx, yy)
        gn = np.abs(gxv * N[:, 0:1] + gyv * N[:, 1:2])
        gd = np.abs(gxv * D[:, 0:1] + gyv * D[:, 1:2])
        return (gn > thr) & (gn >= 2.0 * gd) & ok

    supd = np.zeros(X.shape, bool)
    bgs = np.zeros(X.shape, bool)
    for o in (-1.5, 0.0, 1.5):
        supd |= dir_sup(X + o * nin[:, 0:1], Y + o * nin[:, 1:2])
        # 同一條線往外平移 ext_bg_px 的平行線：背景紋理本身的「隨機支持率」，延長段要比它高才算邊緣
        bgs |= dir_sup(X - (p["ext_bg_px"] + o) * nin[:, 0:1], Y - (p["ext_bg_px"] + o) * nin[:, 1:2])
    text = ctx["text"]
    crs = np.zeros(X.shape, np.float32)
    outb = np.zeros(X.shape, np.float32)
    if len(text["blobs"]):
        for o in (-1.0, 0.0, 1.0):
            v, _ = look(text["mask"], X + o * nin[:, 0:1], Y + o * nin[:, 1:2])
            crs = np.maximum(crs, v)
        depth = max(4.0, p["text_band_h"] * text["h"], p["text_band_frac"] * min(h, w))
        offs = np.linspace(2.0, depth, 6)
        for o in offs:
            v, _ = look(text["solid"], X - o * nin[:, 0:1], Y - o * nin[:, 1:2])
            outb += v / len(offs)
    fr = frame.astype(bool)
    sup[fr] = p["lsd_frame_support"]
    crs[fr] = 0.0
    outb[fr] = 0.0
    z = np.zeros((len(N), 1), np.float64)
    return {
        "T": T,
        "nt": len(ts),
        "P0": P0,
        "D": D,
        "nin": nin,
        "cs_sup": np.hstack([z, np.cumsum(sup, axis=1, dtype=np.float64)]),
        "cs_crs": np.hstack([z, np.cumsum(crs, axis=1, dtype=np.float64)]),
        "cs_out": np.hstack([z, np.cumsum(outb, axis=1, dtype=np.float64)]),
        "cs_in": np.hstack([z, np.cumsum(inside, axis=1, dtype=np.float64)]),
        "cs_bg": np.hstack([z, np.cumsum(bgs, axis=1, dtype=np.float64)]),
        "cs_supd": np.hstack([z, np.cumsum(supd, axis=1, dtype=np.float64)]),
        "frame": fr,
    }


def _range_mean(cs: np.ndarray, li: np.ndarray, t0: np.ndarray, t1: np.ndarray, prof: dict) -> np.ndarray:
    """累加和查表：第 li 條線在 t0..t1（t0 ≤ t1）之間的平均值。"""
    T, nt = prof["T"], prof["nt"]
    i0 = np.clip(np.ceil(t0).astype(np.int64) + T, 0, nt)
    i1 = np.clip(np.floor(t1).astype(np.int64) + T + 1, 0, nt)
    cnt = np.maximum(i1 - i0, 1)
    return (cs[li, np.maximum(i1, i0)] - cs[li, i0]) / cnt


def _edge_terms(prof: dict, lines: np.ndarray, corners: np.ndarray, p: dict) -> dict:
    """四邊形（M 個）的逐邊量測。lines (M,4)：第 k 邊所在直線的編號；corners (M,4,2)：第 k 角 = 第 k−1 邊與第 k 邊的交點。"""
    M = len(lines)
    sup = np.zeros((M, 4))
    crs = np.zeros((M, 4))
    out = np.zeros((M, 4))
    ext_after = np.zeros((M, 4))  # 第 k 邊越過終點（第 k+1 角）之後的支持率
    ext_before = np.zeros((M, 4))  # 第 k 邊越過起點（第 k 角）之前的支持率
    for k in range(4):
        li = lines[:, k]
        a, b = corners[:, k], corners[:, (k + 1) % 4]
        P0, D = prof["P0"][li], prof["D"][li]
        ta = np.einsum("ij,ij->i", a - P0, D)
        tb = np.einsum("ij,ij->i", b - P0, D)
        lo, hi = np.minimum(ta, tb), np.maximum(ta, tb)
        L = hi - lo
        m = 0.05 * L
        sup[:, k] = _range_mean(prof["cs_sup"], li, lo + m, hi - m, prof)
        crs[:, k] = _range_mean(prof["cs_crs"], li, lo + m, hi - m, prof)
        out[:, k] = _range_mean(prof["cs_out"], li, lo + m, hi - m, prof)
        e0, e1 = 0.02 * L, (0.02 + p["ext_len_frac"]) * L
        fwd = tb >= ta  # 由起點走向終點時 t 是否遞增

        def ext_of(t0, t1, li=li):
            v = _range_mean(prof["cs_supd"], li, t0, t1, prof)
            bg = _range_mean(prof["cs_bg"], li, t0, t1, prof)
            vis = _range_mean(prof["cs_in"], li, t0, t1, prof)
            v = np.clip((v - bg) / np.maximum(1.0 - bg, 0.2), 0.0, 1.0)  # 扣掉背景紋理的隨機支持率
            return np.where(vis >= 0.5, v, np.nan)  # 延長段大多在畫面外：無從判斷

        after = np.where(fwd, ext_of(tb + e0, tb + e1), ext_of(tb - e1, tb - e0))
        before = np.where(fwd, ext_of(ta - e1, ta - e0), ext_of(ta + e0, ta + e1))
        fr = prof["frame"][li]
        ext_after[:, k] = np.where(fr, np.nan, after)
        ext_before[:, k] = np.where(fr, np.nan, before)
    # 第 k 邊的兩個鄰邊：第 k−1 邊越過第 k 角、第 k+1 邊越過第 k+1 角（往回）；取看得到的那幾段的平均
    pair = np.stack([np.roll(ext_after, 1, axis=1), np.roll(ext_before, -1, axis=1)], axis=2)
    cnt = np.sum(np.isfinite(pair), axis=2)
    ext = np.where(cnt > 0, np.nansum(pair, axis=2) / np.maximum(cnt, 1), 0.0)
    # 第 k 角：第 k−1 邊越過它、或第 k 邊往回越過它，任一條還有邊緣（捲起或翹起的角、框到紙上的區塊）
    ext_corner = np.nan_to_num(np.fmax(np.roll(ext_after, 1, axis=1), ext_before), nan=0.0)
    return {"sup": sup, "crs": crs, "out": out, "ext": ext, "ext_corner": ext_corner}


def _text_inside(text: dict, N: np.ndarray, C: np.ndarray, tc: np.ndarray, lines: np.ndarray, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """每個四邊形包住多少字：回傳（in_frac：與它相交的各文字群中、落在裡面的比例；cover：佔全圖字量的比例）。"""
    M = len(lines)
    if len(text["blobs"]) == 0:
        return np.ones(M), np.ones(M)
    tcl = np.broadcast_to(np.asarray(tc, dtype=np.float64), N.shape)
    sgn = np.where(np.sum(N * tcl, axis=1) - C >= 0, 1.0, -1.0)
    side = ((text["blobs"] @ N.T - C[None, :]) * sgn[None, :] > 0).T  # (線, 字塊)
    side[frame.astype(bool)] = True
    ins = side[lines[:, 0]] & side[lines[:, 1]] & side[lines[:, 2]] & side[lines[:, 3]]  # (M, 字塊)
    nc = len(text["cmass"])
    onehot = np.zeros((len(text["blobs"]), nc), np.float32)
    onehot[np.arange(len(text["blobs"])), text["cluster"]] = text["mass"]
    min_c = ins.astype(np.float32) @ onehot  # (M, 群)
    touched = (min_c > 0) @ text["cmass"]
    tot_in = min_c.sum(axis=1)
    in_frac = np.where(touched > 0, tot_in / np.maximum(touched, 1e-9), 1.0)
    cover = tot_in / max(float(text["cmass"].sum()), 1e-9)
    return in_frac, cover


def _cand_score(t: dict, in_frac: np.ndarray, cover: np.ndarray, area_frac: np.ndarray, n_frame: np.ndarray, p: dict, sup_min_full: float = 0.5) -> tuple[np.ndarray, dict]:
    """所有候選共用的評分。回傳（分數、各因子）。各因子都在 0 到 1 之間。"""
    sup = t["sup"]
    f_edge = sup.mean(axis=1) * np.minimum(1.0, sup.min(axis=1) / sup_min_full)
    f_area = np.sqrt(np.clip(area_frac, 0.0, 1.0))
    f_frame = p["lsd_frame_penalty"] ** n_frame
    # 文字先驗只當輔助（形態學找字會把杯緣、紋理誤認成字）：包住與它相交的字群越完整越好、包住全圖一半以上的字最好
    f_in = (0.5 + 0.5 * in_frac) * (0.75 + 0.25 * np.minimum(1.0, cover / 0.5))
    f_cross = np.prod(np.clip(1.0 - t["crs"] / p["cross_full"], p["cross_floor"], 1.0), axis=1)
    f_outtext = np.prod(1.0 - p["out_text_w"] * np.clip(t["out"] / p["out_text_full"], 0.0, 1.0), axis=1)
    f_ext = np.prod(1.0 - p["ext_w"] * np.clip((t["ext"] - 0.3) / 0.4, 0.0, 1.0), axis=1)
    score = f_edge * f_area * f_frame * f_in * f_cross * f_outtext * f_ext
    return score, {"edge": f_edge, "in": f_in, "cross": f_cross, "outtext": f_outtext, "ext": f_ext}


def _quad_lines(q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """四邊形四條邊所在的直線（N·x = C）。第 k 條是 q[k] → q[k+1]。"""
    N, C = [], []
    for k in range(4):
        n, c = _line_through(q[k], q[(k + 1) % 4])
        N.append(n)
        C.append(c)
    return np.array(N), np.array(C)


def _score_quads(quads: list[np.ndarray], ctx: dict, p: dict, sup_override: Optional[list] = None, sup_min_full: float = 0.5, n_frame: Optional[list] = None) -> tuple[np.ndarray, list[dict]]:
    """單獨的四邊形（縮圖座標）用同一套評分：把每個四邊形的四條邊當成四條直線取樣（全部一次向量化）。
    sup_override：精修量到的支持率（每個四邊形 4 個值）；n_frame：每個四邊形有幾條邊是畫面邊。"""
    M = len(quads)
    if M == 0:
        return np.zeros(0), []
    Q = np.array([np.asarray(q, dtype=np.float64) for q in quads])
    NN, CC, TC = [], [], []
    for q in Q:
        N, C = _quad_lines(q)
        NN.append(N)
        CC.append(C)
        TC.append(np.repeat(q.mean(axis=0)[None, :], 4, axis=0))
    N, C, tc = np.concatenate(NN), np.concatenate(CC), np.concatenate(TC)
    fr = np.zeros(4 * M, bool)
    lmax = max(float(np.linalg.norm(q[(k + 1) % 4] - q[k])) for q in Q for k in range(4))
    prof = _line_profiles(N, C, fr, tc, ctx, p, half_len=(0.6 + p["ext_len_frac"]) * lmax + 4.0)
    lines = np.arange(4 * M).reshape(M, 4)
    t = _edge_terms(prof, lines, Q, p)
    if sup_override is not None:
        t["sup"] = np.asarray(sup_override, dtype=np.float64).reshape(M, 4)
    in_frac, cover = _text_inside(ctx["text"], N, C, tc, lines, fr)
    af = np.array([_poly_area(q) for q in Q]) / (ctx["shape"][0] * ctx["shape"][1])
    nf = np.zeros(M) if n_frame is None else np.asarray(n_frame, dtype=np.float64)
    s, f = _cand_score(t, in_frac, cover, af, nf, p, sup_min_full)
    infos = [{"terms": {k: v[i] for k, v in t.items()}, "factors": {k: float(v[i]) for k, v in f.items()}, "in_frac": float(in_frac[i])} for i in range(M)]
    return s, infos


def _lsd_candidates(small: np.ndarray, ctx: dict, p: dict) -> list[tuple[float, np.ndarray]]:
    """LSD 直線組合候選（縮圖座標）：上下左右各取最長的幾條直線＋畫面邊，窮舉四線組合，先以幾何條件剪枝再評分。"""
    h, w = ctx["shape"]
    N, C, _ = _merge_segments(ctx["segs"], ctx["shape"], p)  # 已依總長度由大到小
    text = ctx["text"]
    if len(text["blobs"]) >= p["text_center_min_blobs"]:
        tc = np.median(text["blobs"], axis=0)
    else:
        tc = np.array([w / 2.0, h / 2.0])
    roles: list[list[int]] = [[], [], [], []]  # 上、右、下、左
    if len(N):
        ang = np.degrees(np.arctan2(N[:, 0], -N[:, 1])) % 180.0  # 直線方向與水平的夾角
        tol = p["lsd_axis_tol_deg"]
        horiz = np.minimum(ang, 180.0 - ang) < tol
        vert = np.abs(ang - 90.0) < tol
        sd = N @ tc - C
        foot = tc[None, :] - sd[:, None] * N  # tc 在線上的垂足：由它相對 tc 的位置決定是哪一邊
        for i in range(len(N)):
            dy, dx = foot[i, 1] - tc[1], foot[i, 0] - tc[0]
            if horiz[i]:
                roles[0 if dy < 0 else 2].append(i)
            elif vert[i]:
                roles[3 if dx < 0 else 1].append(i)
    # 貼著畫面邊的直線（影像邊界的假邊緣）不用：畫面邊另外以 frame_lines 加入
    tolf = p["frame_side_px"]
    def near_border(i: int) -> bool:
        for ax, val in ((0, 0.0), (0, float(w)), (1, 0.0), (1, float(h))):
            if abs(N[i][1 - ax]) < 0.02 and abs(C[i] / max(abs(N[i][ax]), 1e-9) * np.sign(N[i][ax]) - val) <= tolf:
                return True
        return False
    roles = [[i for i in r if not near_border(i)] for r in roles]
    K = p["lsd_lines_per_side"]
    frame_lines = [(np.array([0.0, 1.0]), 0.0), (np.array([1.0, 0.0]), float(w)), (np.array([0.0, 1.0]), float(h)), (np.array([1.0, 0.0]), 0.0)]
    allN, allC, allF, role_idx = [], [], [], []
    for r in range(4):
        idx = []
        for i in roles[r][:K]:  # 已依長度排序
            idx.append(len(allN))
            allN.append(N[i])
            allC.append(C[i])
            allF.append(False)
        idx.append(len(allN))
        allN.append(frame_lines[r][0])
        allC.append(frame_lines[r][1])
        allF.append(True)
        role_idx.append(np.array(idx))
    allN, allC, allF = np.array(allN), np.array(allC), np.array(allF)
    gT, gR, gB, gL = np.meshgrid(*role_idx, indexing="ij")
    lines = np.stack([gT.ravel(), gR.ravel(), gB.ravel(), gL.ravel()], axis=1)

    def inter(i, j):
        a1, b1, c1 = allN[i, 0], allN[i, 1], allC[i]
        a2, b2, c2 = allN[j, 0], allN[j, 1], allC[j]
        det = a1 * b2 - a2 * b1
        ok = np.abs(det) > 1e-9
        det = np.where(ok, det, 1.0)
        x = (c1 * b2 - c2 * b1) / det
        y = (a1 * c2 - a2 * c1) / det
        return np.where(ok[:, None], np.stack([x, y], axis=1), np.nan)

    T_, R_, B_, L_ = lines[:, 0], lines[:, 1], lines[:, 2], lines[:, 3]
    corners = np.stack([inter(L_, T_), inter(T_, R_), inter(R_, B_), inter(B_, L_)], axis=1)  # 左上、右上、右下、左下
    good = np.all(np.isfinite(corners), axis=(1, 2))
    corners = np.where(good[:, None, None], corners, 0.0)
    e = np.roll(corners, -1, axis=1) - corners  # 第 k 邊向量
    cr = e[:, :, 0] * np.roll(e, -1, axis=1)[:, :, 1] - e[:, :, 1] * np.roll(e, -1, axis=1)[:, :, 0]
    good &= np.all(cr > 0, axis=1)  # 凸且順時針（y 向下）
    el = np.linalg.norm(e, axis=2)
    cosang = -np.einsum("mki,mki->mk", np.roll(e, 1, axis=1), e) / np.maximum(np.roll(el, 1, axis=1) * el, 1e-9)
    amin = math.cos(math.radians(180.0 - p["cand_min_angle_deg"]))
    amax = math.cos(math.radians(p["cand_min_angle_deg"]))
    good &= np.all((cosang > amin) & (cosang < amax), axis=1)
    x, y = corners[:, :, 0], corners[:, :, 1]
    area = 0.5 * np.abs(np.sum(x * np.roll(y, -1, axis=1) - y * np.roll(x, -1, axis=1), axis=1))
    af = area / (w * h)
    good &= (af >= p["min_area_frac"]) & (af < p["max_area_frac"])
    dx = np.maximum(0.0, np.maximum(-x, x - w))
    dy = np.maximum(0.0, np.maximum(-y, y - h))
    good &= np.hypot(dx, dy).max(axis=1) <= p["corner_out_max_frac"] * math.hypot(w, h)
    if not good.any():
        return []
    lines, corners, af = lines[good], corners[good], af[good]
    prof = _line_profiles(allN, allC, allF, tc, ctx, p)
    t = _edge_terms(prof, lines, corners, p)
    in_frac, cover = _text_inside(text, allN, allC, tc, lines, allF)
    nb = allF[lines].sum(axis=1)
    score, _ = _cand_score(t, in_frac, cover, af, nb, p)
    score = np.where(nb >= 3, 0.0, score)  # 三條以上是畫面邊：等於整張圖，不當候選
    order = np.argsort(-score)
    out: list[tuple[float, np.ndarray]] = []
    tol = 0.03 * math.hypot(w, h)
    for i in order:
        if score[i] <= 0:
            break
        q = corners[i]
        if all(np.max(np.linalg.norm(q - o, axis=1)) > tol for _, o in out):
            out.append((float(score[i]), q.copy()))
        if len(out) >= p["lsd_top_combos"]:
            break
    return out


def _step_contrast(q: np.ndarray, ctx: dict, p: dict) -> np.ndarray:
    """每條邊內外兩側帶狀區（避開字）的平均 Lab 色差。紙邊兩側是紙與背景；表格線、印刷框線兩側是同一張紙。"""
    h, w = ctx["shape"]
    lab = ctx["lab_s"]
    solid = ctx["text"]["solid"]
    cen = q.mean(axis=0)
    bw = p["step_band_px"]
    out = np.zeros(4)
    for k in range(4):
        a, b = q[k], q[(k + 1) % 4]
        L = float(np.linalg.norm(b - a))
        if L < 4:
            continue
        tdir = (b - a) / L
        nout = np.array([tdir[1], -tdir[0]])
        if (0.5 * (a + b) - cen) @ nout < 0:
            nout = -nout
        ts = np.linspace(0.08, 0.92, int(np.clip(L / 3, 16, 200)))
        base = a[None] + ts[:, None] * (b - a)[None]
        cols = []
        for sgn in (-1.0, 1.0):
            offs = np.linspace(2.0, 2.0 + bw, 4)
            pts = (base[:, None, :] + sgn * offs[None, :, None] * nout[None, None, :]).reshape(-1, 2)
            xi = np.rint(pts[:, 0] - 0.5).astype(int)
            yi = np.rint(pts[:, 1] - 0.5).astype(int)
            ok = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
            xi, yi = xi[ok], yi[ok]
            ok2 = solid[yi, xi] == 0
            if ok2.sum() < 8:
                cols.append(None)
                continue
            cols.append(np.median(lab[yi[ok2], xi[ok2]], axis=0))
        if cols[0] is not None and cols[1] is not None:
            out[k] = float(np.linalg.norm(cols[1] - cols[0]))
        else:
            out[k] = p["step_full"]  # 一側在畫面外：無從判斷，不扣分
    return out


# ============================================================ 精修


def _sample(img: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    mx = (xs - 0.5).astype(np.float32)
    my = (ys - 0.5).astype(np.float32)
    return cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def _fit_line_robust(pts: np.ndarray, w0: np.ndarray, init: tuple[np.ndarray, float], scale0: float, p: dict) -> dict:
    """Tukey 雙權重 IRLS 的總體最小平方直線擬合。回傳法向量、偏移、殘差尺度、權重、協方差資訊。"""
    n_vec, d = init
    w = w0.copy()
    s = max(scale0, p["min_resid_scale"])
    for it in range(p["fit_iters"]):
        r = pts @ n_vec - d
        u = r / (p["tukey_c"] * s)
        w = w0 * np.where(np.abs(u) < 1, (1 - u * u) ** 2, 0.0)
        if w.sum() < 1e-9 or (w > 0).sum() < 3:
            break
        c = (w[:, None] * pts).sum(axis=0) / w.sum()
        X = pts - c
        cov = (w[:, None, None] * (X[:, :, None] * X[:, None, :])).sum(axis=0) / w.sum()
        evals, evecs = np.linalg.eigh(cov)
        n_new = evecs[:, 0]
        if n_new @ n_vec < 0:
            n_new = -n_new
        moved = abs(float(n_new @ c) - d) + float(np.linalg.norm(n_new - n_vec)) * 1e3
        n_vec, d = n_new, float(n_new @ c)
        r = pts @ n_vec - d
        act = w > 0
        mad = 1.4826 * np.median(np.abs(r[act])) if act.any() else s
        s_new = max(mad, p["min_resid_scale"])
        # 尺度只縮不放，避免被離群點拉大
        s_old = s
        s = min(s, s_new) if it > 0 else s_new
        # 已收斂（直線與尺度都幾乎不動）就提早結束：結果與跑滿次數相差在 1e-5 像素以內
        if it >= 2 and moved < 1e-6 and abs(s - s_old) < 1e-6:
            break
    r = pts @ n_vec - d
    act = w > 0
    if act.sum() >= 3:
        c = (w[:, None] * pts).sum(axis=0) / w.sum()
        t_dir = np.array([n_vec[1], -n_vec[0]])
        t = (pts - c) @ t_dir
        wn = w / w.sum()
        n_eff = (w.sum() ** 2) / max((w * w).sum(), 1e-12) / p["corr_samples"]
        sig = float(np.sqrt(np.sum(wn * r * r) / max(1e-9, 1 - (wn * wn).sum())))
        sig = max(sig, 0.05)
        var_off = sig * sig / max(n_eff, 1.0)
        spread = float(np.sum(wn * t * t))
        var_th = var_off / max(spread, 1e-9)
    else:
        c, sig, var_off, var_th = pts.mean(axis=0) if len(pts) else np.zeros(2), 99.0, 99.0, 1.0
    return {"n": n_vec, "d": d, "w": w, "sigma": sig, "scale": s, "c": c, "var_off": var_off, "var_th": var_th}


def _refine(img: np.ndarray, quad: np.ndarray, r0: float, p: dict, rounds: Optional[slice] = None, edge_sigma0: float = 0.0) -> Optional[dict]:
    """在原圖（已模糊的 float32 影像）上以邊緣點直線擬合精修四邊形。

    rounds：只跑 refine_radii 的這幾輪（例如先跑前兩輪粗修、挑出最好的再接著跑後兩輪）；edge_sigma0：接續時上一輪量到的邊緣模糊 σ。
    """
    H, W = img.shape[:2]
    q = quad.astype(np.float64).copy()
    radii = [r0 if r is None else float(r) for r in p["refine_radii"]]
    if rounds is not None:
        radii = radii[rounds]
    edge_sigma = edge_sigma0  # 上一輪量到的邊緣模糊寬度（像素）
    hstep = p["profile_step"]
    res = None
    for R in radii:
        # 搜尋半徑要涵蓋邊緣的模糊寬度（嚴重模糊時自動放大）
        R = float(min(p["max_search_radius"], max(R, p["radius_per_sigma"] * edge_sigma)))
        cen = q.mean(axis=0)
        lines, info = [], []
        for k in range(4):
            a, b = q[k], q[(k + 1) % 4]
            L = float(np.linalg.norm(b - a))
            if L < 8:
                return None
            tdir = (b - a) / L
            nout = np.array([tdir[1], -tdir[0]])
            if (0.5 * (a + b) - cen) @ nout < 0:
                nout = -nout
            ns = int(np.clip(L / p["sample_step"], 16, p["max_samples_per_side"]))
            m = p["corner_margin_frac"]
            ts = np.linspace(m, 1 - m, ns)
            base = a[None, :] + ts[:, None] * (b - a)[None, :]
            offs = np.arange(-R, R + hstep / 2, hstep)
            X = base[:, 0:1] + offs[None, :] * nout[0]
            Y = base[:, 1:2] + offs[None, :] * nout[1]
            valid = np.all((X >= 0.5) & (X <= W - 0.5) & (Y >= 0.5) & (Y <= H - 0.5), axis=1)
            P = _sample(img, X, Y)
            if P.ndim == 2:
                P = P[:, :, None]
            D = (P[:, 2:] - P[:, :-2]) / (2 * hstep)
            so = offs[1:-1]
            if valid.sum() >= 3:
                diff = np.median(P[valid, -1] - P[valid, 0], axis=0)
            else:
                diff = np.zeros(P.shape[2])
            contrast = float(np.linalg.norm(diff))
            if contrast >= p["min_side_contrast"]:
                g = D @ (diff / contrast)
            else:
                g = np.linalg.norm(D, axis=2)
            gi = g[:, 1:-1]
            j = np.argmax(gi, axis=1) + 1
            idx = np.arange(ns)
            y0, y1, y2 = g[idx, j - 1], g[idx, j], g[idx, j + 1]
            den = y0 - 2 * y1 + y2
            delta = np.where(np.abs(den) > 1e-9, 0.5 * (y0 - y2) / np.where(np.abs(den) > 1e-9, den, 1), 0.0)
            delta = np.clip(delta, -0.5, 0.5)
            s_hat = so[j] + delta * hstep
            peak = y1
            # 邊緣寬度：導數剖面在峰值一半以上的連續寬度（FWHM），換算成高斯 σ
            above = g >= 0.5 * peak[:, None]
            jj = np.arange(g.shape[1])[None, :]
            left_edge = np.where(~above & (jj < j[:, None]), jj, -1).max(axis=1)
            right_edge = np.where(~above & (jj > j[:, None]), jj, g.shape[1]).min(axis=1)
            fwhm = (right_edge - left_edge - 1) * hstep
            pts = base + s_hat[:, None] * nout[None, :]
            medpk = np.median(peak[valid]) if valid.any() else 0.0
            keep = valid & (peak >= p["min_edge_grad"]) & (peak >= p["rel_edge_grad"] * medpk)
            init = _line_through(a, b)
            if keep.sum() < 6:
                fit = {"n": init[0], "d": init[1], "w": np.zeros(ns), "sigma": 99.0, "var_off": 99.0, "var_th": 1.0, "c": 0.5 * (a + b)}
                support = 0.0
                inl = np.zeros(ns, bool)
            else:
                fit = _fit_line_robust(pts[keep], np.sqrt(peak[keep]), init, R / 2.0, p)
                r = np.full(ns, np.inf)
                r[keep] = np.abs(pts[keep] @ fit["n"] - fit["d"])
                tol = max(p["inlier_px"], 2.5 * fit["sigma"])
                vis = max(int(valid.sum()), 1)
                inl = (r <= tol) & keep
                support = float(np.sum(inl)) / vis
            lines.append((fit["n"], fit["d"]))
            info.append(
                {
                    "fit": fit,
                    "support": support,
                    "visible": float(valid.mean()),
                    "contrast": contrast,
                    "n_pts": int(keep.sum()),
                    "edge_sigma": float(np.median(fwhm[keep])) / 2.355 if keep.sum() >= 6 else 0.0,
                    "ts": ts,
                    "inl": inl,  # 每個取樣點是否有邊緣支持（沿邊由起點到終點）
                    "valid": valid,
                }
            )
        newq = []
        for k in range(4):
            x = _intersect(lines[k - 1], lines[k])  # 第 k 角 = 第 k−1 邊（左側）與第 k 邊的交點
            if x is None:
                return None
            newq.append(x)
        newq = np.array(newq)
        if not _is_convex(newq):
            return None
        q = newq
        edge_sigma = float(np.median([i["edge_sigma"] for i in info]))
        res = {"corners": q, "lines": lines, "info": info, "edge_sigma": edge_sigma, "radius": R}
    return res


def _corner_errors(res: dict, p: dict) -> np.ndarray:
    q, info = res["corners"], res["info"]
    errs = np.zeros(4)
    for k in range(4):
        e_sq = 0.0
        for side in (k - 1, k):
            f = info[side % 4]["fit"]
            tdir = np.array([f["n"][1], -f["n"][0]])
            dt = float((q[k] - f["c"]) @ tdir)
            e_sq += f["var_off"] + dt * dt * f["var_th"]
        n1, n2 = info[(k - 1) % 4]["fit"]["n"], info[k]["fit"]["n"]
        sin_phi = abs(float(n1[0] * n2[1] - n1[1] * n2[0]))
        errs[k] = math.sqrt(e_sq) / max(sin_phi, 0.2)
    # 模糊造成的系統誤差（角落變圓、邊緣兩側內容不對稱）：與邊緣寬度成正比
    errs = np.hypot(errs, p["blur_err_coef"] * res.get("edge_sigma", 0.0))
    return np.maximum(errs, p["corner_err_floor"])


def _robust_poly(t: np.ndarray, s: np.ndarray, w0: np.ndarray, deg: int, p: dict) -> tuple[np.ndarray, float, np.ndarray]:
    """Tukey 雙權重 IRLS 多項式擬合 s(t)。回傳（係數〔高次在前〕、殘差尺度、權重）。"""
    w = w0.copy()
    coef = np.polyfit(t, s, deg, w=np.sqrt(np.maximum(w, 1e-12)))
    scale = 1.0
    for it in range(8):
        r = s - np.polyval(coef, t)
        act = w0 > 0
        scale = max(1.4826 * float(np.median(np.abs(r[act]))) if act.any() else 1.0, 0.25)
        u = r / (p["tukey_c"] * scale)
        w = w0 * np.where(np.abs(u) < 1, (1 - u * u) ** 2, 0.0)
        if (w > 0).sum() <= deg + 2:
            break
        coef = np.polyfit(t, s, deg, w=np.sqrt(w))
    return coef, scale, w


def _lab_of(rgb: np.ndarray) -> np.ndarray:
    a = np.clip(np.asarray(rgb, np.float32).reshape(-1, 1, 3), 0, 255).astype(np.uint8)
    lab = cv2.cvtColor(a, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    lab[:, 0] *= 100.0 / 255.0
    lab[:, 1:] -= 128.0
    return lab


def _snap_corners_to_paper(img: np.ndarray, q: np.ndarray, p: dict) -> tuple[np.ndarray, list[tuple[int, float]]]:
    """角點的檢查：角內側（沿角平分線往內幾個像素）與角外側顏色幾乎相同、又不像紙（頁內的眾數色）時，
    角點不在紙邊上（例如紙的上緣捲起、上邊的直線跟到了背景的折痕）。這時沿相鄰兩條邊往內走，
    走到「邊內側開始像紙」的地方（最多 snap_max_frac × 邊長），取走得最短的那條邊上的點當新的角。
    回傳（新的四角、[(角的編號, 移動距離)]）。"""
    H, W = img.shape[:2]
    q = q.copy()
    cen = q.mean(axis=0)
    # 紙的顏色：四邊形內縮 15% 的格點顏色的中位數（文件大多是紙色；滿版深色海報就是那個深色）
    uu, vv = np.meshgrid(np.linspace(0.15, 0.85, 12), np.linspace(0.15, 0.85, 12))
    uu, vv = uu.ravel(), vv.ravel()
    grid = ((1 - uu) * (1 - vv))[:, None] * q[0] + (uu * (1 - vv))[:, None] * q[1] + (uu * vv)[:, None] * q[2] + ((1 - uu) * vv)[:, None] * q[3]
    paper = np.median(_lab_of(_sample(img, grid[:, 0], grid[:, 1])), axis=0)
    moved = []
    for k in range(4):
        a = q[k]
        u1 = q[(k - 1) % 4] - a
        u2 = q[(k + 1) % 4] - a
        L1, L2 = float(np.linalg.norm(u1)), float(np.linalg.norm(u2))
        if min(L1, L2) < 40:
            continue
        u1, u2 = u1 / L1, u2 / L2
        bis = u1 + u2
        if np.linalg.norm(bis) < 1e-6:
            continue
        bis /= np.linalg.norm(bis)
        ds = np.linspace(3.0, 8.0, 6)
        pin = a[None, :] + ds[:, None] * bis[None, :]
        pout = a[None, :] - ds[:, None] * bis[None, :]
        okp = np.all((pout >= 0.5) & (pout <= np.array([W - 0.5, H - 0.5])), axis=1) & np.all((pin >= 0.5) & (pin <= np.array([W - 0.5, H - 0.5])), axis=1)
        if okp.sum() < 3:
            continue
        cin = np.median(_lab_of(_sample(img, pin[okp, 0], pin[okp, 1])), axis=0)
        cout = np.median(_lab_of(_sample(img, pout[okp, 0], pout[okp, 1])), axis=0)
        d_io = float(np.linalg.norm(cin - cout))
        d_ip = float(np.linalg.norm(cin - paper))
        if not (d_io < p["snap_same_de"] and d_ip > p["snap_not_paper_de"]):
            continue
        best_d = None
        for u, L, other in ((u1, L1, u2), (u2, L2, u1)):
            # 沿這條邊走；邊的內側方向＝垂直於邊、指向另一條相鄰邊那一側
            n_in = np.array([-u[1], u[0]])
            if n_in @ other < 0:
                n_in = -n_in
            dmax = p["snap_max_frac"] * L
            steps = np.arange(1.0, dmax + 0.5, 1.0)
            if len(steps) == 0:
                continue
            pts = a[None, :] + steps[:, None] * u[None, :] + 4.0 * n_in[None, :]
            inb = np.all((pts >= 0.5) & (pts <= np.array([W - 0.5, H - 0.5])), axis=1)
            if not inb.any():
                continue
            lab = _lab_of(_sample(img, pts[:, 0], pts[:, 1]))
            # 「像紙」：顏色從角內側的顏色往紙色走了 snap_toward_paper 以上（紙在陰影裡會比頁中央暗，不能要求跟紙色一樣）
            axis_ = paper - cin
            prog = ((lab - cin) @ axis_) / max(float(axis_ @ axis_), 1e-6)
            like = (prog >= p["snap_toward_paper"]) & inb
            # 連續 3 個點都像紙才算（避開雜點）
            run = np.convolve(like.astype(int), np.ones(3, int), mode="valid") == 3
            if run.any():
                d = float(steps[int(np.argmax(run))])
                if best_d is None or d < best_d[0]:
                    best_d = (d, a + d * u)
        if best_d is not None:
            q[k] = best_d[1]
            moved.append((k, best_d[0]))
    return q, moved


def _measure_bow(img: np.ndarray, q: np.ndarray, p: dict) -> list[dict]:
    """量每條邊的彎曲：沿法線較大範圍內，以動態規劃找一條連續、平滑、梯度最強的邊緣路徑，再擬合二次曲線。

    回傳每邊 dict：bow（二次曲線與其弦的最大距離＝弓高，像素）、curved（是否判為彎曲）、
    t、s（路徑：沿邊距離與相對直線的法向偏移〔向外為正〕，只含可信的點）、
    a、tdir、nout、L（這條邊的起點、方向、向外法向量、長度）。
    """
    H, W = img.shape[:2]
    cen = q.mean(axis=0)
    out = []
    for k in range(4):
        a, b = q[k], q[(k + 1) % 4]
        L = float(np.linalg.norm(b - a))
        tdir = (b - a) / max(L, 1e-9)
        nout = np.array([tdir[1], -tdir[0]])
        if (0.5 * (a + b) - cen) @ nout < 0:
            nout = -nout
        info = {"bow": 0.0, "curved": False, "t": np.zeros(0), "s": np.zeros(0), "a": a, "tdir": tdir, "nout": nout, "L": L, "valid_frac": 0.0}
        out.append(info)
        if L < 40:
            continue
        R = float(np.clip(p["bow_radius_frac"] * L, p["bow_radius_min"], p["bow_radius_max"]))
        so = max(0.5, R / 40.0)
        offs = np.arange(-R, R + so / 2, so)
        ns = int(np.clip(L / p["bow_sample_step"], 24, 120))
        ts = np.linspace(0.03, 0.97, ns)
        base = a[None, :] + ts[:, None] * (b - a)[None, :]
        X = base[:, 0:1] + offs[None, :] * nout[0]
        Y = base[:, 1:2] + offs[None, :] * nout[1]
        inb = (X >= 0.5) & (X <= W - 0.5) & (Y >= 0.5) & (Y <= H - 0.5)
        P = _sample(img, X, Y)
        if P.ndim == 2:
            P = P[:, :, None]
        j0 = int(np.argmin(np.abs(offs + 3.0)))
        j1 = int(np.argmin(np.abs(offs - 3.0)))
        rows = inb[:, j0] & inb[:, j1]
        if rows.sum() < 8:
            continue
        diff = np.median(P[rows, j1] - P[rows, j0], axis=0)
        con = float(np.linalg.norm(diff))
        D = np.zeros(P.shape[:2])
        Dv = (P[:, 2:] - P[:, :-2]) / (2 * so)
        D[:, 1:-1] = Dv @ (diff / con) if con >= p["min_side_contrast"] else np.linalg.norm(Dv, axis=2)
        D = np.where(inb, D, 0.0)
        pk = D.max(axis=1)
        ref = float(np.median(pk[rows])) if rows.any() else 0.0
        if ref <= 1e-6:
            continue
        G = np.clip(D / ref, -1.0, 2.0)
        # 動態規劃：每步最多移動 J 格；移動與離開直線都有小代價
        J = max(1, int(round(p["bow_max_slope"] * (L / ns) / so)))
        prior = 0.15 * (offs / R) ** 2
        acc = G[0] - prior
        back = np.zeros((ns, len(offs)), np.int16)
        no = len(offs)
        djs = np.arange(-J, J + 1)
        cost = 0.05 * np.abs(djs)[None, :]
        idx = np.arange(no)[:, None] - djs[None, :]  # 從第 j − dj 格走到第 j 格
        valid_idx = (idx >= 0) & (idx < no)
        idx = np.clip(idx, 0, no - 1)
        for i in range(1, ns):
            cand = np.where(valid_idx, acc[idx] - cost, -np.inf)
            a_ = np.argmax(cand, axis=1)
            acc = cand[np.arange(no), a_] + G[i] - prior
            back[i] = djs[a_]
        path = np.zeros(ns, int)
        path[-1] = int(np.argmax(acc))
        for i in range(ns - 1, 0, -1):
            path[i - 1] = path[i] - back[i, path[i]]
        path = np.clip(path, 0, len(offs) - 1)
        gv = G[np.arange(ns), path]
        ok = (gv >= 0.3) & rows
        info["valid_frac"] = float(ok.mean())
        if ok.sum() < 12:
            continue
        # 直線本身（精修量到的紙邊）沿線有沒有邊緣：紙邊真的彎了，弦的中段（或翹起的那一端）就不會有邊緣；
        # 直線整段都有邊緣、路徑卻偏到別處，偏到的是紙上的印刷（例如投影片底部色帶的交界），不是紙邊
        jl = np.abs(offs) <= max(2.0, 2.0 * so)
        line_ok = (G[:, jl].max(axis=1) >= p["bow_line_support_g"]) & rows
        info["line_support"] = float(line_ok[rows].mean())
        tt = ts[ok] * L
        ss = offs[path[ok]]
        info["t"], info["s"] = tt, ss
        coef2, sc2, _ = _robust_poly(tt, ss, gv[ok], 2, p)
        _, sc1, _ = _robust_poly(tt, ss, gv[ok], 1, p)
        span = float(tt.max() - tt.min())
        sag = abs(coef2[0]) * span * span / 4.0
        # 角落附近另擬直線：只在一端捲起、翹起時，整條的二次曲線看不出來，但角點會偏
        ends = []
        tall = ts * L
        step_px = L / ns
        for lo, hi, t_at in ((0.0, p["bow_end_frac"], 0.0), (1.0 - p["bow_end_frac"], 1.0, L)):
            win = (tall >= lo * L) & (tall <= hi * L)
            m = (tt >= lo * L) & (tt <= hi * L)
            e = 0.0
            # 角落附近的路徑要幾乎連續可見、沒有跳格（跳到別的邊緣），偏移才算數；直線在這一段本身就有邊緣時，紙沒有翹起
            if m.sum() >= 5 and ok[win].mean() >= p["bow_end_valid"] and line_ok[win & rows].mean() < p["bow_line_support_frac"]:
                ce, sce, _ = _robust_poly(tt[m], ss[m], gv[ok][m], 1, p)
                jumps = np.abs(np.diff(ss[m]))
                if sce <= 1.5 and (len(jumps) == 0 or jumps.max() <= max(3.0, p["bow_max_slope"] * step_px)):
                    e = abs(float(np.polyval(ce, t_at)))
            ends.append(e)
        thr_b = max(p["bow_min_px"], p["bow_rel"] * L)
        curved_sag = (sag >= max(thr_b, 3.0 * sc2) and sc1 >= 1.2 * sc2 and sc2 <= max(1.0, 0.2 * sag)
                      and info["line_support"] < p["bow_line_support_frac"])
        curved_end = max(ends) >= thr_b
        info["bow"] = float(max(sag if curved_sag else 0.0, max(ends)))
        info["sag"], info["end_off"] = float(sag), (ends[0], ends[1])
        info["coef2"] = coef2
        info["scale2"], info["scale1"] = sc2, sc1
        info["curved"] = bool(info["valid_frac"] >= 0.5 and (curved_sag or curved_end))
    return out


def _boundary(q: np.ndarray, bows: list[dict], per_side: int = 24) -> np.ndarray:
    """沿外框依序取點（左上 → 右上 → 右下 → 左下，不重複起點）。彎曲的邊用量到的邊緣路徑（中值平滑），直的邊用直線。"""
    pts = []
    for k in range(4):
        a, b = q[k], q[(k + 1) % 4]
        bi = bows[k]
        u = np.linspace(0.0, 1.0, per_side, endpoint=False)
        seg = a[None, :] + u[:, None] * (b - a)[None, :]
        if bi["curved"] and len(bi["t"]) >= 5:
            t, sv = bi["t"], bi["s"]
            if len(sv) >= 5:
                pad = np.pad(sv, 2, mode="edge")
                sv = np.median(np.stack([pad[i : i + len(sv)] for i in range(5)]), axis=0)
            off = np.interp(u * bi["L"], t, sv)  # 兩端以最近的量測值延伸
            seg = bi["a"][None, :] + (u * bi["L"])[:, None] * bi["tdir"][None, :] + off[:, None] * bi["nout"][None, :]
        pts.append(seg)
    return np.concatenate(pts)


# ============================================================ 長寬比


def _aspect_core(c: np.ndarray, w: int, h: int, focal: Optional[float]) -> tuple[Optional[float], Optional[float], float]:
    """Zhang & He（2007）：回傳（高÷寬、焦距像素、透視強度）。焦距解不出來時前兩者為 None。"""
    s = math.hypot(w, h)
    pp = np.array([w / 2.0, h / 2.0])
    def hm(v):
        u = (v - pp) / s
        return np.array([u[0], u[1], 1.0])
    m1, m2, m4, m3 = hm(c[0]), hm(c[1]), hm(c[2]), hm(c[3])  # m1 左上、m2 右上、m3 左下、m4 右下
    den2 = np.dot(np.cross(m2, m4), m3)
    den3 = np.dot(np.cross(m3, m4), m2)
    if abs(den2) < 1e-12 or abs(den3) < 1e-12:
        return None, None, 0.0
    k2 = np.dot(np.cross(m1, m4), m3) / den2
    k3 = np.dot(np.cross(m1, m4), m2) / den3
    strength = float(max(abs(k2 - 1), abs(k3 - 1)))
    n2 = k2 * m2 - m1
    n3 = k3 * m3 - m1
    if focal is not None:
        f = focal / s
    else:
        if abs(n2[2]) < 1e-9 or abs(n3[2]) < 1e-9:
            return None, None, strength
        f2 = -(n2[0] * n3[0] + n2[1] * n3[1]) / (n2[2] * n3[2])
        if not np.isfinite(f2) or f2 <= 0:
            return None, None, strength
        f = math.sqrt(f2)
    num = n2[0] ** 2 + n2[1] ** 2 + f * f * n2[2] ** 2
    dnm = n3[0] ** 2 + n3[1] ** 2 + f * f * n3[2] ** 2
    if num <= 0 or dnm <= 0:
        return None, None, strength
    return float(math.sqrt(dnm / num)), float(f * s), strength


def _side_ratio(c: np.ndarray) -> float:
    top = np.linalg.norm(c[1] - c[0])
    bottom = np.linalg.norm(c[2] - c[3])
    left = np.linalg.norm(c[3] - c[0])
    right = np.linalg.norm(c[2] - c[1])
    return float((left + right) / max(top + bottom, 1e-9))


def estimate_aspect(corners: np.ndarray, image_size: tuple[int, int], corner_err: Optional[np.ndarray] = None, params: dict | None = None) -> tuple[Estimate, str, Optional[float]]:
    """由四角（左上、右上、右下、左下）在透視下反推真實長寬比（高 ÷ 寬）。

    image_size 為（寬, 高）像素。回傳（Estimate、方法、焦距像素或 None）。
    方法："perspective"（解出焦距）、"known_focal"（params["focal_px"]）、"side_ratio"（近乎正面）、
    "focal_prior"（單向透視，焦距用先驗範圍）。
    """
    p = _p(params)
    w, h = image_size
    c = np.asarray(corners, dtype=np.float64)
    diag = math.hypot(w, h)
    rng = np.random.default_rng(12345)
    sig = np.full(4, p["mc_min_corner_sigma"]) if corner_err is None else np.maximum(np.asarray(corner_err, dtype=np.float64), p["mc_min_corner_sigma"])
    noise = rng.standard_normal((p["mc_samples"], 4, 2)) * sig[None, :, None]
    samples = c[None] + noise

    def mc(focal):
        vals = []
        for s_ in samples:
            a, _, _ = _aspect_core(s_, w, h, focal)
            if a is not None:
                vals.append(a)
        return np.array(vals)

    a_side = _side_ratio(c)
    top, bottom = np.linalg.norm(c[1] - c[0]), np.linalg.norm(c[2] - c[3])
    left, right = np.linalg.norm(c[3] - c[0]), np.linalg.norm(c[2] - c[1])
    keystone = max(abs(math.log(max(top, 1e-9) / max(bottom, 1e-9))), abs(math.log(max(left, 1e-9) / max(right, 1e-9))))

    def conf_of(rel):
        return float(1.0 / (1.0 + (rel / p["aspect_conf_scale"]) ** 2))

    if p["focal_px"]:
        a0, f0, _ = _aspect_core(c, w, h, float(p["focal_px"]))
        vals = mc(float(p["focal_px"]))
        if a0 is not None and len(vals) > 3:
            err = float(np.std(vals))
            return Estimate(a0, err, conf_of(err / a0)), "known_focal", float(p["focal_px"])
    a0, f0, strength = _aspect_core(c, w, h, None)
    if a0 is not None:
        fs = []
        vals = []
        for s_ in samples:
            a, f, _ = _aspect_core(s_, w, h, None)
            if a is not None:
                vals.append(a)
                fs.append(f)
        ok_frac = len(vals) / len(samples)
        if (
            ok_frac >= 0.9
            and p["focal_min_frac"] * diag <= f0 <= p["focal_max_frac"] * diag
            and np.std(vals) / a0 <= p["persp_max_rel_err"]
        ):
            err = float(math.hypot(np.std(vals), abs(np.median(vals) - a0)))
            return Estimate(a0, err, conf_of(err / a0)), "perspective", f0
    if strength < p["frontal_k_tol"]:
        vals = np.array([_side_ratio(s_) for s_ in samples])
        sys_err = a_side * (p["side_ratio_sys_coef"] * keystone + 0.5 * strength)
        err = float(math.hypot(np.std(vals), sys_err))
        return Estimate(a_side, err, conf_of(err / a_side)), "side_ratio", None
    lo, mid, hi = p["focal_prior_fracs"]
    a_mid, _, _ = _aspect_core(c, w, h, mid * diag)
    ends = [_aspect_core(c, w, h, fr * diag)[0] for fr in (lo, hi)]
    ends = [e for e in ends if e is not None]
    if a_mid is None:
        err = abs(a_side) * 0.15
        return Estimate(a_side, err, conf_of(0.15)), "side_ratio", None
    vals = mc(mid * diag)
    spread = max([abs(e - a_mid) for e in ends] + [0.0]) / 2.0
    err = float(math.sqrt(spread**2 + (np.std(vals) if len(vals) > 3 else 0.0) ** 2))
    return Estimate(a_mid, err, conf_of(err / a_mid)), "focal_prior", None


# ============================================================ 公開介面


def _overexposed_frac(small: np.ndarray) -> float:
    """最大一片過曝區（亮度 ≥ 253、紋理消失）佔畫面的比例：反光蓋住紙邊時找不到紙，提示使用者換角度重拍。"""
    luma = small.astype(np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    sat = cv2.morphologyEx((luma >= 253.0).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lbl, st, _ = cv2.connectedComponentsWithStats(sat, connectivity=8)
    if n <= 1:
        return 0.0
    k = 1 + int(np.argmax(st[1:, 4]))
    if float(np.std(luma[lbl == k])) >= 3.0:
        return 0.0
    return float(st[k, 4]) / float(luma.size)


def _full_image(image: np.ndarray, conf: float, notes: list[str], status: str = "ok", alternatives: Optional[list] = None, alt_scores: Optional[list] = None) -> PageQuad:
    """以整張圖為頁面。status：截圖、掃描（整張圖就是頁面）為 "ok"；照片找不到紙為 "not_found"；紙超出畫面為 "cut_off"。"""
    h, w = image.shape[:2]
    corners = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float64)
    a = h / w
    est = Estimate(a, a * (1.0 / min(w, h)), conf)
    return PageQuad(
        corners=corners,
        confidence=float(conf),
        method="full_image",
        aspect_ratio=float(a),
        notes=notes,
        corner_error_px=np.zeros(4),
        aspect=est,
        aspect_method="image",
        focal_px=None,
        edge_support=None,
        status=status,
        alternatives=list(alternatives or []),
        alternative_scores=list(alt_scores or []),
    )


def find_page_quad(image: np.ndarray, params: dict | None = None) -> PageQuad:
    """找出頁面四角（左上、右上、右下、左下，連續像素座標）、信心值與真實長寬比。"""
    p = _p(params)
    img = np.ascontiguousarray(image[..., :3]) if image.ndim == 3 else np.ascontiguousarray(np.dstack([image] * 3))
    H, W = img.shape[:2]
    notes: list[str] = []
    itype = p["input_type"]
    if itype in ("screenshot", "scan"):
        return _full_image(img, p["conf_full_digital"], [f"上游判定為{'截圖' if itype == 'screenshot' else '掃描'}：整張圖就是頁面"])

    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    noise = _noise_sigma(gray)
    digital = noise < p["digital_noise_max"]

    scale = min(1.0, p["detect_max_side"] / max(H, W))
    small = cv2.resize(img, (max(8, int(round(W * scale))), max(8, int(round(H * scale)))), interpolation=cv2.INTER_AREA) if scale < 1 else img.copy()
    sx, sy = small.shape[1] / W, small.shape[0] / H
    small_lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB)
    margin_uniform = _border_uniform(small_lab, p)

    persp_sides = 0  # 有邊緣支持、且不與影像軸平行的長直邊數（透視線索：照片才會有）
    diag = math.hypot(W, H)
    contour_cand = None
    alt_quads: list[np.ndarray] = []  # 退路時也交給上層的候選四角（例如紙被畫面切掉時的三邊＋畫面邊）
    alt_scores: list[float] = []
    cut_side = False  # 最佳候選有一條邊貼在畫面邊上（紙超出畫面）

    def fallback(reason: str) -> PageQuad:
        glare = _overexposed_frac(small)
        if glare >= p["glare_note_frac"]:
            notes.append(f"畫面上有大片過曝（約 {glare:.0%}，可能是反光蓋住了紙邊或內容）：建議換個角度重拍")
        if contour_cand is not None and not digital and not cut_side:
            qc = _order_corners(contour_cand)
            err = np.full(4, p["contour_corner_err_px"] / min(sx, sy))
            est, amethod, focal = estimate_aspect(qc, (W, H), err, p)
            return PageQuad(
                corners=qc, confidence=p["conf_contour"], method="contour", aspect_ratio=float(est.value),
                notes=notes + [reason, f"邊緣精修失敗（可能嚴重模糊），四角取自縮圖輪廓近似，誤差約 {err[0]:.1f} 像素；建議請使用者確認四角"],
                corner_error_px=err, aspect=est, aspect_method=amethod, focal_px=focal, edge_support=None,
                status="low_contrast", alternatives=list(alt_quads), alternative_scores=list(alt_scores),
            )
        status = "cut_off" if cut_side else "not_found"
        extra = ["紙的一條邊貼著畫面邊（紙超出畫面）；其他候選裡有「三條邊＋畫面邊」的框，可讓使用者確認"] if cut_side and alt_quads else []
        if persp_sides >= 1:
            return _full_image(img, p["conf_full_fail"], notes + [reason, "畫面中有傾斜的長直邊（透視線索），應是照片；找不到完整四邊，以整張圖為頁面；建議請使用者手動拉四角"] + extra, status, alt_quads, alt_scores)
        if digital:
            return _full_image(img, p["conf_full_digital"], notes + [f"影像幾乎無雜訊（σ≈{noise:.2f}），判為截圖或軟體匯出：整張圖就是頁面"])
        if margin_uniform:
            return _full_image(img, p["conf_full_margin"], notes + ["四周是均勻的頁邊色，判為掃描或整頁拍攝：整張圖就是頁面"])
        return _full_image(img, p["conf_full_fail"], notes + [reason, "找不到可信的頁面四邊，以整張圖為頁面；建議請使用者手動拉四角"] + extra, status, alt_quads, alt_scores)

    # ---- 1. 候選（縮圖上）：輪廓、色塊、LSD 直線組合，全部用同一套評分 ----
    blur_s = cv2.GaussianBlur(small, (0, 0), p["detect_blur_sigma"]).astype(np.float32)
    gx = cv2.Sobel(blur_s, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    gy = cv2.Sobel(blur_s, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    m2 = gx * gx + gy * gy
    mag = np.sqrt(m2.max(axis=2))
    ch = m2.argmax(axis=2)[..., None]
    gvec = (np.take_along_axis(gx, ch, axis=2)[..., 0], np.take_along_axis(gy, ch, axis=2)[..., 0])  # 梯度最大那個色版的梯度向量
    thr = max(p["min_edge_grad"] * 1.5, float(np.percentile(mag, 75)))
    area_s = small.shape[0] * small.shape[1]
    segs = _lsd_segments(small, p)
    ctx = {"shape": small.shape[:2], "mag": mag, "gvec": gvec, "thr": thr, "segs": segs, "text": _text_map(small, p, segs), "lab_s": small_lab.astype(np.float32)}
    pre = []
    for q in _dedup(_initial_candidates(small, p), tol=3.0):
        af = _poly_area(q) / area_s
        if af < p["min_area_frac"]:
            continue
        sup = _quick_support(mag, q, thr)
        pre.append((float(np.mean(sup)) * min(1.0, float(np.min(sup)) / 0.5) * math.sqrt(af), q))
    pre.sort(key=lambda t: -t[0])
    pre_q = [q for _, q in pre[: p["max_candidates_refined"] * 2]]
    pool: list[tuple[float, np.ndarray, str]] = []
    if pre_q:
        sc, _ = _score_quads(pre_q, ctx, p)
        pool += [(float(s), q, "contour") for s, q in zip(sc, pre_q)]
    pool += [(s, q, "lsd") for s, q in _lsd_candidates(small, ctx, p)]
    if not pool:
        return fallback("沒有找到夠大的四邊形候選")
    pool.sort(key=lambda t: -t[0])
    chosen: list[np.ndarray] = []
    chosen_src: list[str] = []
    for s, q, src in pool:
        if s <= 0 or len(chosen) >= p["max_candidates_refined"]:
            break
        if all(np.max(np.linalg.norm(q - o, axis=1)) > 3.0 for o in chosen):
            chosen.append(q)
            chosen_src.append(src)
    if not chosen:
        return fallback("候選四邊形分數都是零（沒有邊緣支持）")
    # 退路（精修全部失敗時）：前幾名候選裡，四邊快速支持率最好的那個（優先輪廓候選，貼著實際外形）
    for want in ("contour", "lsd"):
        if contour_cand is not None:
            break
        best_sup = -1.0
        for s_, qs, src in pool[: 2 * p["max_candidates_refined"]]:
            if src != want:
                continue
            qsup = _quick_support(mag, qs, thr)
            key = float(np.min(qsup)) + 0.01 * float(np.mean(qsup)) + 0.001 * _poly_area(qs) / area_s  # 同分時取外圍（較大）的
            if float(np.min(qsup)) >= p["contour_min_support"] and key > best_sup:
                # 退路也要通過紙邊檢查：某條邊兩側同色（頁內的表格框、圖框；不是紙與背景的交界）就不採用。
                # （「框外緊貼著還有字」的文字先驗在這裡不用：木紋等強紋理常被當成字，會把真的紙也排除）
                if float(np.min(_step_contrast(qs, ctx, p))) < p["cut_off_min_step"]:
                    continue
                best_sup, contour_cand = key, qs / np.array([sx, sy])

    # ---- 2. 原圖精修，精修後再用同一套評分（支持率改用精修量到的）----
    img_f = cv2.GaussianBlur(img.astype(np.float32), (0, 0), p["refine_sigma"])
    r0 = 3.0 / min(sx, sy) + 4.0
    scale_s = np.array([sx, sy])
    def judge(res: dict) -> dict:
        """精修結果的幾何檢查與評分（與候選評分同一套，支持率改用精修量到的）。"""
        q = res["corners"]
        sup = np.array([i["support"] for i in res["info"]])
        vis = np.array([i["visible"] for i in res["info"]])
        # 這條邊幾乎整條都在畫面外，或貼著畫面邊（兩端都在同一條畫面邊的 frame_side_px 內）：不是真的紙邊，是紙超出畫面
        on_frame = vis < 0.2
        tol_f = p["frame_side_px"] / min(sx, sy)
        for k in range(4):
            a_, b_ = q[k], q[(k + 1) % 4]
            for dist in (lambda v: abs(v[0]), lambda v: abs(v[0] - W), lambda v: abs(v[1]), lambda v: abs(v[1] - H)):
                if dist(a_) <= tol_f and dist(b_) <= tol_f:
                    on_frame[k] = True
        sup_eff = np.where(on_frame, p["lsd_frame_support"], sup)
        af = _poly_area(q) / (W * H)
        out_d = 0.0
        for v in q:
            dx = max(0.0, -v[0], v[0] - W)
            dy = max(0.0, -v[1], v[1] - H)
            out_d = max(out_d, math.hypot(dx, dy))
        ang = _corner_angles(q)
        geom_ok = (
            p["min_area_frac"] <= af
            and out_d <= p["corner_out_max_frac"] * diag
            and ang.min() >= p["min_corner_angle_deg"]
            and ang.max() <= 180 - p["min_corner_angle_deg"]
        )
        qsm = q * scale_s
        sc, finfo = _score_quads([qsm], ctx, p, sup_override=[sup_eff], sup_min_full=0.6, n_frame=[int(on_frame.sum())])
        step = _step_contrast(qsm, ctx, p)
        f_step = float(np.prod(np.clip(step / p["step_full"], p["step_floor"], 1.0)))
        score = float(sc[0]) * f_step * (1.0 if geom_ok else 0.01)
        fac = dict(finfo[0]["factors"])
        fac["step"] = f_step
        res.update({"support": sup, "on_frame": on_frame, "area_frac": af, "out_d": out_d, "score": score, "geom_ok": geom_ok,
                    "factors": fac, "terms": finfo[0]["terms"], "step": step})
        return res

    # 先粗修（前兩輪）全部候選，挑出分數接近最佳的，再把剩下幾輪跑完（結果與一次跑完相同，只是省掉落選者的細修）
    n_coarse = min(2, len(p["refine_radii"]))
    refined = []
    for qs in chosen:
        res = _refine(img_f, qs / scale_s, r0, p, rounds=slice(0, n_coarse))
        if res is not None:
            refined.append(judge(res))
    refined.sort(key=lambda r: -r["score"])
    if refined and n_coarse < len(p["refine_radii"]):
        top = refined[0]["score"]
        done = []
        for r in refined:
            if r is refined[0] or r["score"] >= p["fine_refine_ratio"] * top:
                res = _refine(img_f, r["corners"], r0, p, rounds=slice(n_coarse, None), edge_sigma0=r["edge_sigma"])
                if res is None:
                    continue
                r = judge(res)
            done.append(r)
        refined = done
    if not refined:
        return fallback("四邊形精修失敗")
    refined.sort(key=lambda r: -r["score"])
    best = refined[0]
    if isinstance(p.get("_debug"), dict):  # 供評估腳本觀察中間結果
        p["_debug"].update({"pool": [(a, b) for a, b, _ in pool], "chosen": chosen, "refined": refined, "ctx": ctx, "scale": scale_s})

    def iou(a: np.ndarray, b: np.ndarray) -> float:
        inter, _ = cv2.intersectConvexConvex(a.astype(np.float32), b.astype(np.float32))
        u = _poly_area(a) + _poly_area(b) - inter
        return float(inter / u) if u > 0 else 0.0

    def accept(r: dict, frame_sides: int = 0) -> bool:
        """可當答案的候選：幾何合理、四邊都有邊緣支持；frame_sides=1 時也接受一條邊貼在畫面邊上（紙被切掉）。"""
        if not r["geom_ok"] or int(r["on_frame"].sum()) > frame_sides:
            return False
        s_ = r["support"][~r["on_frame"]]
        return bool(len(s_) and s_.min() >= p["min_side_support"])

    # 兩張紙貼近並排時，外圍的大框（把兩張一起框住）分數可能最高：框內有兩個互不重疊、都是完整紙張（四邊都是紙與背景的交界）、
    # 合起來幾乎填滿大框的候選，就表示大框是兩張紙。改以其中分數高的那張為答案，大框與另一張列在其他候選（status 會是 multiple_pages）
    a_best = max(_poly_area(best["corners"]), 1e-9)

    def inside_frac(r: dict) -> float:
        inter, _ = cv2.intersectConvexConvex(r["corners"].astype(np.float32), best["corners"].astype(np.float32))
        return float(inter) / max(_poly_area(r["corners"]), 1e-9)

    subs = [r for r in refined[1:] if accept(r) and r["factors"].get("step", 1.0) >= 0.8 and r["score"] >= p["split_min_ratio"] * best["score"]
            and _poly_area(r["corners"]) >= p["split_min_area"] * a_best and inside_frac(r) >= 0.9]
    split = None
    for i in range(len(subs)):
        for j in range(i + 1, len(subs)):
            A, B = subs[i], subs[j]
            if iou(A["corners"], B["corners"]) < 0.05 and _poly_area(A["corners"]) + _poly_area(B["corners"]) >= p["split_min_cover"] * a_best:
                if split is None or A["score"] + B["score"] > split[0]["score"] + split[1]["score"]:
                    split = (A, B) if A["score"] >= B["score"] else (B, A)
    if split is not None:
        merged = best
        best = split[0]
        refined = [best] + [r for r in refined if r is not best]
        notes.append("找到的大框裡其實是兩張並排的紙（兩個互不重疊的完整紙張幾乎填滿大框）：改以其中一張為答案，另一張與大框列在其他候選")
    q = best["corners"]

    others = [r for r in refined[1:] if accept(r, 1)]
    for r in others:
        r["iou"] = iou(r["corners"], q)
        r["dist"] = float(np.max(np.linalg.norm(r["corners"] - q, axis=1)))
    bs = max(best["score"], 1e-12)
    for r in others:
        if r["score"] >= p["alt_min_ratio"] * bs and r["dist"] > 0.02 * diag and len(alt_quads) < p["max_alternatives"]:
            alt_quads.append(r["corners"].copy())
            alt_scores.append(float(r["score"] / bs))
    diff_region = [r for r in others if r["iou"] < p["alt_iou"]]
    gap_ratio = max((r["score"] / bs for r in diff_region), default=0.0)
    f_gap = float(np.clip(1.0 - 0.5 * (gap_ratio - p["gap_lo"]) / (p["gap_hi"] - p["gap_lo"]), 0.5, 1.0))
    same_region = [r for r in others if accept(r) and r["iou"] >= p["same_iou"] and r["score"] >= p["disagree_ratio"] * bs]

    sup = best["support"]
    side_names = ["上", "右", "下", "左"]
    angs = []
    for k in range(4):
        d = q[(k + 1) % 4] - q[k]
        a = math.degrees(math.atan2(d[1], d[0])) % 90.0
        angs.append(min(a, 90.0 - a))
    lens = [float(np.linalg.norm(q[(k + 1) % 4] - q[k])) for k in range(4)]
    persp_sides = sum(
        1
        for k in range(4)
        if sup[k] >= p["persp_edge_support"] and angs[k] > p["persp_edge_min_deg"] and lens[k] >= p["persp_edge_min_len_frac"] * min(W, H)
    )
    found = [side_names[k] for k in range(4) if sup[k] >= p["min_side_support"]]
    if not best["geom_ok"]:
        return fallback("精修後的四邊形不合理（角度、面積或超出畫面太多）")
    if sup.min() < p["min_side_support"]:
        miss = [side_names[k] for k in range(4) if sup[k] < p["min_side_support"]]
        msg = f"只找到{'、'.join(found) or '零'}邊（{'、'.join(miss)}邊缺乏邊緣支持，頁面可能超出畫面、被遮住或與背景同色）"
        weak = sup < p["min_side_support"]
        # 紙超出畫面（cut_off）要有把握才標：恰好一條邊貼著畫面邊，另外三條都量到、而且都是紙與背景的交界（內外有色差）。
        # 這時 alternatives[0]（三條邊＋畫面邊）可以直接預選；其他找不到的情況標 not_found，不讓介面預選錯的框
        strong = ~weak & (np.asarray(best["step"]) >= p["cut_off_min_step"])
        if int(weak.sum()) == 1 and np.all(best["on_frame"][weak]) and int(strong.sum()) == 3 and best["geom_ok"]:
            # 相鄰兩條邊要一路延伸到畫面邊（紙真的超出畫面）；在畫面邊之前就斷了（例如被反光、手遮住），那條邊其實在畫面內，只是看不到
            k = int(np.flatnonzero(weak)[0])

            def end_support(side: int, at_end: bool) -> float:
                inf = best["info"][side]
                ts_, inl, val = inf["ts"], inf["inl"], inf["valid"]
                win = (ts_ >= 1.0 - p["cut_off_end_frac"]) if at_end else (ts_ <= p["cut_off_end_frac"])
                win &= val
                return float(inl[win].mean()) if win.any() else 0.0

            if min(end_support((k - 1) % 4, True), end_support((k + 1) % 4, False)) >= p["cut_off_end_support"]:
                cut_side = True
            alt_quads.insert(0, q.copy())
            alt_scores.insert(0, 1.0)
            del alt_quads[p["max_alternatives"]:]
            del alt_scores[p["max_alternatives"]:]
        return fallback(msg)
    if best["area_frac"] >= p["max_area_frac"]:
        return fallback("四邊形幾乎等於整張圖")
    frame = np.array([[0, 0], [W, 0], [W, H], [0, H]], dtype=np.float64)
    if np.max(np.linalg.norm(q - frame, axis=1)) <= p["frame_tol_frac"] * min(W, H):
        return _full_image(img, p["conf_full_digital"] if digital else p["conf_full_margin"], ["頁面邊緣就是影像邊緣：整張圖就是頁面"])
    # 截圖內的色塊、表格：四邊與影像軸平行且無雜訊
    if max(angs) <= p["axis_tol_deg"] and digital:
        return _full_image(img, p["conf_full_digital"], ["找到的四邊形與影像軸完全平行且影像無雜訊：判為截圖內的色塊或表格，整張圖就是頁面"])

    # ---- 2c. 角點落在背景上（例如紙的上緣捲起，上邊的直線被布料的折痕帶走）：沿相鄰的邊往內找到紙的實體角 ----
    q, moved = _snap_corners_to_paper(img_f, q, p)
    for k, d in moved:
        notes.append(f"{['左上', '右上', '右下', '左下'][k]}角原本落在背景上（角內外顏色相同、不像紙），已沿邊往內移到紙的實體角（{d:.1f} 像素）；誤差已計入")

    # ---- 3. 彎曲的邊：沿法線找連續的邊緣路徑、擬合二次曲線量弓高；彎曲時角點改用角落附近的路徑求 ----
    bows = _measure_bow(img_f, q, p)
    curved = [k for k in range(4) if bows[k]["curved"]]
    bow_px = [float(b["bow"]) if b["curved"] else 0.0 for b in bows]
    boundary = None
    if curved:
        # 角點不移動（沿用直線精修的結果，避免誤判彎曲時把角拉走）；彎曲的程度計入四角誤差，外框另附給攤平用
        boundary = _boundary(q, bows)

    # ---- 4. 四角誤差：直線擬合誤差、紙邊弓高、同一張紙其他框法的分歧，三者取大 ----
    cerr_fit = _corner_errors(best, p)
    cerr_bow = p["bow_err_factor"] * np.array([max(bow_px[k - 1], bow_px[k]) for k in range(4)])
    cerr_dis = np.zeros(4)
    for r in same_region:
        cerr_dis = np.maximum(cerr_dis, np.linalg.norm(r["corners"] - q, axis=1))
    cerr = np.maximum(np.maximum(cerr_fit, cerr_bow), cerr_dis)
    for k, d in moved:  # 移過的角：誤差至少是移動量的一半（直線模型在這裡不成立）
        cerr[k] = max(cerr[k], 0.5 * d)
    est, amethod, focal = estimate_aspect(q, (W, H), cerr, p)
    contrast = min(i["contrast"] for i in best["info"])

    # ---- 5. 信心值 ----
    # 邊緣支持率 × 對比 × 四角誤差 × 是否有角落在畫面外 × 直線度 × 與次佳不同區域候選的差距 × 長寬比合理度 × 邊外內容
    f_sup = float(np.clip((sup.min() - 0.3) / 0.5, 0.0, 1.0))
    f_con = float(np.clip(contrast / p["good_contrast"], 0.3, 1.0))
    f_err = 1.0 / (1.0 + (float(np.mean(cerr)) / p["err_conf_px"]) ** 2)
    f_vis = 0.6 if best["out_d"] > 0.5 else 1.0
    short = min(lens)
    f_straight = 1.0 / (1.0 + (max(bow_px) / max(0.01 * short, 1e-9)) ** 2)
    ratio = max(est.value, 1.0 / max(est.value, 1e-9))
    rel = est.error / max(est.value, 1e-9)
    f_aspect = 1.0 if any(abs(ratio / c - 1.0) <= max(p["aspect_tol"], 2.0 * rel) for c in p["aspect_common"]) else p["aspect_odd_factor"]
    fac = best["factors"]
    f_out = float(fac["outtext"] * fac["ext"] * fac["step"])
    # 角落外還有邊緣延伸：最常見的是捲起、翹起的角（直線交點會切掉翹出去的部分），也可能框到紙上的區塊
    oc = np.asarray(best["terms"]["ext_corner"], dtype=np.float64)
    over = np.clip((oc - 0.3) / 0.4, 0.0, 1.0)
    f_corner = float(np.prod(1.0 - p["corner_ext_w"] * over))
    if over.any():
        lens_c = np.array([max(lens[k - 1], lens[k]) for k in range(4)])
        cerr = np.where(over > 0, np.maximum(cerr, over * (0.02 + p["ext_len_frac"]) * lens_c), cerr)
    if best.get("edge_sigma", 0.0) > 4.0:
        notes.append(f"影像模糊（邊緣寬度 σ≈{best['edge_sigma']:.1f} 像素），四角誤差放大")
    # 自己估的四角誤差（含弓高、分歧、角落延伸）相對於「對角線 1%」的容許量：誤差估到容許量時信心減半
    f_rel = 1.0 / (1.0 + (float(cerr.max()) / (p["max_corner_err_frac"] * diag)) ** 2)
    conf = 0.05 + 0.93 * f_sup * math.sqrt(f_con) * f_err * f_vis * f_straight * f_gap * f_aspect * f_out * f_corner * f_rel
    if isinstance(p.get("_debug"), dict):
        p["_debug"]["conf_factors"] = {"sup": f_sup, "con": f_con, "err": f_err, "vis": f_vis, "straight": f_straight, "gap": f_gap, "aspect": f_aspect, "out": f_out, "corner": f_corner, "rel": f_rel, "cerr": cerr.copy()}
    if best["out_d"] > 0.5:
        notes.append("有角落落在畫面外，由邊線延長相交推得")
    if sup.min() < 0.75:
        notes.append(f"部分邊緣被遮住或對比低（最低支持率 {sup.min():.2f}）")
    if contrast < p["good_contrast"]:
        notes.append(f"頁面與背景對比低（最低內外色差 {contrast:.1f} 色階）")
    if amethod in ("side_ratio", "focal_prior"):
        notes.append("透視解不穩定，長寬比" + ("退回邊長比" if amethod == "side_ratio" else "用焦距先驗範圍估計，誤差較大"))
    if curved:
        notes.append(f"{'、'.join(side_names[k] for k in curved)}邊彎曲（弓高最大 {max(bow_px):.1f} 像素）：紙張捲起或彎曲，四角仍取直線交點（彎出、翹起的部分可能被切掉，誤差已放大），外框另附；建議攤平（去彎曲）並請使用者確認")
    if gap_ratio > p["gap_lo"]:
        notes.append(f"照片裡另有分數接近的紙（次佳 ÷ 最佳 = {gap_ratio:.2f}），已列在其他候選；建議請使用者選擇")
    if fac["outtext"] < 0.8:
        notes.append("某條邊外側緊貼著還有字：頁面可能比框到的更大（例如標題色帶被切掉）")
    if fac["ext"] < 0.8:
        notes.append("相鄰兩邊越過角落後仍有邊緣：框到的可能是紙上的色塊或圖框，紙還沒結束")
    if over.any():
        notes.append(f"{'、'.join(['左上', '右上', '右下', '左下'][k] for k in range(4) if over[k] > 0)}角外還有邊緣延伸：可能是捲起或翹起的角（直線交點會切掉翹出去的部分），四角誤差已放大")
    if fac["step"] < 0.8:
        notes.append("某條邊兩側顏色幾乎相同：可能是紙上的表格線或框線，不是紙邊")
    if f_aspect < 1.0:
        notes.append(f"長寬比 {ratio:.3f} 不是常見紙張比例（只輕微降低信心）")
    if same_region and float(cerr_dis.max()) > 1.0:
        notes.append(f"同一張紙的其他框法與此相差最多 {cerr_dis.max():.1f} 像素，已計入四角誤差")
    # ---- 6. 狀態：不是 ok 時，介面請使用者拖曳四角確認 ----
    if best["out_d"] > 0.5:
        status = "cut_off"
    elif gap_ratio > p["gap_lo"]:
        status = "multiple_pages"
    elif curved:
        status = "curved_edge"
    elif conf < p["review_conf"] or float(cerr.max()) > p["max_corner_err_frac"] * diag:
        status = "low_contrast"
    else:
        status = "ok"
    if status == "low_contrast":
        notes.append(f"信心值 {conf:.2f} 偏低或某角誤差過大（{cerr.max():.1f} 像素）：建議請使用者確認四角")
    return PageQuad(
        corners=q,
        confidence=float(conf),
        method="edge_fit",
        aspect_ratio=float(est.value),
        notes=notes,
        corner_error_px=cerr,
        aspect=est,
        aspect_method=amethod,
        focal_px=focal,
        edge_support=[float(v) for v in sup],
        status=status,
        alternatives=alt_quads,
        alternative_scores=alt_scores,
        boundary=boundary,
        edge_bow_px=bow_px,
    )


def rectify(image: np.ndarray, quad: Optional[PageQuad], params: dict | None = None, *, return_dewarp: bool = False):
    """攤平。回傳（攤平後影像、3×3 單應矩陣：原圖連續座標 → 攤平影像連續座標）；return_dewarp=True 時多回傳 DewarpResult 或 None。

    params["dewarp"] 為假（預設）：只做透視攤平（與以前完全相同）。
    params["dewarp"] 為真：先把「原照片」交給攤平函數（set_dewarper 註冊的，預設 dewarp.dewarp）。
      它判定紙是彎的、而且攤平後的直線度驗證比透視好，才採用攤平結果（DewarpResult.applied）；這時單應矩陣只是
      由攤平後四角求的近似，逐像素的對應在 DewarpResult.map_x、map_y（輸出像素中心 → 原照片）。
      平的紙、或攤平不可信時維持透視攤平。攤平函數出錯時也退回透視攤平（錯誤寫在 DewarpResult.notes）。
    quad 為 None 或 method="full_image"（找不到四角）時：開了攤平就讓攤平函數試（它用學習式模型找外框當候選，
      再由紙邊量出四角）；至少三邊有量測支持時採用它的結果（DewarpResult.pseudo_quad，平的紙只做透視），
      上層應把這組四角當成「需要使用者確認」。沒開、或量得到的紙邊不到三條，就交回原圖與單位矩陣
      （學習式模型的外框只放在 DewarpResult.candidate_outline 當候選）。"""
    p = _p(params)
    dres = None
    if p["dewarp"]:
        fn = _DEWARPER
        if fn is None:
            from .dewarp import dewarp as fn
        try:
            dres = fn(image, quad, p)
        except Exception as e:  # noqa: BLE001 —— 攤平失敗不中斷：退回透視
            from .dewarp import DewarpResult

            dres = DewarpResult(image=None, map_x=None, map_y=None, applied=False, source="", curved=False,
                                notes=[f"攤平失敗（{type(e).__name__}: {e}），維持透視攤平。"])
        no_quad = quad is None or quad.method == "full_image"
        # 找不到四角時，攤平函數由紙邊量出的四角（至少三邊有量測支持）也採用：平的紙做透視、彎的紙攤平；上層要請使用者確認
        use_pseudo = no_quad and dres is not None and getattr(dres, "pseudo_quad", None) is not None and getattr(dres, "measured_sides", 0) >= 3
        if dres is not None and dres.image is not None and (dres.applied or use_pseudo):
            Hm = dres.homography if dres.homography is not None else np.eye(3)
            return (dres.image, Hm, dres) if return_dewarp else (dres.image, Hm)
    if quad is None or quad.method == "full_image":
        out = np.ascontiguousarray(image)
        return (out, np.eye(3), dres) if return_dewarp else (out, np.eye(3))
    c = np.asarray(quad.corners, dtype=np.float64)
    a = float(quad.aspect_ratio) if quad.aspect_ratio and quad.aspect_ratio > 0 else _side_ratio(c)
    top, bottom = np.linalg.norm(c[1] - c[0]), np.linalg.norm(c[2] - c[3])
    left, right = np.linalg.norm(c[3] - c[0]), np.linalg.norm(c[2] - c[1])
    if max(top, bottom) >= max(left, right):
        Wf = max(top, bottom)
        Hf = Wf * a
    else:
        Hf = max(left, right)
        Wf = Hf / a
    s = min(1.0, p["max_output_side"] / max(Wf, Hf))
    Wo, Ho = max(1, int(round(Wf * s))), max(1, int(round(Hf * s)))
    dst = np.array([[0, 0], [Wo, 0], [Wo, Ho], [0, Ho]], dtype=np.float64)
    Hm = homography_from_points(c, dst)
    interp = cv2.INTER_CUBIC if p["rectify_interp"] == "cubic" else cv2.INTER_LINEAR
    out = cv2.warpPerspective(image, _shift_h(Hm), (Wo, Ho), flags=interp, borderMode=cv2.BORDER_REPLICATE)
    return (out, Hm, dres) if return_dewarp else (out, Hm)


def enhance(image: np.ndarray, params: dict | None = None, *, return_info: bool = False):
    """去陰影與光照不均、白平衡，供偵測與辨識用（量顏色時請用原圖）。回傳 RGB uint8；
    return_info=True 時另回傳 dict：glare_mask（反光，原圖大小 bool）、glare_frac、paper_frac、notes。

    做法（縮圖上估照度圖，長邊 max(enhance_work_side, enhance_fine_side) 與原圖取小）：
      1. 粗估背景：形態學閉運算（淺底）或開運算（深底，在更小的縮圖上做）去掉文字，再中值濾波；
         不做高斯平滑，硬陰影的邊才不會被抹開。
      2. 紙白像素：Sauvola 二值化遮掉墨跡（再外擴一點），排除與粗估背景亮度或顏色差太多的像素（色塊、圖片）與反光。
      3. 只用紙白像素估照度：多尺度正規化卷積把墨跡處補上周圍的紙色，再以引導濾波（以自己為引導，保邊平滑）
         去掉紙紋與雜訊；陰影的硬邊保留在照度圖裡，相除之後不會留下亮暗帶。
      4. 原圖除以照度圖；白平衡：淺底時把背景參考色拉成中性灰白（增益有上下限），彩色紙不做。
      反光：紙上成片、幾乎沒有彩度、比周圍紙色亮的過曝區只偵測並標記（不修補，也不拿來估照度）。"""
    from .dewarp import guided_filter, sauvola

    p = _p(params)
    img = image[..., :3].astype(np.float32)
    H, W = img.shape[:2]
    fine = max(int(p["enhance_work_side"]), int(p.get("enhance_fine_side", 1600)))
    s = min(1.0, fine / max(H, W))
    small = cv2.resize(img, (max(4, int(round(W * s))), max(4, int(round(H * s)))), interpolation=cv2.INTER_AREA) if s < 1 else img.copy()
    hs, ws = small.shape[:2]
    luma = small @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    dark = float(np.median(luma)) < p["dark_bg_luma"]
    # 1. 粗估背景（在 enhance_work_side 的縮圖上做形態學，比較快）
    s2 = min(1.0, p["enhance_work_side"] / max(hs, ws))
    coarse_in = cv2.resize(small, (max(4, int(round(ws * s2))), max(4, int(round(hs * s2)))), interpolation=cv2.INTER_AREA) if s2 < 1 else small
    k = max(3, int(round(p["bg_kernel_frac"] * max(coarse_in.shape[:2]))) | 1)
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    bg0 = cv2.morphologyEx(coarse_in, cv2.MORPH_OPEN if dark else cv2.MORPH_CLOSE, ker)
    bg0 = cv2.medianBlur(np.clip(bg0, 0, 255).astype(np.uint8), min(k, 31) | 1).astype(np.float32)
    if s2 < 1:
        bg0 = cv2.resize(bg0, (ws, hs), interpolation=cv2.INTER_LINEAR)
    l0 = np.maximum(bg0 @ np.array([0.299, 0.587, 0.114], dtype=np.float32), 1.0)
    # 2. 紙白像素
    win = max(15, int(round(0.025 * max(hs, ws))) | 1)
    ratio = luma / l0
    ink = sauvola(255.0 - luma if dark else luma, win, 0.2)
    ink &= (ratio > 1.15) if dark else (ratio < 0.85)  # 陰影的邊在 Sauvola 看來也是「比周圍暗」；粗估背景跟著陰影走，比值近 1 就不是墨跡
    ink = cv2.dilate(ink.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    lab_s = cv2.cvtColor(np.clip(small, 0, 255).astype(np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32)
    lab_b = cv2.cvtColor(np.clip(bg0, 0, 255).astype(np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32)
    chroma_d = np.hypot(lab_s[..., 1] - lab_b[..., 1], lab_s[..., 2] - lab_b[..., 2])
    other = (np.abs(ratio - 1.0) > 0.25) | (chroma_d > 14.0)
    # 反光：過亮、無彩度、成片，而且比外圍的紙亮
    glare_s = np.zeros((hs, ws), bool)
    if not dark:
        sat = (luma >= 248.0) & (np.hypot(lab_s[..., 1] - 128, lab_s[..., 2] - 128) < 8.0)
        sat = cv2.morphologyEx(sat.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, lbl, st, _ = cv2.connectedComponentsWithStats(sat, connectivity=8)
        amin = (max(hs, ws) / 120.0) ** 2
        for i in range(1, n):
            x, y, w_, h_, area = st[i]
            if area < amin:
                continue
            m = (lbl[max(0, y - 1) : y + h_ + 1, max(0, x - 1) : x + w_ + 1] == i).astype(np.uint8)
            pad = max(4, int(0.5 * max(w_, h_)), int(0.04 * max(hs, ws)))
            y0, y1, x0, x1 = max(0, y - pad), min(hs, y + h_ + pad), max(0, x - pad), min(ws, x + w_ + pad)
            region = np.zeros((y1 - y0, x1 - x0), np.uint8)
            region[max(0, y - 1) - y0 : max(0, y - 1) - y0 + m.shape[0], max(0, x - 1) - x0 : max(0, x - 1) - x0 + m.shape[1]] = m
            k1 = max(9, int(0.012 * max(hs, ws)) | 1)
            ring = (cv2.dilate(region, np.ones((3 * k1, 3 * k1), np.uint8)) > 0) & (cv2.dilate(region, np.ones((k1, k1), np.uint8)) == 0)
            if area >= 0.6 * hs * ws or ring.sum() <= 20:
                continue
            ring_p75 = float(np.percentile(luma[y0:y1, x0:x1][ring], 75))  # 外圍的紙（75 百分位，避開字）
            blob = lbl == i
            # 局部過曝：成片飽和（≥ 253）、紋理消失（字也被洗掉），外圍的紙沒有飽和。白紙本身就很亮（約 245）時，
            # 「比外圍亮很多」看不出來，要靠飽和與紋理消失判斷
            saturated = float((luma[blob] >= 253.0).mean()) >= 0.6 and float(np.std(luma[blob])) < 3.0 and ring_p75 < 250.0
            if (ring_p75 < 238.0 and area < 0.25 * hs * ws) or saturated:  # 外圍的紙明顯比較暗，或局部過曝（大片的只認過曝）
                glare_s |= blob
        glare_s = cv2.dilate(glare_s.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    paper = ~ink & ~other & ~glare_s
    # 3. 只用紙白像素估照度：多尺度正規化卷積補洞，再保邊平滑
    m = paper.astype(np.float32)
    fill = bg0.copy()
    done = np.zeros((hs, ws), bool)
    def blur(a, sg):  # 大 σ 的高斯在縮小的影像上做再放大（快很多，結果是平滑的照度，不需要精確）
        if sg <= 4.0:
            return cv2.GaussianBlur(a, (0, 0), sg)
        d = sg / 3.0
        sm_ = cv2.resize(a, (max(2, int(round(ws / d))), max(2, int(round(hs / d)))), interpolation=cv2.INTER_AREA)
        return cv2.resize(cv2.GaussianBlur(sm_, (0, 0), 3.0), (ws, hs), interpolation=cv2.INTER_LINEAR)

    for sig in (2.0, 6.0, 18.0, 54.0):
        sg = sig * max(hs, ws) / 1000.0
        den = blur(m, sg)
        num = blur(small * m[..., None], sg)
        ok = (den > 0.25) & ~done
        fill[ok] = num[ok] / den[ok][:, None]
        done |= ok
    fill[paper] = small[paper]
    guide = (fill @ np.array([0.299, 0.587, 0.114], dtype=np.float32)) / 255.0
    r = max(2, int(round(0.012 * max(hs, ws))))
    bg = guided_filter(guide, fill / 255.0, r, 0.03 ** 2) * 255.0
    bg = np.maximum(bg, 1.0)
    bl = bg @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    # 4. 白平衡與相除
    if dark:
        ref = np.full(3, float(np.median(bl)), dtype=np.float32)
    else:
        ref_rgb = np.percentile(bg[paper] if paper.sum() > 100 else bg.reshape(-1, 3), 90, axis=0)
        lab = cv2.cvtColor(np.clip(ref_rgb, 0, 255).astype(np.uint8)[None, None], cv2.COLOR_RGB2LAB)[0, 0].astype(np.float32)
        chroma = float(math.hypot(lab[1] - 128, lab[2] - 128))
        if chroma <= p["wb_max_chroma"]:
            ref = np.full(3, p["white_target"], dtype=np.float32)
        else:
            ref = ref_rgb.astype(np.float32) * (p["white_target"] / max(float(ref_rgb.max()), 1.0))
    gain = np.clip(ref[None, None, :] / bg, p["min_gain"], p["max_gain"])
    gain_full = cv2.resize(gain, (W, H), interpolation=cv2.INTER_LINEAR) if s < 1 else gain
    out = np.clip(img * gain_full, 0, 255).astype(np.uint8)
    if not return_info:
        return out
    glare = cv2.resize(glare_s.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST) > 0 if s < 1 else glare_s
    gf = float(glare.mean())
    notes = [f"頁面上有反光（過曝、看不到內容的區域約佔 {gf:.1%}），那裡的字可能讀不到；建議換個角度重拍"] if gf > 0.0005 else []
    return out, {"glare_mask": glare, "glare_frac": gf, "paper_frac": float(paper.mean()), "notes": notes}

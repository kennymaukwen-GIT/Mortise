"""SVG 向量圖輸出器（OutputFormat.SVG_ASSETS）：只輸出文件裡的向量圖，每個圖一個獨立的 SVG 檔，打包成 zip。

給「我只要把這個標誌、這個圖示拿回來」的情境：原生 PDF 裡本來就是向量的圖形（精確擷取），
以及已經被壓成像素、由描圖重建的圖形，都各自存成一個可以直接丟進設計軟體的 SVG。

zip 的內容：
  page-001-元素編號.svg   每個向量圖一個檔。viewBox 是圖形的外框（左上角為原點，單位 pt），寫法與整頁 SVG 相同
                          （見 exporters/svg.py：基本圖形寫成基本元素、漸層放 defs、只用展示屬性、沒有指令碼與外部參照）。
  manifest.json           每個圖的來源（精確擷取或描圖重建）、信心值、誤差、路徑數、所在頁與位置。
  README.txt              只有在整份文件沒有向量圖時才有，說明為什麼 zip 裡沒有圖。

哪些向量圖會輸出：一律「由圖片找向量圖」——頁面上帶 vector_id 的圖片，才輸出它對應的向量圖。
母片上的圖形每一頁都有一份，這裡只輸出一次（母片取用的那一頁），其餘頁碼記在 manifest 的 also_on_pages。

這個格式不含文字、點陣圖、形狀與表格；要整頁的內容請用整頁 SVG（OutputFormat.SVG）。
"""

from __future__ import annotations

import io
import json
import math
import re
import zipfile
from typing import Any

from ..artifacts import Degradation, OutputFile, OutputFormat, StyledDoc, StyledImage, StyledPage, StyledVector
from ..artifacts.styled_doc import vector_index
from ..config import Config
from ..pipeline.store import Store
from . import svg

MANIFEST = "manifest.json"
README = "README.txt"
ORIGIN_LABEL = {"pdf_paths": "精確擷取", "traced": "描圖重建"}
_DRAWABLE = {"path", "rect", "circle", "ellipse", "line", "polygon", "polyline"}


def collect(doc: StyledDoc) -> tuple[list[dict[str, Any]], list[str]]:
    """找出要輸出的向量圖。回傳（項目清單, 說明）。每個項目有 page（StyledPage）、image、vector、master、also_on_pages。"""
    notes: list[str] = []
    index = vector_index(doc)
    member_of = {eid: m.id for m in doc.masters for eid in m.member_ids}
    master_images = {im.id for m in doc.masters for im in m.images}
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    referenced: set[str] = set()
    for page in doc.pages:
        for im in sorted(page.images, key=lambda i: i.z):
            if not im.vector_id:
                continue
            referenced.add(im.vector_id)
            vec = index.get(im.vector_id)
            if vec is None:
                notes.append(f"第 {page.page_index + 1} 頁的圖片 {im.id} 指向的向量圖 {im.vector_id} 不存在，沒有輸出。")
                continue
            if not vec.paths:
                notes.append(f"向量圖 {vec.id} 沒有任何路徑，沒有輸出。")
                continue
            if vec.id in seen:
                continue
            seen.add(vec.id)
            entries.append({"page": page, "image": im, "vector": vec, "master": member_of.get(im.id), "also_on_pages": []})
    # 母片上的圖形：每頁各有一份，只留母片取用的那一份，其餘頁碼記在它的 also_on_pages
    keep: list[dict[str, Any]] = []
    for e in entries:
        im: StyledImage = e["image"]
        if e["master"] is None or im.id in master_images:
            keep.append(e)
    for e in entries:
        im = e["image"]
        if e["master"] is None or im.id in master_images:
            continue
        same = [k for k in keep if k["master"] == e["master"] and k["image"].id in master_images]
        if not same:
            keep.append(e)  # 找不到母片取用的那一份：照常輸出
            continue
        cx, cy = im.box.x + im.box.w / 2, im.box.y + im.box.h / 2
        best = min(same, key=lambda k: math.hypot(k["image"].box.x + k["image"].box.w / 2 - cx, k["image"].box.y + k["image"].box.h / 2 - cy))
        best["also_on_pages"].append(e["page"].page_index + 1)
    keep.sort(key=lambda e: (e["page"].page_index, e["image"].z))
    orphans = [v.id for page in doc.pages for v in page.vectors if v.id not in referenced]
    if orphans:
        notes.append(f"有 {len(orphans)} 個向量圖沒有任何圖片指向它（{'、'.join(orphans[:5])}{'…' if len(orphans) > 5 else ''}），依「由圖片找向量圖」的規則沒有輸出。")
    return keep, notes


def _file_name(page: StyledPage, vec: StyledVector, used: set[str]) -> str:
    base = f"page-{page.page_index + 1:03d}-" + (re.sub(r"[^A-Za-z0-9\-]+", "-", vec.id).strip("-") or "vector")
    name, k = base, 1
    while name + ".svg" in used:
        k += 1
        name = f"{base}-{k}"
    used.add(name + ".svg")
    return name + ".svg"


def _count(data: bytes) -> tuple[int, int, int]:
    """重新解析一個向量圖 SVG，回傳（畫出的圖形數, 其中基本元素的數量, 漸層數）。"""
    root = svg.parse(data)
    drawn = prims = grads = 0
    for el in root.iter():
        name = svg._local(el.tag)
        if name in _DRAWABLE:
            drawn += 1
            prims += name != "path"
        elif name in ("linearGradient", "radialGradient"):
            grads += 1
    return drawn, prims, grads


def export(doc: StyledDoc, basename: str, cfg: Config, store: Store) -> OutputFile:
    entries, notes = collect(doc)
    deg: list[Degradation] = []
    files: dict[str, bytes] = {}
    items: list[dict[str, Any]] = []
    used: set[str] = set()
    ok = True
    for e in entries:
        page, im, vec = e["page"], e["image"], e["vector"]
        name = _file_name(page, vec, used)
        data = svg.vector_svg(vec, cfg, degradations=deg, title=f"{basename} {vec.id}").encode("utf-8")
        files[name] = data
        problems, root = svg.check(data)
        drawn = prims = grads = 0
        if root is not None:
            drawn, prims, grads = _count(data)
        for msg in problems:
            ok = False
            notes.append(f"自檢（{name}）：{msg}")
        if drawn == 0:
            ok = False
            notes.append(f"自檢（{name}）：沒有畫出任何圖形。")
        items.append({
            "file": name,
            "element_id": vec.id,
            "image_id": im.id,
            "page": page.page_index + 1,
            "also_on_pages": e["also_on_pages"],
            "master": e["master"],
            "origin": vec.origin,
            "origin_label": ORIGIN_LABEL.get(vec.origin, vec.origin),
            "confidence": round(float(vec.confidence), 4),
            "render_error": None if vec.render_error is None else round(float(vec.render_error), 4),
            "kind": vec.kind,
            "paths": drawn,
            "paths_in_document": len(vec.paths),
            "primitives": prims,
            "gradients": grads,
            "box_pt": {"x": round(im.box.x, 3), "y": round(im.box.y, 3), "w": round(im.box.w, 3), "h": round(im.box.h, 3)},
            "page_size_pt": {"w": round(page.width_pt, 3), "h": round(page.height_pt, 3)},
            "z": im.z,
            "bytes": len(data),
        })
        if drawn != len(vec.paths):
            notes.append(f"{name}：成品文件有 {len(vec.paths)} 條路徑，寫出 {drawn} 條（沒有填色也沒有筆畫的路徑看不見，不寫）。")
    manifest = {
        "schema": "mortise.svg_assets/1",
        "document": basename,
        "unit": "pt",
        "count": len(items),
        "fields": {
            "origin": "pdf_paths：原生 PDF 裡本來就是向量，直接讀出來（精確擷取）；traced：已經被壓成像素，由描圖重建",
            "confidence": "這個向量圖的信心值（0 到 1）",
            "render_error": "描圖重建才有：把向量重新畫出來與原始像素比較的平均色差（ΔE）；精確擷取的是 null",
            "paths": "SVG 裡畫出的圖形數（路徑與基本元素）",
            "box_pt": "圖形在頁面上的位置（pt，原點在頁面左上角）",
            "also_on_pages": "同一個圖形（母片）還出現在哪些頁",
        },
        "items": items,
    }
    files[MANIFEST] = (json.dumps(manifest, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    if not items:
        files[README] = (
            "這份文件沒有可以輸出的向量圖。\n\n"
            "「SVG 向量圖」只輸出成品文件裡的向量圖：原生 PDF 裡本來就是向量的圖形，或是由描圖重建的圖形。\n"
            "這份文件的辨識結果裡沒有這樣的圖形，所以壓縮檔裡只有這份說明與空的 manifest.json。\n"
            "要整頁的內容（文字、圖片、形狀、表格）請改選「SVG 整頁」。\n"
        ).encode("utf-8")
        notes.append("整份文件沒有向量圖：zip 裡只有 README.txt（說明）與空的 manifest.json。")
    blob_data = svg.zip_bytes(files)
    # 自檢：重新打開 zip，確認 manifest 列的檔案都在、數量相符
    try:
        with zipfile.ZipFile(io.BytesIO(blob_data)) as z:
            names = set(z.namelist())
            listed = json.loads(z.read(MANIFEST).decode("utf-8"))
        missing = [it["file"] for it in listed["items"] if it["file"] not in names]
        extra = sorted(n for n in names if n.endswith(".svg") and n not in {it["file"] for it in listed["items"]})
        if missing or extra or listed["count"] != len(listed["items"]):
            ok = False
            notes.append(f"自檢：manifest.json 與 zip 的內容不一致（缺 {missing}，多 {extra}）。")
    except Exception as e:  # pragma: no cover
        ok = False
        notes.append(f"自檢：zip 無法重新打開（{type(e).__name__}: {e}）。")
    others = sum(len(p.text_boxes) + len(p.shapes) + len(p.tables) + sum(1 for i in p.images if not i.vector_id) for p in doc.pages)
    if others:
        deg.append(Degradation(element_id="doc", concept="向量圖以外的內容", action=f"這個格式只輸出向量圖；文字、點陣圖、形狀、表格共 {others} 個元素不在裡面（請用整頁 SVG 或其他格式）", lossy=True))
    if items:
        notes.append("各設計軟體開啟這些 SVG 後的行為未經實測；渲染只用 resvg 與 Chromium 驗證過。")
    blob = store.put_blob(blob_data, "application/zip", "zip")
    return OutputFile(format=OutputFormat.SVG_ASSETS, file=blob, filename=f"{basename}_vectors.zip", degradations=deg, fonts_used=[], selfcheck_ok=ok, selfcheck_notes=notes)

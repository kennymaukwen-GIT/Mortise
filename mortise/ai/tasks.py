"""各階段的 AI 使用點：每個任務定義「給 AI 看什麼、問什麼、AI 只能回什麼、怎麼檢查回答」。

目前有兩個：
  structure.roles_order  ④結構：段落的角色標籤與整頁的閱讀順序（編號標記圖）
  ocr.choose_candidates  ③辨識：辨識信心低的字，在候選字中挑一個（AI 不得改寫原文）
"""

from __future__ import annotations

from typing import Optional

from .dispatcher import AiTask

ROLES = ["title", "subtitle", "body", "caption", "header", "footer", "page_number", "footnote", "list_item", "table", "figure", "other"]

_SYSTEM = (
    "你在協助一個文件還原系統理解頁面結構。圖上的元素已經被程式框起來並標上編號。"
    "你只能用題目中出現的編號回答，不能新增或改寫任何文字、數字或座標。看不出來時選最保守的答案。"
)


# ---------------------------------------------------------------- ④ 角色與閱讀順序


def roles_order_task(image_png: bytes, items: list[dict], page_index: int) -> AiTask:
    """items：每項 {n: 編號, kind: "paragraph"|"image"|"table"|"shape", text: 前 80 字（段落才有）}。
    回答：每個段落編號的角色，以及所有編號的閱讀順序。"""
    para_ns = [it["n"] for it in items if it["kind"] == "paragraph"]
    all_ns = [it["n"] for it in items]
    lines = []
    for it in items:
        desc = f"{it['n']}. [{it['kind']}]"
        if it.get("text"):
            desc += f" {it['text']}"
        lines.append(desc)
    text = (
        "頁面上框起來的元素如下（編號、種類、段落的開頭文字）：\n" + "\n".join(lines) + "\n\n"
        "請回答兩件事：\n"
        f"1. roles：每個段落（編號 {', '.join(map(str, para_ns))}）的角色，只能是：{', '.join(ROLES)}。\n"
        "2. reading_order：人閱讀這一頁的順序，列出所有編號，每個編號恰好出現一次。"
        "多欄版面要讀完一欄再讀下一欄；頁首、頁尾、頁碼放最後。"
    )
    schema = {
        "type": "object",
        "properties": {
            "roles": {"type": "array", "items": {"type": "object", "properties": {"n": {"type": "integer"}, "role": {"type": "string", "enum": ROLES}}, "required": ["n", "role"], "additionalProperties": False}},
            "reading_order": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["roles", "reading_order"],
        "additionalProperties": False,
    }

    def validate(ans: dict) -> Optional[str]:
        roles = ans.get("roles", [])
        got = [r.get("n") for r in roles]
        if sorted(got) != sorted(para_ns):
            missing = sorted(set(para_ns) - set(got))
            extra = sorted(set(got) - set(para_ns))
            return f"roles 的編號不對（缺少 {missing}，多出 {extra}，或有重複）"
        if any(r.get("role") not in ROLES for r in roles):
            return "roles 中有不允許的角色"
        order = ans.get("reading_order", [])
        if sorted(order) != sorted(all_ns):
            return "reading_order 必須恰好包含每個編號一次"
        return None

    return AiTask(name="structure.roles_order", prompt_version="1", system=_SYSTEM, text=text, schema=schema, validate=validate, image_png=image_png, page_index=page_index, max_tokens=4000)


# ---------------------------------------------------------------- ③ 候選字仲裁


def choose_candidates_task(stacked_png: bytes, lines: list[dict], page_index: int) -> AiTask:
    """lines：每項 {label: "L1", text: 辨識結果, slots: [{k: 全頁唯一的位置編號, index: 在 text 中的位置, candidates: [字, ...]}]}。
    圖是這幾行的影像由上而下疊在一起，每行左邊標著 L1、L2…。
    回答：每個位置選第幾個候選（從 0 起）。AI 只能在候選中選，不能自己寫字。"""
    blocks = []
    sizes: dict[int, int] = {}
    for ln in lines:
        marked = list(ln["text"])
        for sl in sorted(ln["slots"], key=lambda x: -x["index"]):
            marked[sl["index"]] = f"[{sl['k']}]"
            sizes[sl["k"]] = len(sl["candidates"])
        cand = "\n".join(f"  [{sl['k']}]：" + "、".join(f"{i}={c!r}" for i, c in enumerate(sl["candidates"])) for sl in ln["slots"])
        blocks.append(f"{ln['label']}：{''.join(marked)}\n{cand}")
    t = (
        "圖是幾行文字的影像，由上而下疊在一起，每行左邊標著 L1、L2…。文字辨識的結果如下，不確定的字用 [編號] 標出，"
        "下面列出每個位置的候選字：\n\n" + "\n\n".join(blocks) + "\n\n"
        "請依影像與上下文，為每個 [編號] 選一個候選的序號。只能選列出的候選，不能寫其他字。"
    )
    ks = sorted(sizes)
    schema = {
        "type": "object",
        "properties": {"choices": {"type": "array", "items": {"type": "object", "properties": {"k": {"type": "integer"}, "choice": {"type": "integer"}}, "required": ["k", "choice"], "additionalProperties": False}}},
        "required": ["choices"],
        "additionalProperties": False,
    }

    def validate(ans: dict) -> Optional[str]:
        ch = ans.get("choices", [])
        if sorted(c.get("k") for c in ch) != ks:
            return "每個 [編號] 都要恰好回答一次"
        for c in ch:
            if not (0 <= int(c.get("choice", -1)) < sizes[c["k"]]):
                return f"位置 [{c['k']}] 的序號超出候選範圍"
        return None

    return AiTask(name="ocr.choose_candidates", prompt_version="1", system=_SYSTEM, text=t, schema=schema, validate=validate, image_png=stacked_png, page_index=page_index, max_tokens=2000)

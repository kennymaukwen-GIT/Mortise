"""AI 調度器與 AI 使用點：用假的供應商測試（不呼叫真的模型、不花錢）。"""

import re
from pathlib import Path

import pytest
from PIL import Image

from mortise.ai import AiDispatcher
from mortise.ai.marks import render_marks, stack_crops
from mortise.ai.providers import FakeProvider
from mortise.ai.tasks import choose_candidates_task, roles_order_task
from mortise.artifacts import Intent, Layout, Quad
from mortise.artifacts.layout import PageLayout
from mortise.config import Config
from mortise.pipeline import Pipeline

FIX = Path(__file__).parent / "fixtures" / "slide_basic.pdf"
CFG = Config.load().section("ai")


def _items(text):
    return [(int(n), kind, rest) for n, kind, rest in re.findall(r"^(\d+)\. \[(\w+)\] ?(.*)$", text, flags=re.M)]


def good_answer(system, text, image, schema):
    items = _items(text)
    roles = [{"n": n, "role": "title" if rest.startswith("Mortise") else "body"} for n, kind, rest in items if kind == "paragraph"]
    return {"roles": roles, "reading_order": [n for n, _, _ in reversed(items)]}


def test_roles_order_validation():
    t = roles_order_task(b"png", [{"n": 1, "kind": "paragraph", "text": "a"}, {"n": 2, "kind": "image"}], 0)
    assert t.validate({"roles": [{"n": 1, "role": "body"}], "reading_order": [2, 1]}) is None
    assert t.validate({"roles": [{"n": 1, "role": "body"}], "reading_order": [1]}) is not None  # 漏了編號
    assert t.validate({"roles": [{"n": 1, "role": "hero"}], "reading_order": [1, 2]}) is not None  # 不允許的標籤
    assert t.validate({"roles": [{"n": 2, "role": "body"}], "reading_order": [1, 2]}) is not None  # 圖片不能有角色


def test_candidate_choice_cannot_invent_text():
    t = choose_candidates_task(b"png", [{"label": "L1", "text": "營收成長", "slots": [{"k": 1, "index": 2, "candidates": ["成", "戌"]}]}], 0)
    assert t.validate({"choices": [{"k": 1, "choice": 1}]}) is None
    assert t.validate({"choices": [{"k": 1, "choice": 2}]}) is not None  # 超出候選範圍
    assert t.validate({"choices": []}) is not None


def test_dispatcher_cache_budget_and_retry(tmp_path):
    calls = []

    def flaky(system, text, image, schema):
        calls.append(text)
        return {"roles": [], "reading_order": []} if len(calls) == 1 else good_answer(system, text, image, schema)

    d = AiDispatcher({**CFG, "max_calls_per_page": 2}, cache_dir=tmp_path, provider=FakeProvider(flaky))
    task = roles_order_task(b"img", [{"n": 1, "kind": "paragraph", "text": "Mortise"}, {"n": 2, "kind": "paragraph", "text": "x"}], 0)
    ans = d.ask(task)
    assert ans["roles"][0]["role"] == "title" and len(calls) == 2  # 第一次不合法，重問後合法
    assert "上一次的回答不合法" in calls[1]
    assert d.ask(task) == ans and len(calls) == 2  # 第二次直接用快取
    assert (tmp_path / "calls.jsonl").read_text(encoding="utf-8").count("\n") == 3
    d2 = AiDispatcher({**CFG, "max_cost_per_document_usd": 0.0}, cache_dir=None, provider=FakeProvider(good_answer))
    assert d2.ask(task) is None  # 超出花費上限：不呼叫


def test_marks_and_stack_render():
    im = Image.new("RGB", (1000, 600), "white")
    png, mapping = render_marks(im, [("a", Quad.from_rect(0.1, 0.1, 0.5, 0.2)), ("b", Quad.from_rect(0.1, 0.3, 0.5, 0.4))])
    assert mapping == {1: "a", 2: "b"} and png[:4] == b"\x89PNG"
    big = Image.new("RGB", (6000, 3000), "white")
    png2, _ = render_marks(big, [("a", Quad.from_rect(0.1, 0.1, 0.5, 0.2))])
    import io

    assert max(Image.open(io.BytesIO(png2)).size) <= 2576  # 縮到模型的影像長邊上限
    assert stack_crops([("L1", Image.new("RGB", (300, 20), "white")), ("L2", Image.new("RGB", (100, 30), "white"))])[:4] == b"\x89PNG"


@pytest.fixture(scope="module")
def ai_job(tmp_path_factory):
    pipe = Pipeline(tmp_path_factory.mktemp("aijob"))
    fake = FakeProvider(good_answer)
    pipe._ai_dispatcher = AiDispatcher(pipe.cfg.section("ai"), cache_dir=pipe.dir / "ai", provider=fake)
    pipe.start(FIX, FIX.name, Intent())
    pipe.run()
    return pipe, fake


def test_structure_uses_ai_answer(ai_job):
    pipe, fake = ai_job
    pl = pipe.store.load("s4/page-001", PageLayout)
    assert fake.calls and fake.calls[0]["has_image"]
    title = [p for p in pl.paragraphs if p.role.value.value == "title"]
    assert len(title) == 1 and title[0].role.source.value == "ai"
    boxes = [b.id for b in pl.text_boxes]
    order = [e for e in pl.reading_order if e in boxes]
    assert order == list(reversed(sorted(boxes, key=lambda b: next(x for x in pl.text_boxes if x.id == b).quad.bbox[1])))
    assert any("大型 AI" in n for n in pl.notes)

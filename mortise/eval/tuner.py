"""自動調參：在一批樣本上用座標下降法（一次調一個參數）找出讓指定總分最高的設定。

    python -m mortise.eval tune 樣本目錄 [--out 輸出目錄] [--tunable mortise/config/tunable.toml]
                                [--objective composite] [--validation 0.25] [--rounds 2]

做法：
1. 讀「可調參數」清單（mortise/config/tunable.toml）：每個參數的設定檔位置（節.鍵）、範圍與步距（或離散候選）。
2. 依樣本路徑的 CRC32 把樣本穩定地分成「調參用」與「驗證用」兩份（validation_fraction）；
   樣本只有一個時不分（驗證分數回報為空，並在報告中提醒）。
3. 目標分數：每個樣本的每種評測取目標指標（預設「綜合分數」；也可以給 {指標: 權重} 自訂加權）後平均，
   再對樣本平均。評測本身用 runner.evaluate_sample（同一套指標），工作目錄沿用快取，
   所以只改 [structure]／[settings] 時只會從④重跑。
4. 座標下降：依清單順序，每次只動一個參數：先往上一步試，有進步（超過 min_improvement）就繼續往同方向走，
   沒進步就改往下試；走不動就換下一個參數。整份清單掃 max_rounds 輪，或某一輪完全沒有進步就停。
   同一組設定只評一次（記憶化）。
5. 輸出：設定檔覆寫檔 override.toml（Config.load(override=...) 可讀，只寫和預設不同的值）、
   tune_report.json（每個參數調整前後的值與分數、所有試過的點、調參與驗證分數）。
   驗證分數變差時 adopt=false：表示可能過度擬合，建議不要採用。

已知限制：
- 座標下降只找局部最佳；參數之間有交互作用時可能卡住（多跑幾輪可以緩解）。
- 每次評測都要跑一次④以後的流水線（含 LibreOffice 渲染），樣本多時很慢；目前不平行。
- 原生 PDF 沒有人工標準答案時，目標分數只有端到端指標（⑦），對結構參數的鑑別力有限。

可單獨升級的點：改用貝氏最佳化或隨機搜尋起點；平行評測；交叉驗證（k-fold）取代單次切分。
"""

from __future__ import annotations

import copy
import json
import math
import tomllib
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from ..config import Config
from . import metrics as M
from .runner import Sample, discover, evaluate_sample

TUNABLE_DEFAULT = Path(__file__).resolve().parent.parent / "config" / "tunable.toml"

DEFAULT_PARAMS: dict[str, Any] = {
    "objective": "composite",  # 目標指標名稱，或 {指標名稱: 權重}（加權方式同 metrics.composite）
    "validation_fraction": 0.25,  # 驗證用樣本的比例（0 到 1；樣本數 ≥ 2 時至少一個）
    "split_salt": "mortise-tune",  # 切分用的鹽（換掉就換一種切法）
    "max_rounds": 2,  # 整份參數清單最多掃幾輪
    "max_steps_per_param": 6,  # 每個參數每個方向最多走幾步
    "min_improvement": 1e-4,  # 分數至少進步這麼多才移動（避免在平台上亂走）
    "adopt_tolerance": 0.002,  # 驗證分數退步不超過此值仍建議採用
    "eval": {"variants": []},  # 傳給 runner.evaluate_sample 的參數（預設不做自我監督以節省時間）
}


def _p(params: dict | None) -> dict[str, Any]:
    p = copy.deepcopy(DEFAULT_PARAMS)
    if params:
        p.update(params)
    return p


@dataclass
class TunableParam:
    path: str  # 節.鍵
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    values: Optional[list[float]] = None
    note: str = ""

    @property
    def section(self) -> str:
        return self.path.split(".", 1)[0]

    @property
    def key(self) -> str:
        return self.path.split(".", 1)[1]

    def neighbor(self, cur: float, direction: int) -> Optional[float]:
        """往上（+1）或往下（-1）一步的值；超出範圍回傳 None。"""
        if self.values:
            vals = sorted(self.values)
            idx = int(np.argmin([abs(v - cur) for v in vals]))
            j = idx + direction
            return vals[j] if 0 <= j < len(vals) else None
        nxt = round(cur + direction * float(self.step), 10)
        if nxt > float(self.max) + 1e-12 or nxt < float(self.min) - 1e-12:
            return None
        return min(float(self.max), max(float(self.min), nxt))


def load_tunable(path: Optional[Path] = None) -> list[TunableParam]:
    data = tomllib.loads(Path(path or TUNABLE_DEFAULT).read_text(encoding="utf-8"))
    out = []
    for d in data.get("param", []):
        out.append(TunableParam(**{k: d[k] for k in ("path", "min", "max", "step", "values", "note") if k in d}))
    return out


def get_value(cfg: Config, path: str) -> Any:
    sec, key = path.split(".", 1)
    return cfg.section(sec).get(key)


def with_values(cfg: Config, values: dict[str, Any]) -> Config:
    data = copy.deepcopy(cfg.data)
    for path, v in values.items():
        sec, key = path.split(".", 1)
        data.setdefault(sec, {})[key] = v
    return Config(data)


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(float(v)) if isinstance(v, float) else str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)


def write_override(values: dict[str, Any], path: Path, header: str = "") -> Path:
    """寫出設定檔覆寫檔（TOML），Config.load(override=path) 可讀。"""
    secs: dict[str, dict[str, Any]] = {}
    for p, v in values.items():
        sec, key = p.split(".", 1)
        secs.setdefault(sec, {})[key] = v
    lines = [f"# {h}" for h in header.splitlines()] if header else []
    for sec, kv in secs.items():
        lines += ["", f"[{sec}]"] + [f"{k} = {_toml_value(v)}" for k, v in kv.items()]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return Path(path)


def split_samples(samples: list[Sample], fraction: float, salt: str = "mortise-tune") -> tuple[list[Sample], list[Sample]]:
    """依 CRC32 穩定切分（調參用、驗證用）。"""
    if len(samples) < 2 or fraction <= 0:
        return list(samples), []
    order = sorted(samples, key=lambda s: zlib.crc32(f"{salt}|{s.key}".encode("utf-8")))
    n_val = min(len(samples) - 1, max(1, int(round(len(samples) * fraction))))
    val = order[:n_val]
    return [s for s in samples if s not in val], val


# ====================================================================== 座標下降（與評測方式無關的核心）


@dataclass
class ParamResult:
    path: str
    before: Any
    after: Any
    score_before: float
    score_after: float
    tried: list[tuple[Any, float]] = field(default_factory=list)


@dataclass
class TuneResult:
    start: dict[str, Any]
    best: dict[str, Any]
    train_before: Optional[float]
    train_after: Optional[float]
    validation_before: Optional[float] = None
    validation_after: Optional[float] = None
    adopt: Optional[bool] = None
    params: list[ParamResult] = field(default_factory=list)
    rounds: int = 0
    evaluations: int = 0
    train_samples: list[str] = field(default_factory=list)
    validation_samples: list[str] = field(default_factory=list)
    override_path: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def coordinate_descent(tunables: list[TunableParam], start: dict[str, Any], score: Callable[[dict[str, Any]], float],
                       params: dict | None = None) -> TuneResult:
    """座標下降：score(完整的參數值 dict) → 越高越好。回傳最佳值與每個參數的調整紀錄。"""
    p = _p(params)
    memo: dict[str, float] = {}

    def f(vals: dict[str, Any]) -> float:
        k = json.dumps(vals, sort_keys=True)
        if k not in memo:
            s = score(dict(vals))
            memo[k] = float(s) if s is not None and not (isinstance(s, float) and math.isnan(s)) else -math.inf
        return memo[k]

    cur = dict(start)
    best = f(cur)
    first = best
    history: dict[str, ParamResult] = {t.path: ParamResult(t.path, start[t.path], start[t.path], best, best) for t in tunables}
    rounds = 0
    for rounds in range(1, int(p["max_rounds"]) + 1):
        improved_round = False
        for t in tunables:
            rec = history[t.path]
            before_v, before_s = cur[t.path], best
            for direction in (+1, -1):
                moved = False
                for _ in range(int(p["max_steps_per_param"])):
                    cand = t.neighbor(cur[t.path], direction)
                    if cand is None:
                        break
                    trial = {**cur, t.path: cand}
                    s = f(trial)
                    rec.tried.append((cand, s))
                    if s > best + float(p["min_improvement"]):
                        cur, best, moved = trial, s, True
                    else:
                        break
                if moved:
                    break
            if cur[t.path] != before_v:
                improved_round = True
            rec.after, rec.score_after = cur[t.path], best
            if rounds == 1:
                rec.score_before = before_s
        if not improved_round:
            break
    return TuneResult(start=dict(start), best=cur, train_before=first, train_after=best, params=list(history.values()), rounds=rounds, evaluations=len(memo))


# ====================================================================== 在樣本上調參


def objective_of(evaluation_metrics: dict[str, float], objective: Any, metric_params: dict | None = None) -> Optional[float]:
    if isinstance(objective, dict):
        return M.composite(evaluation_metrics, {**(metric_params or {}), "composite_weights": objective})
    return evaluation_metrics.get(str(objective))


def set_score(samples: list[Sample], cfg: Config, out_dir: Path, params: dict | None = None) -> Optional[float]:
    """一組設定在一批樣本上的目標分數（評測平均 → 樣本平均）。"""
    p = _p(params)
    mp = p["eval"].get("metrics", {})
    per_sample = []
    for s in samples:
        r = evaluate_sample(s, out_dir, cfg, p["eval"])
        vals = [objective_of(e["metrics"], p["objective"], mp) for e in r["evaluations"]]
        vals = [v for v in vals if v is not None]
        if vals:
            per_sample.append(float(np.mean(vals)))
    return float(np.mean(per_sample)) if per_sample else None


def tune(sample_dir: Path, out_dir: Path, tunable: Optional[Path | list[TunableParam]] = None, cfg: Optional[Config] = None,
         params: dict | None = None, limit: Optional[int] = None, on_progress: Optional[Callable[[str], None]] = None) -> TuneResult:
    p = _p(params)
    cfg = cfg or Config.load()
    say = on_progress or (lambda s: None)
    tunables = tunable if isinstance(tunable, list) else load_tunable(tunable)
    tunables = [t for t in tunables if get_value(cfg, t.path) is not None]
    out_dir = Path(out_dir)
    samples = discover(Path(sample_dir), limit)
    train, val = split_samples(samples, float(p["validation_fraction"]), p["split_salt"])
    start = {t.path: get_value(cfg, t.path) for t in tunables}
    n = [0]

    def score(vals: dict[str, Any]) -> float:
        n[0] += 1
        s = set_score(train, with_values(cfg, vals), out_dir, p)
        say(f"  評測 #{n[0]}：{s if s is None else round(s, 5)}  {({k: v for k, v in vals.items() if v != start[k]})}")
        return s if s is not None else -math.inf

    res = coordinate_descent(tunables, start, score, p)
    res.train_samples = [s.key for s in train]
    res.validation_samples = [s.key for s in val]
    if val:
        res.validation_before = set_score(val, with_values(cfg, start), out_dir, p)
        res.validation_after = set_score(val, with_values(cfg, res.best), out_dir, p)
        if res.validation_before is not None and res.validation_after is not None:
            res.adopt = res.validation_after >= res.validation_before - float(p["adopt_tolerance"])
            if not res.adopt:
                res.notes.append("驗證分數變差：調出來的設定可能只適合調參用的樣本（過度擬合），建議不要採用。")
    else:
        res.notes.append("樣本太少，沒有切出驗證用樣本：無法檢查過度擬合，請謹慎採用。")
    changed = {k: v for k, v in res.best.items() if v != start[k]}
    header = f"Mortise 自動調參覆寫檔（座標下降）。調參分數 {res.train_before} → {res.train_after}；驗證分數 {res.validation_before} → {res.validation_after}。"
    res.override_path = str(write_override(changed, out_dir / "override.toml", header))
    (out_dir / "tune_report.json").write_text(json.dumps(res.to_dict(), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return res

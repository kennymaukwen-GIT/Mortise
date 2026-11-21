"""幾何：3×3 矩陣（列優先 9 個數），把來源座標轉成頁面相對座標。"""

from __future__ import annotations

from ..artifacts import Point, Quad

Matrix = list[float]  # [m00, m01, m02, m10, m11, m12, m20, m21, m22]


def apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    w = m[6] * x + m[7] * y + m[8]
    return (m[0] * x + m[1] * y + m[2]) / w, (m[3] * x + m[4] * y + m[5]) / w


def to_point(m: Matrix, x: float, y: float) -> Point:
    px, py = apply(m, x, y)
    return Point(x=px, y=py)


def quad_from_source(m: Matrix, tl: tuple[float, float], tr: tuple[float, float], br: tuple[float, float], bl: tuple[float, float]) -> Quad:
    """把來源座標的四個角轉成頁面座標的四角。"""
    return Quad(tl=to_point(m, *tl), tr=to_point(m, *tr), br=to_point(m, *br), bl=to_point(m, *bl))


def affine_mul(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, ...]:
    """PDF 的 6 數矩陣相乘：先套 a、再套 b（列向量慣例）。"""
    return (
        a[0] * b[0] + a[1] * b[2],
        a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2],
        a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4],
        a[4] * b[1] + a[5] * b[3] + b[5],
    )


def affine_apply(m: tuple[float, ...], x: float, y: float) -> tuple[float, float]:
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

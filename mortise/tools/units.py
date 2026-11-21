"""單位換算。"""

PT_PER_INCH = 72.0
MM_PER_INCH = 25.4
EMU_PER_INCH = 914400
EMU_PER_PT = 12700


def to_pt(value: float, unit: str, dpi: float = 96.0) -> float:
    unit = unit.lower()
    if unit == "pt":
        return value
    if unit == "mm":
        return value * PT_PER_INCH / MM_PER_INCH
    if unit == "cm":
        return value * 10 * PT_PER_INCH / MM_PER_INCH
    if unit in ("in", "inch"):
        return value * PT_PER_INCH
    if unit == "px":
        return value * PT_PER_INCH / dpi
    raise ValueError(f"不認得的單位：{unit}")


def pt_to_emu(pt: float) -> int:
    return int(round(pt * EMU_PER_PT))


def pt_to_mm(pt: float) -> float:
    return pt * MM_PER_INCH / PT_PER_INCH

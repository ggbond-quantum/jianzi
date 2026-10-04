from pathlib import Path

from jianzipu.extract.emf import convert_emf_to_svg

RAW_STK = Path(__file__).parent.parent / ".raw" / "stk"


def test_convert_one_emf_produces_valid_svg():
    svg = convert_emf_to_svg(RAW_STK / "散.emf")
    assert svg.startswith("<svg")
    assert 'd="M ' in svg
    assert "C " in svg


def test_convert_emf_extracts_bezier_outline():
    svg = convert_emf_to_svg(RAW_STK / "勾.emf")
    assert "Z" in svg

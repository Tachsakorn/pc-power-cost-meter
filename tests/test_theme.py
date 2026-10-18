import pytest

from power_meter.ui.theme import DARK, LIGHT, Palette, contrast_ratio

TEXT_MIN = 4.5


def test_contrast_known_pair() -> None:
    assert contrast_ratio("#E8B866", "#1A1613") == pytest.approx(9.83, abs=0.01)


def test_contrast_is_symmetric_and_bounded() -> None:
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert contrast_ratio("#FFFFFF", "#000000") == pytest.approx(21.0)
    assert contrast_ratio("#336699", "#336699") == pytest.approx(1.0)


@pytest.mark.parametrize("palette", [DARK, LIGHT], ids=["dark", "light"])
def test_palettes_meet_contrast(palette: Palette) -> None:
    for surface in (palette.background, palette.surface):
        assert contrast_ratio(palette.text, surface) >= TEXT_MIN
        assert contrast_ratio(palette.text_muted, surface) >= TEXT_MIN
        assert contrast_ratio(palette.error, surface) >= TEXT_MIN
    assert contrast_ratio(palette.focus, palette.background) >= 3.0


@pytest.mark.parametrize("palette", [DARK, LIGHT], ids=["dark", "light"])
def test_accent_meets_text_contrast(palette: Palette) -> None:
    assert contrast_ratio(palette.accent, palette.background) >= TEXT_MIN

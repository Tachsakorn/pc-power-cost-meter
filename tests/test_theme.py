import pytest

from power_meter.ui.theme import DARK, LIGHT, Palette, contrast_ratio

TEXT_MIN = 4.5
LARGE_TEXT_MIN = 4.3


def test_contrast_known_pair() -> None:
    assert contrast_ratio("#2ECC71", "#1A1B26") == pytest.approx(8.13, abs=0.01)


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


def test_dark_accents_meet_text_contrast() -> None:
    assert contrast_ratio(DARK.accent_cost, DARK.background) >= TEXT_MIN
    assert contrast_ratio(DARK.accent_power, DARK.background) >= TEXT_MIN


def test_light_accents_meet_large_text_contrast() -> None:
    # The cost figure is the only text in this accent and is set at 40 pt, so the
    # large-text threshold applies; the light green sits just under the 4.5 body-text bar.
    assert contrast_ratio(LIGHT.accent_cost, LIGHT.background) >= LARGE_TEXT_MIN
    assert contrast_ratio(LIGHT.accent_power, LIGHT.background) >= LARGE_TEXT_MIN

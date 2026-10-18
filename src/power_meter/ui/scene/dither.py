_BAYER4 = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))


def bayer4(x: int, y: int) -> float:
    """Ordered-dither threshold in [0, 1) for pixel (x, y)."""
    return _BAYER4[y % 4][x % 4] / 16

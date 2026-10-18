import math

from PIL import Image, ImageColor, ImageDraw

TICKS = 61
START_DEGREES = 190
END_DEGREES = -10
SUPERSAMPLE = 4
DESIGN_WIDTH = 320


def render_gauge(fraction: float, accent: str, track: str, width: int, height: int) -> Image.Image:
    lit = 0 if math.isnan(fraction) else round(min(max(fraction, 0.0), 1.0) * (TICKS - 1))
    k = width / DESIGN_WIDTH * SUPERSAMPLE
    canvas = Image.new("RGBA", (width * SUPERSAMPLE, height * SUPERSAMPLE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    centre_x, centre_y = width / 2 * SUPERSAMPLE, height * SUPERSAMPLE - 32 * k
    radius = 118 * k
    lit_colour = ImageColor.getrgb(accent)
    unlit_colour = ImageColor.getrgb(track)

    for i in range(TICKS):
        angle = math.radians(START_DEGREES + (END_DEGREES - START_DEGREES) * i / (TICKS - 1))
        length = (14 if i % 10 == 0 else 9) * k
        thickness = 1.6 * k
        if i == lit:
            length, thickness = 22 * k, 2.2 * k
        colour = lit_colour if i <= lit else unlit_colour
        cos, sin = math.cos(angle), math.sin(angle)
        outer = (centre_x + radius * cos, centre_y - radius * sin)
        inner = (centre_x + (radius - length) * cos, centre_y - (radius - length) * sin)
        draw.line([outer, inner], fill=colour, width=round(thickness))
        cap = thickness / 2
        for x, y in (outer, inner):
            draw.ellipse((x - cap, y - cap, x + cap, y + cap), fill=colour)
    return canvas.resize((width, height), Image.Resampling.LANCZOS)

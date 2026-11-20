#!/usr/bin/env python3
"""Draw the corpus_pepys logo in Art Techno and write every size the repo uses.

The mark is the fire: the Great Fire of 1666, the diary's best-known pages,
drawn as Art Techno's boot sun. Flames rise as rays from a die on the horizon
behind the City, with Old St Paul's dome and towers in silhouette. The
palette, drawing helpers, towers and outlined lettering come from the Art
Techno brand build, so the SVGs render the same everywhere.

Usage::

    python scripts/build_logo.py

Needs ``fonttools``, Google Chrome for the PNG renders, and a clone of
https://github.com/Flux-Frontiers/art_techno beside this repo. Set ``CHROME``
to the browser binary if it is not at the default macOS path.

Writes:

- ``assets/corpuspepys_logo.svg`` and ``assets/corpuspepys_logo.png``: the
  square mark (1024 px PNG)
- ``assets/corpuspepys_icon.svg``: the mark reduced for 64 px and below
- ``assets/corpuspepys_banner.svg`` and ``assets/corpuspepys_banner.png``
"""

import importlib.util
import math
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AT_BUILD = ROOT.parent / "art_techno" / "docs" / "brand" / "build.py"

_spec = importlib.util.spec_from_file_location("at_build", AT_BUILD)
at = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(at)

f, line, pad, via, diamond, clipped_rect = (
    at.f,
    at.line,
    at.pad,
    at.via,
    at.diamond,
    at.clipped_rect,
)
OBSIDIAN, PANEL, COPPER, GOLD, GREEN, MINT, SILK = (
    at.OBSIDIAN,
    at.PANEL,
    at.COPPER,
    at.GOLD,
    at.GREEN,
    at.MINT,
    at.SILK,
)

NAME = "CORPUS PEPYS"
PART = "THE DIARY . 1660 . 1669"


def rect(
    x: float, y: float, w: float, h: float, fill: str, stroke: str = "", width: float = 0
) -> str:
    s = f' stroke="{stroke}" stroke-width="{f(width)}"' if stroke else ""
    return f'<rect x="{f(x)}" y="{f(y)}" width="{f(w)}" height="{f(h)}" fill="{fill}"{s}/>'


def die(x: float, y: float, s: float, core: str) -> str:
    """A square green die in a gold line, centered on ``x, y``.

    :param x: Center x.
    :param y: Center y.
    :param s: Side length.
    :param core: Color of the square at its center.
    """
    return rect(x - s / 2, y - s / 2, s, s, GREEN, GOLD, s / 9) + rect(
        x - s / 6, y - s / 6, s / 3, s / 3, core
    )


def fire() -> str:
    """The Fire rising from a die behind the City's skyline, on a 512 field."""
    out = []
    cx, hz = 256, 300
    # Flames: rays from the die, long copper and short gold, each ending in a pad.
    for k in range(-6, 7):
        a = math.radians(90 + 13 * k)
        long = k % 2 == 0
        r1 = 210 if long else 160
        start = (cx + 40 * math.cos(a), hz - 40 * math.sin(a))
        end = (cx + r1 * math.cos(a), hz - r1 * math.sin(a))
        out.append(line([start, end], COPPER if long else GOLD, 5 if long else 3.5))
        out.append(pad(*end, 7 if long else 5, COPPER if long else GOLD))
    # The City: Old St Paul's dome and spire between stepped towers.
    out.append(at.tower(146, hz, 40, 96, GOLD) + at.tower(362, hz, 40, 110, GOLD))
    out.append(at.tower(110, hz, 32, 64, COPPER) + at.tower(404, hz, 32, 72, COPPER))
    out.append(
        f'<path d="M174 {hz}V{hz - 62}A30 30 0 0 1 234 {hz - 62}V{hz}Z" fill="{PANEL}" '
        f'stroke="{GOLD}" stroke-width="3"/>'
    )
    out.append(line([(204, hz - 92), (204, hz - 116)], GOLD, 3) + pad(204, hz - 120, 4, GOLD))
    out.append(line([(84, hz), (428, hz)], GOLD, 4) + via(78, hz, 7, GOLD) + via(434, hz, 7, GOLD))
    out.append(die(cx, hz, 40, SILK))
    return "".join(out)


def fire_small() -> str:
    """The fire reduced for 64 px and below: five flames, two towers, the die."""
    out = []
    for k in range(-2, 3):
        a = math.radians(90 + 30 * k)
        start = (32 + 8 * math.cos(a), 44 - 8 * math.sin(a))
        end = (32 + 24 * math.cos(a), 44 - 24 * math.sin(a))
        out.append(line([start, end], COPPER, 2.6) + pad(*end, 2.8, COPPER))
    out.append(line([(8, 46), (56, 46)], GOLD, 3))
    for x, h in ((14, 12), (50, 14)):
        out.append(rect(x - 4, 46 - h, 8, h, PANEL, GOLD, 1.8))
    out.append(die(32, 46, 12, GOLD))
    return "".join(out)


def frame(w: float, h: float, inset: float, c: float, heavy: float) -> str:
    """Double frame with 45-degree corners and a green diamond at each corner.

    :param w: Canvas width.
    :param h: Canvas height.
    :param inset: Distance of the heavy line from the canvas edge.
    :param c: Corner cut of the heavy line.
    :param heavy: Stroke width of the heavy line.
    """
    out = [
        f'<path d="{clipped_rect(inset, inset, w - 2 * inset, h - 2 * inset, c)}" fill="none" stroke="{GOLD}" stroke-width="{f(heavy)}"/>',
        f'<path d="{clipped_rect(inset * 1.9, inset * 1.9, w - 3.8 * inset, h - 3.8 * inset, c * 0.75)}" fill="none" stroke="{GOLD}" stroke-width="{f(heavy * 0.4)}" stroke-opacity=".6"/>',
    ]
    d = inset * 1.45 + c * 0.4375
    for cx, cy in ((d, d), (w - d, d), (d, h - d), (w - d, h - d)):
        out.append(diamond(cx, cy, inset * 0.5, GREEN))
    return "".join(out)


def svg(w: int, h: int, title: str, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f"<title>{title}</title>"
        f'<rect width="{w}" height="{h}" fill="{OBSIDIAN}"/>{body}</svg>\n'
    )


def mark(display, mono) -> str:
    """512 square mark: the fire, the name and the part line beneath."""
    body = frame(512, 512, 10, 26, 5) + fire()
    body += display.path(NAME, 256, 430, 40, 0.2, "middle", GOLD)
    body += mono.path(PART, 256, 462, 13.5, 0.3, "middle", MINT)
    return svg(512, 512, "corpus_pepys mark, Art Techno", body)


def icon() -> str:
    """64 icon: the reduced fire in one heavy frame, no lettering."""
    body = f'<path d="{clipped_rect(2.5, 2.5, 59, 59, 7.5)}" fill="none" stroke="{GOLD}" stroke-width="3"/>'
    return svg(64, 64, "corpus_pepys icon, Art Techno", body + fire_small())


def banner(display, mono) -> str:
    """1280 x 320 banner: the fire left, the name and part line right.

    The name is set as large as the room right of the fire allows, and the
    pair is centered on the banner's height.
    """
    s = 0.78
    body = frame(1280, 320, 10, 18, 3)
    body += (
        f'<g transform="translate({f(230 - 256 * s)} {f(160 - 205 * s)}) scale({s})">{fire()}</g>'
    )
    cx, room, gap = 820, 760, 34
    size = min(120, 120 * room / display.width(NAME, 120, 0.2))
    part = min(26, 26 * room / mono.width(PART, 26, 0.3))
    cap = display.cap * size / display.upm
    pcap = mono.cap * part / mono.upm
    base = (320 - (cap + gap + pcap)) / 2 + cap
    body += display.path(NAME, cx, base, size, 0.2, "middle", GOLD)
    body += mono.path(PART, cx, base + gap + pcap, part, 0.3, "middle", MINT)
    return svg(1280, 320, "corpus_pepys banner, Art Techno", body)


def render(src: Path, out: Path, w: int, h: int) -> None:
    """Render the SVG ``src`` to ``out`` at ``w`` x ``h`` px with headless Chrome.

    :param src: SVG file; its ``width`` attribute sets the CSS width.
    :param out: PNG to write.
    :param w: Output width in px.
    :param h: Output height in px.
    """
    svg_w = int(src.read_text().split('width="', 1)[1].split('"', 1)[0])
    subprocess.run(
        [
            at.CHROME,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--window-size={svg_w},{svg_w * h // w}",
            f"--force-device-scale-factor={w / svg_w}",
            f"--screenshot={out}",
            src.as_uri(),
        ],
        check=True,
        capture_output=True,
    )


def main() -> None:
    at.fetch_fonts()
    display = at.Face(at.CACHE / "JosefinSans.ttf", weight=600)
    mono = at.Face(at.CACHE / "IBMPlexMono-Medium.ttf")

    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    mark_svg = assets / "corpuspepys_logo.svg"
    icon_svg = assets / "corpuspepys_icon.svg"
    banner_svg = assets / "corpuspepys_banner.svg"
    mark_svg.write_text(mark(display, mono))
    icon_svg.write_text(icon())
    banner_svg.write_text(banner(display, mono))

    render(mark_svg, mark_svg.with_suffix(".png"), 1024, 1024)
    render(banner_svg, banner_svg.with_suffix(".png"), 2560, 640)


if __name__ == "__main__":
    main()

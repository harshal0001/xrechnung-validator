"""Draw the link preview card at frontend/public/og.png.

Run by hand when the card's wording changes, not as part of the build: it needs
Pillow and it downloads two webfonts, neither of which the service itself wants.

    uv run --with pillow python scripts/make_og_card.py

The card shows one real finding rather than a logo, in the page's own colours and
typefaces, because what this service does *is* that sentence. A card showing a
mark would be advertising the wrong thing.
"""

from __future__ import annotations

import argparse
import re
import tempfile
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Pinned by URL rather than by family name so the card does not change silently
# when Google ships a new version of either face.
FONT_CSS = "https://fonts.googleapis.com/css2?family={}&display=swap"
FACES = {
    "sans": ("Public+Sans:wght@400", 400),
    "sans-bold": ("Public+Sans:wght@700", 700),
    "mono": ("IBM+Plex+Mono:wght@500", 500),
}

PAPER, INK, INK_2, INK_3 = "#fbfaf7", "#131211", "#55524b", "#8d887e"
RULE, RULE_FIRM, SIGNAL, SIGNAL_WASH = "#ded9cf", "#211f1c", "#b5121b", "#fbf0ef"
WIDTH, HEIGHT, MARGIN = 1200, 630, 84


def fetch_face(spec: str, weight: int, into: Path) -> Path:
    """The TTF behind a Google Fonts family, saved locally."""
    request = urllib.request.Request(FONT_CSS.format(spec), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        css = response.read().decode()
    urls = re.findall(r"https://[^)]+\.ttf", css)
    if not urls:
        raise SystemExit(f"no TTF in the stylesheet for {spec}")
    target = into / f"{spec}-{weight}.ttf"
    urllib.request.urlretrieve(urls[-1], target)
    return target


def tracked(draw: ImageDraw.ImageDraw, xy, text, font, fill, extra: float = 3.0) -> None:
    """Letter-spaced text. Pillow has no tracking, and the eyebrow needs it."""
    x, y = xy
    for character in text:
        draw.text((x, y), character, font=font, fill=fill)
        x += draw.textlength(character, font=font) + extra


def draw_card(fonts: dict[str, Path], out: Path) -> None:
    sans = lambda size: ImageFont.truetype(str(fonts["sans"]), size)  # noqa: E731
    bold = lambda size: ImageFont.truetype(str(fonts["sans-bold"]), size)  # noqa: E731
    mono = lambda size: ImageFont.truetype(str(fonts["mono"]), size)  # noqa: E731

    card = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(card)

    # A firm rule down the left edge: the page uses the same device to mark a
    # block as part of the document rather than chrome around it.
    draw.rectangle([0, 0, 10, HEIGHT], fill=RULE_FIRM)

    y = MARGIN
    tracked(draw, (MARGIN, y), "EN 16931 · XRECHNUNG 3.0.2 · ZUGFERD", mono(19), INK_3)

    y += 62
    draw.text((MARGIN, y), "XRechnung-Validator", font=bold(76), fill=INK)

    y += 112
    draw.text(
        (MARGIN, y), "Prüft deutsche E-Rechnungen gegen den amtlichen", font=sans(31), fill=INK_2
    )
    draw.text(
        (MARGIN, y + 44),
        "Regelsatz und erklärt jeden Fehler im Klartext.",
        font=sans(31),
        fill=INK_2,
    )

    y += 128
    draw.line([MARGIN, y, WIDTH - MARGIN, y], fill=RULE, width=2)

    y += 40
    draw.rectangle([MARGIN, y, WIDTH - MARGIN, y + 96], fill=SIGNAL_WASH)
    draw.rectangle([MARGIN, y, MARGIN + 5, y + 96], fill=SIGNAL)
    draw.text((MARGIN + 30, y + 22), "BR-DE-15", font=mono(25), fill=SIGNAL)
    draw.text((MARGIN + 30, y + 56), "Die Käuferreferenz (BT-10) fehlt.", font=sans(26), fill=INK)

    card.save(out, optimize=True)
    print(f"{out}  {card.size[0]}x{card.size[1]}  {out.stat().st_size:,} bytes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("frontend/public/og.png"))
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as scratch:
        fonts = {n: fetch_face(s, w, Path(scratch)) for n, (s, w) in FACES.items()}
        draw_card(fonts, args.out)


if __name__ == "__main__":
    main()

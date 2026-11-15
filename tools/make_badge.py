"""Badges in Nick's layout: thin silver ring, dark disc, shoulders inside the disc, head popping out over the top.

    .venv\\Scripts\\python.exe -m tools.make_badge

Builds, all 256x256:
    ui/logo-va.png        Uncle Sam's still badge (favicon-style, from art/uncle-sam.png, Derrick's art 2026-11-15)
    ui/badge/ring.png     the shared silver ring and dark disc
    ui/badge/mask.png     where a figure may show: inside the disc, plus the pop-out strip above the ring
    ui/badge/nick.png     Nick alone, lifted off ui/logo.png (his disc is one flat color)
    ui/badge/sam.png      Uncle Sam alone, uncut (the page's mask cuts him, so he can move inside the ring)
The page stacks ring, then each figure inside the mask, so Sam can shove Nick out of the same ring. At rest the
stack matches logo.png / logo-va.png. Drawn at 4x and shrunk where shapes are drawn, so the edges stay smooth.
"""
import math
from collections import deque

from PIL import Image, ImageDraw

from guidebot import config

UI = config.ROOT / "ui"
ART = config.ROOT / "art" / "uncle-sam.png"
SIZE, K = 256, 4
CX, CY, R = 128, 159, 96          # Nick's ring, measured from logo.png (outer edge)
INNER = 92                        # silver band inside R, then the disc
CUT = 125                         # above this line a figure shows whole (the pop-out), within the ring's width
RING, EDGE, DISC = (214, 219, 225, 255), (12, 9, 7, 255), (26, 24, 22, 255)
SAM_SCALE, SAM_DX, SAM_DY = 0.2, 0, -2


def _box(r, k=K):
    return (CX * k - r * k, CY * k - r * k, CX * k + r * k, CY * k + r * k)


def ring() -> Image.Image:
    big = Image.new("RGBA", (SIZE * K, SIZE * K))
    d = ImageDraw.Draw(big)
    d.ellipse(_box(R), fill=RING)
    d.ellipse(_box(INNER + 0.6), fill=EDGE)
    d.ellipse(_box(INNER - 0.4), fill=DISC)
    return big.resize((SIZE, SIZE), Image.LANCZOS)


def mask() -> Image.Image:
    big = Image.new("L", (SIZE * K, SIZE * K))
    m = ImageDraw.Draw(big)
    m.ellipse(_box(INNER), fill=255)
    m.rectangle(((CX - R) * K, 0, (CX + R) * K, CUT * K), fill=255)
    return big.resize((SIZE, SIZE), Image.LANCZOS)


def sam() -> Image.Image:
    big = SIZE * K
    art = Image.open(ART).convert("RGBA")
    art = art.resize((round(art.width * SAM_SCALE * K), round(art.height * SAM_SCALE * K)), Image.LANCZOS)
    fig = Image.new("RGBA", (big, big))
    fig.paste(art, (round(SAM_DX * K), round(SAM_DY * K)), art)
    return fig.resize((SIZE, SIZE), Image.LANCZOS)


def nick() -> Image.Image:
    """Nick off logo.png: drop the disc (flood fill of its flat color from the disc's edge) and the ring."""
    im = Image.open(UI / "logo.png").convert("RGBA")
    px = im.load()
    near = lambda p, c, t: all(abs(p[i] - c[i]) <= t for i in range(3))
    gone = set()
    seeds = [(x, y) for y in range(SIZE) for x in range(SIZE)
             if INNER - 4 <= math.hypot(x - CX, y - CY) < INNER and near(px[x, y], DISC, 6)]
    todo = deque(seeds)
    gone.update(seeds)
    while todo:
        x, y = todo.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < SIZE and 0 <= ny < SIZE and (nx, ny) not in gone and near(px[nx, ny], DISC, 6):
                gone.add((nx, ny))
                todo.append((nx, ny))
    out = im.copy()
    o = out.load()
    for y in range(SIZE):
        for x in range(SIZE):
            p, d = px[x, y], math.hypot(x - CX, y - CY)
            ringish = d >= INNER - 1 and max(p[:3]) - min(p[:3]) < 25 and (min(p[:3]) > 90 or d < INNER + 2)
            if (x, y) in gone or ringish:
                o[x, y] = (0, 0, 0, 0)
    return out


def stack(fig: Image.Image) -> Image.Image:
    """What the page shows at rest: ring, then the figure inside the mask."""
    base = ring()
    cut = fig.copy()
    a = Image.composite(fig.getchannel("A"), Image.new("L", fig.size), mask())
    cut.putalpha(a)
    base.alpha_composite(cut)
    return base


def main():
    (UI / "badge").mkdir(exist_ok=True)
    layers = {"ring": ring(), "nick": nick(), "sam": sam()}
    for name, im in layers.items():
        im.save(UI / "badge" / f"{name}.png", optimize=True)
    css = Image.new("RGBA", (SIZE, SIZE), (255, 255, 255, 0))   # CSS masks read alpha
    css.putalpha(mask())
    css.save(UI / "badge" / "mask.png", optimize=True)
    stack(layers["sam"]).save(UI / "logo-va.png", optimize=True)


if __name__ == "__main__":
    main()

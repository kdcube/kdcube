import random
import sys
import os
import shutil

# Enable ANSI escape sequences on Windows
if os.name == 'nt':
    os.system('color')

# --- Terminal color capability detection -----------------------------------
#
# macOS Terminal.app supports 256 colors but NOT 24-bit truecolor.
# Modern terminals (VS Code, JetBrains, iTerm2, Windows Terminal, Linux)
# advertise truecolor support via COLORTERM=truecolor or COLORTERM=24bit.
#
# Detection priority:
#   1. COLORTERM=truecolor / 24bit  → truecolor confirmed
#   2. TERM_PROGRAM=Apple_Terminal  → 256-color fallback
#   3. Anything else                → assume truecolor (safe for modern terminals)

def _detect_truecolor() -> bool:
    """Return True if the terminal supports 24-bit truecolor ANSI escape codes."""
    colorterm = os.environ.get("COLORTERM", "").lower()
    if colorterm in ("truecolor", "24bit"):
        return True
    if os.environ.get("TERM_PROGRAM") == "Apple_Terminal":
        return False
    return True  # VS Code, JetBrains, iTerm2, Windows Terminal, etc.

_TRUECOLOR = _detect_truecolor()

# --- 256-color fallback helper ----------------------------------------------
_CUBE_VALS = [0, 95, 135, 175, 215, 255]

def _rgb_to_256(r: int, g: int, b: int) -> int:
    """Convert an RGB triple to the nearest ANSI 256-color palette index."""
    def _nearest(v):
        return min(range(6), key=lambda i: abs(_CUBE_VALS[i] - v))

    ri, gi, bi = _nearest(r), _nearest(g), _nearest(b)
    cube_idx = 16 + 36 * ri + 6 * gi + bi
    cr, cg, cb = _CUBE_VALS[ri], _CUBE_VALS[gi], _CUBE_VALS[bi]
    cube_dist = (cr - r) ** 2 + (cg - g) ** 2 + (cb - b) ** 2

    # Also compare against the 24-step grayscale ramp (indices 232–255)
    luma = int(0.299 * r + 0.587 * g + 0.114 * b)
    gray_n = max(0, min(23, round((luma - 8) / 10)))
    gray_idx = 232 + gray_n
    gv = 8 + 10 * gray_n
    gray_dist = (gv - r) ** 2 + (gv - g) ** 2 + (gv - b) ** 2

    return cube_idx if cube_dist <= gray_dist else gray_idx

# --- ANSI Truecolor Definitions ---
RESET    = "\033[0m"
RESET_BG = "\033[49m"   # reset background only (keep foreground)

def rgb_fg(r, g, b):
    if _TRUECOLOR:
        return f"\033[38;2;{r};{g};{b}m"
    return f"\033[38;5;{_rgb_to_256(r, g, b)}m"

def rgb_bg(r, g, b):
    if _TRUECOLOR:
        return f"\033[48;2;{r};{g};{b}m"
    return f"\033[48;5;{_rgb_to_256(r, g, b)}m"

# Background-color palette (main robot + label)
COLORS = {
    "0": RESET,
    "1": rgb_bg(198, 243, 241),   # Light Cyan
    "2": rgb_bg(1, 190, 178),     # Teal
    "3": rgb_bg(67, 114, 195),    # Blue
    "4": rgb_bg(255, 255, 255),   # White
    "5": rgb_bg(6, 16, 30),       # Dark Navy
    "6": rgb_bg(107, 99, 254),    # Purple
    "t": rgb_bg(198, 243, 241),   # label top face   (light cyan)
    "f": rgb_bg(1, 190, 178),     # label front face (teal)
    "s": rgb_bg(67, 114, 195),    # label side face  (blue)
}

# Foreground-color palette (half-block upper pixel)
FG = {
    "0": "",                        # transparent — no colour set
    "1": rgb_fg(198, 243, 241),
    "2": rgb_fg(1, 190, 178),
    "3": rgb_fg(67, 114, 195),
    "4": rgb_fg(255, 255, 255),
    "5": rgb_fg(6, 16, 30),
    "6": rgb_fg(107, 99, 254),
}

def _halfblock_char(top: str, bot: str) -> str:
    """
    Combine two pixel values into one terminal character using half-blocks.
      ▀  (U+2580)  = upper half filled with FG colour, lower half = BG colour
      ▄  (U+2584)  = lower half filled with FG colour, upper half = BG colour
    Each resulting glyph is exactly 1 char wide × ½ row tall → square pixel.
    """
    if top == "0" and bot == "0":
        return RESET + " "
    if top != "0" and bot == "0":
        return FG[top] + RESET_BG + "▀" + RESET
    if top == "0" and bot != "0":
        return FG[bot] + RESET_BG + "▄" + RESET
    # both coloured: upper half = top (fg), lower half = bot (bg)
    return FG[top] + COLORS[bot] + "▀" + RESET

# --- Block font (all glyphs are 7 rows tall) ---
# Uppercase = full 7-row cap height.
# Lowercase = x-height letters sit at the bottom; top rows are blank ("0000").
#   'b' has an ascender so it uses all 7 rows like a capital.
FONT_3D = {
    # ── uppercase ──────────────────────────────────────────────────────────
    "K": [
        "10001",
        "10010",
        "10100",
        "11000",
        "10100",
        "10010",
        "10001",
    ],
    "D": [
        "11110",
        "10001",
        "10001",
        "10001",
        "10001",
        "10001",
        "11110",
    ],
    "C": [
        "01111",
        "10000",
        "10000",
        "10000",
        "10000",
        "10000",
        "01111",
    ],
    # ── lowercase (x-height: top 3 rows blank, letter occupies rows 3-6) ──
    "u": [
        "0000",
        "0000",
        "1001",   # starts at row 2 — in sync with 'e' and bottom of 'b'
        "1001",
        "1001",
        "1001",
        "0111",
    ],
    "b": [          # ascender → full 7 rows
        "1000",
        "1000",
        "1110",
        "1001",
        "1001",
        "1001",
        "1110",
    ],
    "e": [
        "00000",
        "00000",
        "01110",   # top arc
        "10001",   # inner opening  ← the visible hole of the 'e'
        "11110",   # crossbar (open right — distinguishes 'e' from 'c')
        "10000",   # left stem
        "01110",   # bottom arc
    ],
}
FONT_3D_GAP = 2   # columns of blank space between letters


# --- Pixel Art Grids (16 columns x 13 rows) ---
BOX_ROBOT = [
    "0000100000006000",
    "0000300000006000",
    "0000300000006000",
    "0011111111111100",
    "0011111111111100",
    "0022222222222200",
    "0024442224442200",
    "3324542224542233",
    "3324442224442233",
    "0022222222222200",
    "0022255555222200",
    "0022222222222200",
    "0033333333333300",
]

ISO_ROBOT = [
    "0000100000060000",
    "0000300000060000",
    "0000300000060000",
    "0000111111110000",
    "0001111111122000",
    "0011111111222200",
    "0033222222222200",
    "0033244224422233",
    "0033245224522233",
    "0033222222222200",
    "0033222555222200",
    "0033222222222200",
    "0000333333333300",
]


# --- Mini Robot (8 cols × 8 pixel-rows, rendered via half-blocks) -----------
# Half-block rendering packs 2 pixel-rows into 1 terminal row.
# 8 pixel-cols × 8 pixel-rows → 8 terminal chars × 4 terminal rows.
# At ~2:1 char aspect ratio each half-block pixel is square → 1:1 robot.
# Features pinned to cols 1 & 6 (symmetric).
# Pairing plan (each pair → 1 terminal row via half-blocks):
#   pair (0,1): antenna tips  on transparent + empty  → tiny ▀ marks floating above head
#   pair (2,3): solid head    + solid head            → fully SOLID light-cyan bar
#   pair (4,5): white eyes    + dark pupils           → eye+pupil in one half-char height
#   pair (6,7): teal body     + blue legs             → body top, leg stubs at cols 1 & 6
MINI_ROBOT = [
    # Half-block pairing: every 2 pixel rows → 1 terminal row.
    # Same-colour pairs  → solid BG space (no ▀ seam artefact).
    # Mixed-colour pairs → ▀ half-block (upper=FG, lower=BG).
    #
    # Rule: same colour in both rows of a pair → solid BG space (no ▀, no gaps).
    #       different colours                  → ▀ half-block (eyes only).
    #
    # Layout  (14 pixel rows → 7 terminal rows):
    #   pair (0,1)  : SOLID light-cyan tip — LEFT antenna only (col1); right = transparent
    #   pair (2,3)  : SOLID shafts — blue(3) col1, purple(6) col6  (two distinct colours)
    #   pair (4,5)  : SOLID light-cyan head bar
    #   pair (6,7)  : 2-wide eyes cols 1-2 & 5-6 — white upper / dark lower
    #   pair (8,9)  : SOLID dark(5) mouth — 2 pixels centered at cols 3-4
    #   pair (10,11): SOLID teal body
    #   pair (12,13): SOLID blue leg squares — cols 1 & 6
    "01000000",   # row  0 — left tip: light-cyan(1) col1 only, right antenna has no tip
    "01000000",   # row  1 — same  →  SOLID light-cyan tip (left only)
    "03000060",   # row  2 — shafts: blue(3) col1, purple(6) col6
    "03000060",   # row  3 — same  →  SOLID shafts, distinct colours
    "11111111",   # row  4 — head
    "11111111",   # row  5 — same  →  SOLID light-cyan
    "24422442",   # row  6 — face: white(4) eyes at cols 1-2 & 5-6
    "25522552",   # row  7 — dark(5) pupils  →  ▀ white upper / dark lower
    "22255222",   # row  8 — mouth: dark(5) at cols 3-4 (centered smile)
    "22222222",   # row  9 — same  →  SOLID dark mouth
    "22222222",   # row 10 — body
    "22222222",   # row 11 — same  →  SOLID teal
    "03000030",   # row 12 — legs: blue(3) at cols 1 & 6
    "03000030",   # row 13 — same  →  SOLID blue legs
]


def _build_front_mask(text: str) -> list:
    """Build a 2-D binary mask (list-of-lists) for the given text string."""
    rows = [[] for _ in range(7)]
    first = True
    for ch in text:   # preserve case so lowercase glyphs are looked up correctly
        glyph = FONT_3D.get(ch)
        if glyph is None:
            continue
        if not first:
            for r in range(7):
                rows[r].extend([0] * FONT_3D_GAP)
        first = False
        for r in range(7):
            rows[r].extend([1 if px == "1" else 0 for px in glyph[r]])
    return rows


def _build_label_3d(text: str) -> tuple:
    """
    Render 'text' as a 3-D extruded pixel label.

    Each lit pixel becomes a tiny cube viewed from upper-left:
      • front face  ('f') – main teal colour, drawn at (x, y)
      • top face    ('t') – light cyan,  drawn at (x, y-1) only for
                            pixels whose top neighbour is empty
      • side face   ('s') – blue,        drawn at (x+1, y) only for
                            pixels whose right neighbour is empty

    Draw order: side → top → front  (front is never overwritten).
    Returns (bitmap_rows, baseline_row).
    """
    front = _build_front_mask(text)
    h = len(front)
    w = len(front[0]) if front else 0

    cells: dict = {}

    # ── side faces (right-edge pixels only) ──────────────────────────────────
    for y in range(h):
        for x in range(w):
            if front[y][x]:
                right_empty = (x + 1 >= w) or (not front[y][x + 1])
                if right_empty:
                    cells[(x + 1, y)] = "s"

    # ── top faces (top-edge pixels only) ─────────────────────────────────────
    for y in range(h):
        for x in range(w):
            if front[y][x]:
                top_empty = (y - 1 < 0) or (not front[y - 1][x])
                if top_empty:
                    # only place top face if that cell isn't already a front face
                    if (x, y - 1) not in cells or cells[(x, y - 1)] != "f":
                        cells[(x, y - 1)] = "t"

    # ── front faces (drawn last – highest priority) ───────────────────────────
    for y in range(h):
        for x in range(w):
            if front[y][x]:
                cells[(x, y)] = "f"

    if not cells:
        return [], 0

    min_x = min(x for x, _ in cells)
    max_x = max(x for x, _ in cells)
    min_y = min(y for _, y in cells)
    max_y = max(y for _, y in cells)

    out_w = max_x - min_x + 1
    out_h = max_y - min_y + 1
    canvas = [["." for _ in range(out_w)] for _ in range(out_h)]

    for (x, y), key in cells.items():
        canvas[y - min_y][x - min_x] = key

    # baseline = row index of the bottom of the front faces inside the bitmap
    baseline_row = (h - 1) - min_y
    return ["".join(row) for row in canvas], baseline_row


def _trim_bitmap_rows(rows: list[str], blank: str = "0") -> list[str]:
    if not rows:
        return rows
    min_x = None
    max_x = None
    for row in rows:
        for idx, ch in enumerate(row):
            if ch != blank:
                min_x = idx if min_x is None else min(min_x, idx)
                max_x = idx if max_x is None else max(max_x, idx)
    if min_x is None or max_x is None:
        return rows
    return [row[min_x : max_x + 1] for row in rows]


def _terminal_columns(default: int = 120) -> int:
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except Exception:
        return default


def _banner_layout(
    label_width: int,
    main_width: int,
    mini_width: int,
) -> tuple[str, str, str]:
    """Choose a pixel width that fits the current terminal."""
    columns = _terminal_columns()

    full_width_double = label_width * 2 + main_width * 2 + 2 + mini_width
    full_width_single = label_width + main_width + 2 + mini_width

    if columns >= full_width_double:
        return "full", "  ", "  "
    if columns >= full_width_single:
        return "full", " ", " "
    return "stacked", " ", " "


def _render_label_row(row: str, pixel_fill: str, pixel_blank: str) -> str:
    parts: list[str] = []
    for ch in row:
        if ch == ".":
            parts.append(pixel_blank)
        else:
            parts.append(COLORS[ch] + pixel_fill + RESET)
    return "".join(parts)


def _center_pad(rendered_width: int) -> str:
    columns = _terminal_columns()
    return " " * max(0, (columns - rendered_width) // 2)


def print_cli_banner():
    """Print the KDCube label, main robot, and mini robot side-by-side."""
    selected_robot = _trim_bitmap_rows(random.choice([BOX_ROBOT, ISO_ROBOT]))
    robot_height = len(selected_robot)

    label_bitmap, label_baseline = _build_label_3d("KDCube")
    label_height = len(label_bitmap)
    label_width = len(label_bitmap[0]) if label_bitmap else 0

    mini_pix_rows = len(MINI_ROBOT)          # pixel rows  (8)
    mini_term_rows = (mini_pix_rows + 1) // 2  # terminal rows (4, via half-blocks)
    mini_width     = len(MINI_ROBOT[0])
    main_width     = len(selected_robot[0]) if selected_robot else 0
    layout_mode, pixel_fill, pixel_blank = _banner_layout(label_width, main_width, mini_width)

    # Align label baseline with the last robot row
    text_start  = (robot_height - 1) - label_baseline
    # Align mini robot bottom with main robot bottom
    mini_start  = robot_height - mini_term_rows

    sys.stdout.write("\n")

    if layout_mode == "stacked":
        if label_bitmap:
            label_render_width = label_width * len(pixel_fill)
            label_pad = _center_pad(label_render_width)
            for row in label_bitmap:
                sys.stdout.write(label_pad + _render_label_row(row, pixel_fill, pixel_blank) + "\n")
            sys.stdout.write("\n")

        robot_render_width = main_width * len(pixel_fill) + 2 + mini_width
        robot_pad = _center_pad(robot_render_width)
        for i in range(robot_height):
            sys.stdout.write(robot_pad)

            for pixel in selected_robot[i]:
                if pixel == "0":
                    sys.stdout.write(RESET + pixel_blank)
                else:
                    sys.stdout.write(COLORS[pixel] + pixel_fill + RESET)

            sys.stdout.write("  ")
            term_rel = i - mini_start
            if 0 <= term_rel < mini_term_rows:
                pix = term_rel * 2
                row_top = MINI_ROBOT[pix]
                row_bot = MINI_ROBOT[pix + 1] if pix + 1 < mini_pix_rows else "0" * mini_width
                for t, b in zip(row_top, row_bot):
                    sys.stdout.write(_halfblock_char(t, b))
            else:
                sys.stdout.write(" " * mini_width)
            sys.stdout.write("\n")

        sys.stdout.write("\n")
        return

    for i in range(robot_height):
        # ── 3-D label ────────────────────────────────────────────────────────
        rel = i - text_start
        if 0 <= rel < label_height:
            sys.stdout.write(_render_label_row(label_bitmap[rel], pixel_fill, pixel_blank))
        else:
            sys.stdout.write(pixel_blank * label_width)

        # ── main robot ───────────────────────────────────────────────────────
        for pixel in selected_robot[i]:
            if pixel == "0":
                sys.stdout.write(RESET + pixel_blank)
            else:
                sys.stdout.write(COLORS[pixel] + pixel_fill + RESET)

        # ── mini robot (half-block, 1:1 square pixels) ───────────────────────
        sys.stdout.write("  ")
        term_rel = i - mini_start
        if 0 <= term_rel < mini_term_rows:
            pix = term_rel * 2
            row_top = MINI_ROBOT[pix]
            row_bot = MINI_ROBOT[pix + 1] if pix + 1 < mini_pix_rows else "0" * mini_width
            for t, b in zip(row_top, row_bot):
                sys.stdout.write(_halfblock_char(t, b))
        else:
            sys.stdout.write(" " * mini_width)

        sys.stdout.write("\n")

    sys.stdout.write("\n")


if __name__ == "__main__":
    print_cli_banner()

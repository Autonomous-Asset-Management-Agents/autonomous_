import logging
import math
import os
import pathlib
import tempfile
import urllib.request
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# #1643 review fix: this module used `logger` (L150/155/272) and `PROJECT_ROOT` (font paths)
# without importing/defining them -> guaranteed NameError on first call. Define both here.
logger = logging.getLogger(__name__)
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

# --- BRANDING & STYLE-GUIDE CONSTANTS ---
BG_COLOR = "#000000"  # pure black canvas
CARD_BG_COLOR = "#1c1c1e"  # rounded cards
CARD_BORDER_COLOR = (
    "#2c2c2e"  # solid border (equivalent to rgba(255,255,255,0.08) on black)
)
TEXT_COLOR = "#f4f4f6"  # foreground text
PROFIT_COLOR = "#30d158"  # green (success / bull)
LOSS_COLOR = "#ff453a"  # red (destructive / bear)
CHART_LINE_COLOR = "#00c27a"  # signature green (matches Overview.tsx)
CHART_GRID_COLOR = "#262626"  # thin grid
TEXT_MUTED_30 = "#606062"  # 30% white blended on CARD_BG_COLOR (#1c1c1e)
TEXT_MUTED_40 = "#777778"  # 40% white blended on CARD_BG_COLOR (#1c1c1e)

# Resolution 1080x1920 (9:16 vertical)
WIDTH, HEIGHT = 1080, 1920

# Safe zones (Symmetric for perfect visual centering, keeping elements out of YouTube Shorts overlay regions)
SAFE_LEFT = 140
SAFE_RIGHT = 140
SAFE_TOP = 120
SAFE_BOTTOM = 260
CONTENT_WIDTH = WIDTH - SAFE_LEFT - SAFE_RIGHT
CARD_RADIUS = 20


# Font names
FONT_INTER = "Inter"
FONT_MONO = "JetBrainsMono"


def create_aurora_gradient(width: int, height: int, t: float = 0.0) -> Image.Image:
    """Generates a premium, subtle dark 'aurora/mesh gradient' background similar to Gemini.
    Uses a downscaled rendering and blur optimization for speed and smooth transitions.
    The colors animate slowly over time `t` (in seconds).
    """
    scale = 4
    sw, sh = width // scale, height // scale

    # Base pure black color (like start and end screens)
    base = Image.new("RGB", (width, height), (0, 0, 0))

    # Small overlay for fast blur
    overlay = Image.new("RGB", (sw, sh), (0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Draw scaled-down blobs with larger, more organic wave displacements & breathing radius (Gemini style)
    # Blob 1: White (center-left)
    cx1 = (200 + 250 * math.sin(0.8 * t)) / scale
    cy1 = (600 + 220 * math.cos(0.6 * t)) / scale
    r1 = (450 + 100 * math.sin(0.7 * t)) / scale
    draw.ellipse([cx1 - r1, cy1 - r1, cx1 + r1, cy1 + r1], fill=(255, 255, 255))

    # Blob 2: Cream-White (top-right)
    cx2 = (800 + 280 * math.sin(-0.7 * t)) / scale
    cy2 = (400 + 240 * math.cos(0.8 * t)) / scale
    r2 = (500 + 120 * math.cos(0.6 * t)) / scale
    draw.ellipse([cx2 - r2, cy2 - r2, cx2 + r2, cy2 + r2], fill=(245, 245, 240))

    # Blob 3: Green accent (middle-right to bottom-right)
    cx3 = (850 + 260 * math.sin(0.9 * t)) / scale
    cy3 = (1300 + 280 * math.cos(-0.6 * t)) / scale
    r3 = (450 + 100 * math.cos(0.8 * t)) / scale
    draw.ellipse([cx3 - r3, cy3 - r3, cx3 + r3, cy3 + r3], fill=(0, 194, 122))

    # Blob 4: Cream-White (bottom-left)
    cx4 = (250 + 240 * math.sin(-0.8 * t)) / scale
    cy4 = (1400 + 260 * math.cos(0.9 * t)) / scale
    r4 = (450 + 120 * math.sin(0.5 * t)) / scale
    draw.ellipse([cx4 - r4, cy4 - r4, cx4 + r4, cy4 + r4], fill=(245, 245, 240))

    # Blur the small image
    blurred_small = overlay.filter(ImageFilter.GaussianBlur(radius=40))

    # Resize up to full size
    try:
        resample = Image.Resampling.BILINEAR
    except AttributeError:
        resample = Image.BILINEAR

    blurred_large = blurred_small.resize((width, height), resample)

    # Blend with base (higher alpha for a clearly animated, premium Gemini glow)
    return Image.blend(base, blurred_large, alpha=0.30)


def get_font_path(font_name: str, bold: bool = False) -> Optional[str]:
    """Helper to download and retrieve Google Fonts for rendering consistency.
    Checks local assets/fonts first, then falls back to temp directory.
    """
    suffix = "-Bold.ttf" if bold else "-Regular.ttf"

    # Prioritize exact local fonts in repo root assets/fonts/
    repo_root = pathlib.Path(PROJECT_ROOT).parent
    project_fonts_path = repo_root / "assets" / "fonts" / f"{font_name}{suffix}"
    if project_fonts_path.exists() and project_fonts_path.stat().st_size > 1000:
        return str(project_fonts_path)

    # Fallback to PROJECT_ROOT/assets/fonts/ if present
    project_fonts_path_alt = (
        pathlib.Path(PROJECT_ROOT) / "assets" / "fonts" / f"{font_name}{suffix}"
    )
    if project_fonts_path_alt.exists() and project_fonts_path_alt.stat().st_size > 1000:
        return str(project_fonts_path_alt)

    temp_dir = pathlib.Path(tempfile.gettempdir())
    local_path = temp_dir / f"{font_name}{suffix}"

    if local_path.exists():
        try:
            if local_path.stat().st_size > 1000:
                return str(local_path)
            else:
                local_path.unlink()
        except Exception:
            pass

    urls = {
        "Inter-Regular.ttf": "https://fonts.gstatic.com/s/inter/v20/UcCO3FwrK3iLTeHuS_nVMrMxCp50SjIw2boKoduKmMEVuI6fAZ9hjQ.ttf",
        "Inter-Bold.ttf": "https://fonts.gstatic.com/s/inter/v20/UcCO3FwrK3iLTeHuS_nVMrMxCp50SjIw2boKoduKmMEVuFuYAZ9hjQ.ttf",
        "JetBrainsMono-Regular.ttf": "https://raw.githubusercontent.com/JetBrains/JetBrainsMono/master/fonts/ttf/JetBrainsMono-Regular.ttf",
        "JetBrainsMono-Bold.ttf": "https://raw.githubusercontent.com/JetBrains/JetBrainsMono/master/fonts/ttf/JetBrainsMono-Bold.ttf",
    }

    filename = f"{font_name}{suffix}"
    url = urls.get(filename)
    if not url:
        return None

    try:
        # Standard verified download
        urllib.request.urlretrieve(url, local_path)
        logger.info(
            "Successfully downloaded font %s to %s (Standard)", filename, local_path
        )
        return str(local_path)
    except Exception as e1:
        logger.warning(
            "Failed standard download for font %s: %s.",
            filename,
            e1,
        )
        if local_path.exists():
            local_path.unlink()
        return None


def get_system_font_fallback(font_name: str, bold: bool = False) -> Optional[str]:
    """Retrieves path to a matching scalable system font on Windows, macOS, or Linux."""
    import sys

    paths = []

    if sys.platform.startswith("win"):
        win_fonts = os.environ.get("WINDIR", "C:\\Windows") + "\\Fonts"
        if font_name == FONT_MONO:
            paths.extend(
                [
                    os.path.join(win_fonts, "consolab.ttf" if bold else "consola.ttf"),
                    os.path.join(win_fonts, "courbd.ttf" if bold else "cour.ttf"),
                ]
            )
        else:
            paths.extend(
                [
                    os.path.join(win_fonts, "segoeuib.ttf" if bold else "segoeui.ttf"),
                    os.path.join(win_fonts, "arialbd.ttf" if bold else "arial.ttf"),
                ]
            )
    elif sys.platform == "darwin":
        mac_fonts = "/Library/Fonts"
        mac_sys_fonts = "/System/Library/Fonts"
        if font_name == FONT_MONO:
            paths.extend(
                [
                    os.path.join(mac_sys_fonts, "Courier.dfont"),
                    os.path.join(mac_sys_fonts, "Menlo.ttc"),
                ]
            )
        else:
            paths.extend(
                [
                    os.path.join(mac_sys_fonts, "SFNS.ttf"),
                    os.path.join(mac_fonts, "Arial Bold.ttf" if bold else "Arial.ttf"),
                    os.path.join(mac_sys_fonts, "Helvetica.ttc"),
                ]
            )
    else:
        linux_fonts = "/usr/share/fonts/truetype"
        if font_name == FONT_MONO:
            paths.extend(
                [
                    os.path.join(
                        linux_fonts,
                        (
                            "dejavu/DejaVuSansMono-Bold.ttf"
                            if bold
                            else "dejavu/DejaVuSansMono.ttf"
                        ),
                    ),
                    os.path.join(
                        linux_fonts,
                        (
                            "freefont/FreeMonoBold.ttf"
                            if bold
                            else "freefont/FreeMono.ttf"
                        ),
                    ),
                ]
            )
        else:
            paths.extend(
                [
                    os.path.join(
                        linux_fonts,
                        (
                            "dejavu/DejaVuSans-Bold.ttf"
                            if bold
                            else "dejavu/DejaVuSans.ttf"
                        ),
                    ),
                    os.path.join(
                        linux_fonts,
                        (
                            "liberation/LiberationSans-Bold.ttf"
                            if bold
                            else "liberation/LiberationSans-Regular.ttf"
                        ),
                    ),
                ]
            )

    for p in paths:
        if os.path.exists(p):
            return p
    return None


def get_pil_font(font_name: str, size: int, bold: bool = False) -> ImageFont.ImageFont:
    """Gets PIL ImageFont, falling back to a scalable system font, and finally load_default."""
    path = get_font_path(font_name, bold)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass

    sys_path = get_system_font_fallback(font_name, bold)
    if sys_path:
        try:
            return ImageFont.truetype(sys_path, size)
        except Exception:
            pass

    logger.warning(
        "No custom or system TTF font found. Using non-scalable default font."
    )
    return ImageFont.load_default()


def get_logo_font(size: int) -> ImageFont.ImageFont:
    """Gets the font for the 'a' character in the logo.
    Tries to find a system monospace font like Consolas, Courier, or Menlo to get the single-storey 'a' shape.
    """
    import sys

    paths = []
    if sys.platform.startswith("win"):
        win_fonts = os.environ.get("WINDIR", "C:\\Windows") + "\\Fonts"
        paths.append(os.path.join(win_fonts, "consolab.ttf"))
        paths.append(os.path.join(win_fonts, "consola.ttf"))
        paths.append(os.path.join(win_fonts, "courbd.ttf"))
    elif sys.platform == "darwin":
        paths.append("/System/Library/Fonts/Menlo.ttc")
        paths.append("/Library/Fonts/Courier New Bold.ttf")
    else:
        paths.append("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf")

    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    # Fallback to general mono
    return get_pil_font(FONT_MONO, size, bold=True)


def draw_app_logo_icon(draw: ImageDraw.ImageDraw, x: int, y: int, size: int):
    """Draws the brand icon: rounded rectangle with 'a' and a green underline, matching public/favicon.svg."""
    # Rounded rect background
    draw.rounded_rectangle(
        [x, y, x + size, y + size], radius=int(size * 0.22), fill="#0a0a0a"
    )

    # 'a' text using single-storey system monospace font (Consolas/Menlo/DejaVu Sans Mono)
    font_a = get_logo_font(int(size * 0.65))
    bbox = draw.textbbox((0, 0), "a", font=font_a)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    # Center letter horizontally and vertically with slight baseline adjustments
    tx = x + (size - tw) // 2 - bbox[0]
    ty = y + (size - th) // 2 - bbox[1] - int(size * 0.05)
    draw.text((tx, ty), "a", fill="#ffffff", font=font_a)

    # Underline relative coordinates (x=20/64, y=46/64, w=24/64, h=4/64)
    ux = x + int(size * 0.3125)
    uy = y + int(size * 0.71875)
    uw = int(size * 0.375)
    uh = max(1, int(size * 0.0625))
    draw.rounded_rectangle(
        [ux, uy, ux + uw, uy + uh], radius=max(1, int(uh * 0.375)), fill="#00c27a"
    )


def draw_header_logo(draw: ImageDraw.ImageDraw, x=SAFE_LEFT, y=80):
    """Draws the app logo icon (underlined 'a' rounded square) followed by 'autonomous_' wordmark."""
    icon_size = 48
    draw_app_logo_icon(draw, x, y, icon_size)

    # Text offset: space it by 16px
    text_x = x + icon_size + 16
    font = get_pil_font(FONT_INTER, 44, bold=True)

    # Vertically center text to the icon
    bbox = draw.textbbox((0, 0), "autonomous", font=font)
    th = bbox[3] - bbox[1]
    text_y = y + (icon_size - th) // 2 - bbox[1]

    draw.text((text_x, text_y), "autonomous", fill="#ffffff", font=font)

    # Draw green caret next to it
    bbox_word = draw.textbbox((text_x, text_y), "autonomous", font=font)
    draw.text((bbox_word[2] + 4, text_y), "_", fill="#00c27a", font=font)


def draw_outro_logo(draw: ImageDraw.ImageDraw, cx, cy):
    """Draws the large logo icon followed by 'autonomous_' centered."""
    icon_size = 64
    font = get_pil_font(FONT_INTER, 64, bold=True)
    text = "autonomous"

    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    # Caret width
    bbox_caret = draw.textbbox((0, 0), "_", font=font)
    tcw = bbox_caret[2] - bbox_caret[0]

    total_w = icon_size + 24 + tw + 8 + tcw
    start_x = cx - total_w // 2

    # Draw icon
    icon_y = cy - icon_size // 2
    draw_app_logo_icon(draw, start_x, icon_y, icon_size)

    # Draw text
    text_x = start_x + icon_size + 24
    text_y = cy - th // 2 - bbox[1]
    draw.text((text_x, text_y), text, fill="#ffffff", font=font)

    # Draw caret
    draw.text((text_x + tw + 8, text_y), "_", fill="#00c27a", font=font)

import os
from typing import Any, Dict

from PIL import Image, ImageDraw

from scripts.shorts_generator.video_branding import (
    CARD_BG_COLOR,
    CARD_BORDER_COLOR,
    CARD_RADIUS,
    CONTENT_WIDTH,
    FONT_INTER,
    FONT_MONO,
    HEIGHT,
    LOSS_COLOR,
    PROFIT_COLOR,
    SAFE_LEFT,
    SAFE_RIGHT,
    TEXT_MUTED_30,
    WIDTH,
    create_aurora_gradient,
    draw_header_logo,
    get_pil_font,
)
from scripts.shorts_generator.video_utils import fmt_eur, fmt_pnl_eur


def render_scene2_frame(
    data: Dict[str, Any], caption: str, chart_path: str, filepath_or_t: Any
):
    """Renders the Portfolio Status slide (Scene 2) with optimized vertical layout."""
    # Create transparent overlay canvas for cards and text
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Header logo
    draw_header_logo(draw)

    # Title (positioned for vertical balance)
    font_title = get_pil_font(FONT_INTER, 60, bold=True)
    draw.text((SAFE_LEFT, 260), "Daily Recap", fill="#ffffff", font=font_title)

    # Dedicated Card Box for Konto-Update
    card_y = 380
    card_height = 300
    draw.rounded_rectangle(
        [SAFE_LEFT, card_y, WIDTH - SAFE_RIGHT, card_y + card_height],
        radius=CARD_RADIUS,
        fill=CARD_BG_COLOR,
        outline=CARD_BORDER_COLOR,
        width=2,
    )

    # Card Label / Eyebrow (Inter, 24px, uppercase, white/30)
    font_eyebrow = get_pil_font(FONT_INTER, 24, bold=True)
    draw.text(
        (SAFE_LEFT + 40, card_y + 35),
        "ACCOUNT UPDATE",
        fill=TEXT_MUTED_30,
        font=font_eyebrow,
    )

    # Account Value (JetBrains Mono, 80px)
    font_val = get_pil_font(FONT_MONO, 80, bold=True)
    draw.text(
        (SAFE_LEFT + 40, card_y + 80),
        fmt_eur(data["total_equity"]),
        fill="#ffffff",
        font=font_val,
    )

    # PnL Badge Card with premium blended app colors
    pnl_sign_pct = "+" if data["pnl_pct"] >= 0 else "−"
    chip_text = (
        f"{fmt_pnl_eur(data['pnl_abs'])} ({pnl_sign_pct}{abs(data['pnl_pct']):.2f}%)"
    )

    # App-aligned badge style tokens
    if data["pnl_pct"] >= 0:
        chip_bg = "#15301d"  # translucent green on dark surface
        chip_border = "#1e5c30"  # matching green border
        chip_text_color = PROFIT_COLOR  # system green
    else:
        chip_bg = "#331818"  # translucent red on dark surface
        chip_border = "#5c1e1e"  # matching red border
        chip_text_color = LOSS_COLOR  # system red

    # YTD performance details
    ytd_pct = data.get("ytd_pct", 5.00)
    ytd_sign = "+" if ytd_pct >= 0 else "−"
    ytd_text = f"YTD: {ytd_sign}{abs(ytd_pct):.2f}%"

    ytd_bg = "#15301d" if ytd_pct >= 0 else "#331818"
    ytd_border = "#1e5c30" if ytd_pct >= 0 else "#5c1e1e"
    ytd_color = PROFIT_COLOR if ytd_pct >= 0 else LOSS_COLOR

    font_chip = get_pil_font(FONT_MONO, 28, bold=True)

    # Calculate text sizes
    c_bbox = draw.textbbox((0, 0), chip_text, font=font_chip)
    y_bbox = draw.textbbox((0, 0), ytd_text, font=font_chip)

    # Calculate text dimensions
    c_tw = c_bbox[2] - c_bbox[0]
    c_th = c_bbox[3] - c_bbox[1]
    y_tw = y_bbox[2] - y_bbox[0]
    y_th = y_bbox[3] - y_bbox[1]

    # Equalize width to the maximum of the two with a 330px minimum (allowing symmetric layout)
    chip_w = max(c_tw + 40, y_tw + 40, 330)
    chip_h = 68  # uniform card height

    cx0, cy0 = SAFE_LEFT + 40, card_y + 190

    # Draw Daily PnL Chip
    draw.rounded_rectangle(
        [cx0, cy0, cx0 + chip_w, cy0 + chip_h],
        radius=10,
        fill=chip_bg,
        outline=chip_border,
        width=2,
    )
    # Center text inside the chip
    tx1 = cx0 + (chip_w - c_tw) // 2 - c_bbox[0]
    ty1 = cy0 + (chip_h - c_th) // 2 - c_bbox[1]
    draw.text((tx1, ty1), chip_text, fill=chip_text_color, font=font_chip)

    # Draw YTD PnL Chip next to Daily PnL Chip
    yx0 = cx0 + chip_w + 20
    draw.rounded_rectangle(
        [yx0, cy0, yx0 + chip_w, cy0 + chip_h],
        radius=10,
        fill=ytd_bg,
        outline=ytd_border,
        width=2,
    )
    # Center text inside the YTD chip
    tx2 = yx0 + (chip_w - y_tw) // 2 - y_bbox[0]
    ty2 = cy0 + (chip_h - y_th) // 2 - y_bbox[1]
    draw.text((tx2, ty2), ytd_text, fill=ytd_color, font=font_chip)

    # Paste Performance Chart wrapped in a rounded card with 20px padding
    chart_y = card_y + card_height + 60
    chart_h = 800
    draw.rounded_rectangle(
        [SAFE_LEFT, chart_y, WIDTH - SAFE_RIGHT, chart_y + chart_h],
        radius=CARD_RADIUS,
        fill=CARD_BG_COLOR,
        outline=CARD_BORDER_COLOR,
        width=2,
    )

    if os.path.exists(chart_path):
        chart_img = Image.open(chart_path)
        # Resize to fit inside the card with 20px padding
        chart_img = chart_img.resize((CONTENT_WIDTH - 40, chart_h - 40))
        img.paste(chart_img, (SAFE_LEFT + 20, chart_y + 20))

    if isinstance(filepath_or_t, str):
        # Merge with a static background gradient for mockup image saving
        bg = create_aurora_gradient(WIDTH, HEIGHT, 0.0)
        bg.paste(img, (0, 0), img)
        bg.save(filepath_or_t)
    else:
        return img

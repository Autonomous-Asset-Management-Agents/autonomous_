import pathlib
from typing import Any, Dict, List

from PIL import Image, ImageDraw

from scripts.shorts_generator.video_branding import (
    CARD_BG_COLOR,
    CARD_BORDER_COLOR,
    CARD_RADIUS,
    FONT_INTER,
    FONT_MONO,
    HEIGHT,
    LOSS_COLOR,
    PROFIT_COLOR,
    SAFE_LEFT,
    SAFE_RIGHT,
    TEXT_MUTED_40,
    WIDTH,
    create_aurora_gradient,
    draw_header_logo,
    draw_outro_logo,
    get_pil_font,
)


def render_scene4_frame(data: Dict[str, Any], caption: str, filepath_or_t: Any):
    """Renders the Market Regime / Risk parameters slide (Scene 4) with optimized proportions (three stacked cards)."""
    # Create transparent overlay canvas for cards and text
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw_header_logo(draw)

    # Title
    font_title = get_pil_font(FONT_INTER, 60, bold=True)
    draw.text((SAFE_LEFT, 280), "Market Regime", fill="#ffffff", font=font_title)

    # Three full-width cards vertically stacked
    c1_y0 = 420
    c1_h = 260
    c1_y1 = c1_y0 + c1_h

    c2_y0 = 730
    c2_h = 260
    c2_y1 = c2_y0 + c2_h

    c3_y0 = 1040
    c3_h = 260
    c3_y1 = c3_y0 + c3_h

    font_label = get_pil_font(FONT_MONO, 24, bold=True)
    font_val = get_pil_font(FONT_MONO, 56, bold=True)

    # --- Card 1: AI Market Phase ---
    draw.rounded_rectangle(
        [SAFE_LEFT, c1_y0, WIDTH - SAFE_RIGHT, c1_y1],
        radius=CARD_RADIUS,
        fill=CARD_BG_COLOR,
        outline=CARD_BORDER_COLOR,
        width=2,
    )
    draw.text(
        (SAFE_LEFT + 40, c1_y0 + 50),
        "AI MARKET PHASE",
        fill=TEXT_MUTED_40,
        font=font_label,
    )
    regime = data["market_regime"].upper()
    regime_color = (
        PROFIT_COLOR
        if regime == "BULLISH"
        else (LOSS_COLOR if regime == "BEARISH" else "#ffffff")
    )
    draw.text((SAFE_LEFT + 40, c1_y0 + 115), regime, fill=regime_color, font=font_val)

    # --- Card 2: Volatility (VIX) ---
    draw.rounded_rectangle(
        [SAFE_LEFT, c2_y0, WIDTH - SAFE_RIGHT, c2_y1],
        radius=CARD_RADIUS,
        fill=CARD_BG_COLOR,
        outline=CARD_BORDER_COLOR,
        width=2,
    )
    draw.text(
        (SAFE_LEFT + 40, c2_y0 + 50),
        "VOLATILITY (VIX)",
        fill=TEXT_MUTED_40,
        font=font_label,
    )
    vix_str = f"{data['vix']:.2f}"
    draw.text((SAFE_LEFT + 40, c2_y0 + 115), vix_str, fill="#ffffff", font=font_val)

    # --- Card 3: Drawdown Limit ---
    # Compliance (R6-3c): the drawdown limit is a PUBLIC financial figure and MUST be
    # data-derived (authoritative source: RiskManager daily_drawdown_limit_percent, exposed
    # via the audited public snapshot), never fabricated. If unavailable, OMIT the card
    # entirely (fail-closed) rather than display a guessed number.
    drawdown_limit_pct = data.get("daily_drawdown_limit_pct")
    if drawdown_limit_pct is not None:
        draw.rounded_rectangle(
            [SAFE_LEFT, c3_y0, WIDTH - SAFE_RIGHT, c3_y1],
            radius=CARD_RADIUS,
            fill=CARD_BG_COLOR,
            outline=CARD_BORDER_COLOR,
            width=2,
        )
        draw.text(
            (SAFE_LEFT + 40, c3_y0 + 50),
            "DRAWDOWN LIMIT",
            fill=TEXT_MUTED_40,
            font=font_label,
        )
        dd_str = f"{drawdown_limit_pct * 100:.2f}%"
        draw.text(
            (SAFE_LEFT + 40, c3_y0 + 115), dd_str, fill=PROFIT_COLOR, font=font_val
        )

    if isinstance(filepath_or_t, str):
        # Merge with a static background gradient for mockup image saving
        bg = create_aurora_gradient(WIDTH, HEIGHT, 0.0)
        bg.paste(img, (0, 0), img)
        bg.save(filepath_or_t)
    else:
        return img


def render_scene5_frame(caption: str, filepath_or_t: Any):
    """Renders the Outro slide (Scene 5) centered logo and CTA."""
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Centered logo
    draw_outro_logo(draw, WIDTH // 2, HEIGHT // 2 - 150)

    # Outro Caption / CTA
    font_copyright = get_pil_font(FONT_INTER, 30, bold=False)
    font_link = get_pil_font(FONT_INTER, 36, bold=True)

    copyright_text = "(c) autonomous asset management agents, 2026"
    link_text = "https://autonomous-trading.de"

    # Position copyright
    cy_copyright = HEIGHT // 2 + 80
    c_bbox = draw.textbbox((0, 0), copyright_text, font=font_copyright)
    cw = c_bbox[2] - c_bbox[0]
    cx = (WIDTH - cw) // 2
    draw.text(
        (cx, cy_copyright), copyright_text, fill=TEXT_MUTED_40, font=font_copyright
    )

    # Position website link
    cy_link = cy_copyright + 60
    l_bbox = draw.textbbox((0, 0), link_text, font=font_link)
    lw = l_bbox[2] - l_bbox[0]
    lx = (WIDTH - lw) // 2
    draw.text((lx, cy_link), link_text, fill="#ffffff", font=font_link)

    # Small Disclaimer at bottom
    font_disc = get_pil_font(FONT_INTER, 24, bold=False)
    disc_text = "Documentation of AI trading. No investment advice."
    d_bbox = draw.textbbox((0, 0), disc_text, font=font_disc)
    dx = (WIDTH - (d_bbox[2] - d_bbox[0])) // 2
    draw.text((dx, HEIGHT - 120), disc_text, fill="#505055", font=font_disc)

    if isinstance(filepath_or_t, str):
        img.save(filepath_or_t)


def render_teaser_typing_frames(out_dir: pathlib.Path) -> List[str]:
    """Renders the typing animation frames for the 1.2 second typed brand sequence."""
    word = "autonomous"
    frames = []
    font = get_pil_font(FONT_INTER, 90, bold=True)

    # 12 frames at 10 fps = 1.2s total typing
    for i in range(12):
        img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Calculate typing letters
        letters_count = min(i + 1, len(word))
        typed_part = word[:letters_count]

        left_x = 120
        y_center = 1440

        # Draw typed part
        draw.text(
            (left_x, y_center), typed_part, fill="#ffffff", font=font, anchor="lm"
        )

        # Draw blinking/stable green caret next to the typed part
        typed_bbox = draw.textbbox(
            (left_x, y_center), typed_part, font=font, anchor="lm"
        )
        caret_x = typed_bbox[2] + 8
        draw.text((caret_x, y_center), "_", fill="#00c27a", font=font, anchor="lm")

        frame_path = out_dir / f"teaser_type_{i:02d}.png"
        img.save(frame_path)
        frames.append(str(frame_path))

    return frames


# --- VOICE SYNTHESIS (EDGE-TTS) ---


async def generate_speech_audio(text: str, filepath: str):
    """No-op for no spoken voiceover."""
    pass

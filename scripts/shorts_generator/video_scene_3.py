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
    TEXT_COLOR,
    TEXT_MUTED_40,
    WIDTH,
    create_aurora_gradient,
    draw_header_logo,
    get_pil_font,
)
from scripts.shorts_generator.video_utils import fmt_eur


def render_scene3_frame(data: Dict[str, Any], caption: str, filepath_or_t: Any):
    """Renders the Trades & Reasons slide (Scene 3) with optimized proportions and centering."""
    # Create transparent overlay canvas for cards and text
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw_header_logo(draw)

    # Layout configuration depending on the number of trades
    trades = data.get("trades", [])
    num_trades = len(trades)

    # Fonts
    font_pill = get_pil_font(FONT_MONO, 28, bold=True)
    font_sym = get_pil_font(FONT_MONO, 46, bold=True)
    font_price = get_pil_font(FONT_MONO, 46, bold=True)
    font_qty = get_pil_font(FONT_MONO, 26, bold=True)
    font_card_text = get_pil_font(FONT_INTER, 32, bold=False)

    if num_trades == 0:
        # Title (centered vertically for 1 card)
        font_title = get_pil_font(FONT_INTER, 60, bold=True)
        draw.text((SAFE_LEFT, 320), "Executed Trades", fill="#ffffff", font=font_title)

        card_y = 480
        card_height = 560
        draw.rounded_rectangle(
            [SAFE_LEFT, card_y, WIDTH - SAFE_RIGHT, card_y + card_height],
            radius=CARD_RADIUS,
            fill=CARD_BG_COLOR,
            outline=CARD_BORDER_COLOR,
            width=2,
        )
        font_no_trades = get_pil_font(FONT_INTER, 46, bold=True)
        draw.text(
            (SAFE_LEFT + 40, card_y + 60),
            "No Trades Today",
            fill=TEXT_MUTED_40,
            font=font_no_trades,
        )

        # Word wrap for no trades reason
        no_trade_text = "The AI models analyzed the market but did not open any new positions to protect capital."
        words = no_trade_text.split(" ")
        lines = []
        cur_line = ""
        for w in words:
            test_line = cur_line + (" " if cur_line else "") + w
            w_bbox = draw.textbbox((0, 0), test_line, font=font_card_text)
            if w_bbox[2] - w_bbox[0] < (CONTENT_WIDTH - 80):
                cur_line = test_line
            else:
                lines.append(cur_line)
                cur_line = w
        if cur_line:
            lines.append(cur_line)

        ny = card_y + 150
        for l in lines[:6]:
            draw.text((SAFE_LEFT + 40, ny), l, fill=TEXT_COLOR, font=font_card_text)
            ny += 48

    elif num_trades == 1:
        # Title
        font_title = get_pil_font(FONT_INTER, 60, bold=True)
        draw.text((SAFE_LEFT, 320), "Executed Trades", fill="#ffffff", font=font_title)

        card_y = 480
        card_height = 560
        t = trades[0]

        draw.rounded_rectangle(
            [SAFE_LEFT, card_y, WIDTH - SAFE_RIGHT, card_y + card_height],
            radius=CARD_RADIUS,
            fill=CARD_BG_COLOR,
            outline=CARD_BORDER_COLOR,
            width=2,
        )

        # Pill Badge
        side_text = t["side"].upper()
        badge_bg = "#15301d" if side_text == "BUY" else "#331818"
        badge_border = "#1e5c30" if side_text == "BUY" else "#5c1e1e"
        badge_text_color = PROFIT_COLOR if side_text == "BUY" else LOSS_COLOR

        p_bbox = draw.textbbox((0, 0), side_text, font=font_pill)
        p_w = p_bbox[2] - p_bbox[0] + 24
        p_h = p_bbox[3] - p_bbox[1] + 14

        px0 = SAFE_LEFT + 40
        py0 = card_y + 45
        px1 = px0 + p_w
        py1 = py0 + p_h

        draw.rounded_rectangle(
            [px0, py0, px1, py1], radius=6, fill=badge_bg, outline=badge_border, width=2
        )
        draw.text(
            (px0 + 12, py0 + 7 - p_bbox[1]),
            side_text,
            fill=badge_text_color,
            font=font_pill,
        )

        # Symbol
        cy_header = py0 + p_h // 2
        s_bbox = draw.textbbox((0, 0), t["symbol"], font=font_sym)
        sy = cy_header - (s_bbox[3] - s_bbox[1]) // 2 - s_bbox[1]
        draw.text((px1 + 20, sy), t["symbol"], fill="#ffffff", font=font_sym)

        # Price
        price_text = fmt_eur(t["price"])
        pr_bbox = draw.textbbox((0, 0), price_text, font=font_price)
        pr_w = pr_bbox[2] - pr_bbox[0]
        pr_x = WIDTH - SAFE_RIGHT - 40 - pr_w
        pry = cy_header - (pr_bbox[3] - pr_bbox[1]) // 2 - pr_bbox[1]
        draw.text((pr_x, pry), price_text, fill="#ffffff", font=font_price)

        # Qty
        qty_text = f"Qty: {t['qty']:.0f} Shares"
        draw.text(
            (SAFE_LEFT + 40, card_y + 140), qty_text, fill=TEXT_MUTED_40, font=font_qty
        )

        # Total sum (Invested / Realized)
        total_label = "Invested" if side_text == "BUY" else "Realized"
        total_val = t["qty"] * t["price"]
        p_pct = t.get("pnl_pct", 0.0)
        p_sign = "+" if p_pct >= 0 else "−"
        total_text = f"{total_label}: {fmt_eur(total_val)} ({p_sign}{abs(p_pct):.2f}%)"
        t_bbox = draw.textbbox((0, 0), total_text, font=font_qty)
        t_w = t_bbox[2] - t_bbox[0]
        t_x = WIDTH - SAFE_RIGHT - 40 - t_w
        draw.text((t_x, card_y + 140), total_text, fill="#ffffff", font=font_qty)

        # Reasoning word wrap
        words = t["reason"].split(" ")
        lines_reason = []
        cur_line = ""
        for w in words:
            test_line = cur_line + (" " if cur_line else "") + w
            w_bbox = draw.textbbox((0, 0), test_line, font=font_card_text)
            if w_bbox[2] - w_bbox[0] < (CONTENT_WIDTH - 80):
                cur_line = test_line
            else:
                lines_reason.append(cur_line)
                cur_line = w
        if cur_line:
            lines_reason.append(cur_line)

        ry = card_y + 210
        for r_line in lines_reason[:6]:
            draw.text(
                (SAFE_LEFT + 40, ry), r_line, fill=TEXT_COLOR, font=font_card_text
            )
            ry += 48

    else:
        # Title (spaced for 2 cards)
        font_title = get_pil_font(FONT_INTER, 60, bold=True)
        draw.text((SAFE_LEFT, 260), "Executed Trades", fill="#ffffff", font=font_title)

        card_y = 380
        for t in trades[:2]:
            card_height = 460
            draw.rounded_rectangle(
                [SAFE_LEFT, card_y, WIDTH - SAFE_RIGHT, card_y + card_height],
                radius=CARD_RADIUS,
                fill=CARD_BG_COLOR,
                outline=CARD_BORDER_COLOR,
                width=2,
            )

            # Pill Badge
            side_text = t["side"].upper()
            badge_bg = "#15301d" if side_text == "BUY" else "#331818"
            badge_border = "#1e5c30" if side_text == "BUY" else "#5c1e1e"
            badge_text_color = PROFIT_COLOR if side_text == "BUY" else LOSS_COLOR

            p_bbox = draw.textbbox((0, 0), side_text, font=font_pill)
            p_w = p_bbox[2] - p_bbox[0] + 24
            p_h = p_bbox[3] - p_bbox[1] + 14

            px0 = SAFE_LEFT + 40
            py0 = card_y + 40
            px1 = px0 + p_w
            py1 = py0 + p_h

            draw.rounded_rectangle(
                [px0, py0, px1, py1],
                radius=6,
                fill=badge_bg,
                outline=badge_border,
                width=2,
            )
            draw.text(
                (px0 + 12, py0 + 7 - p_bbox[1]),
                side_text,
                fill=badge_text_color,
                font=font_pill,
            )

            # Symbol
            cy_header = py0 + p_h // 2
            s_bbox = draw.textbbox((0, 0), t["symbol"], font=font_sym)
            sy = cy_header - (s_bbox[3] - s_bbox[1]) // 2 - s_bbox[1]
            draw.text((px1 + 20, sy), t["symbol"], fill="#ffffff", font=font_sym)

            # Price
            price_text = fmt_eur(t["price"])
            pr_bbox = draw.textbbox((0, 0), price_text, font=font_price)
            pr_w = pr_bbox[2] - pr_bbox[0]
            pr_x = WIDTH - SAFE_RIGHT - 40 - pr_w
            pry = cy_header - (pr_bbox[3] - pr_bbox[1]) // 2 - pr_bbox[1]
            draw.text((pr_x, pry), price_text, fill="#ffffff", font=font_price)

            # Qty
            qty_text = f"Qty: {t['qty']:.0f} Shares"
            draw.text(
                (SAFE_LEFT + 40, card_y + 130),
                qty_text,
                fill=TEXT_MUTED_40,
                font=font_qty,
            )

            # Total sum (Invested / Realized)
            total_label = "Invested" if side_text == "BUY" else "Realized"
            total_val = t["qty"] * t["price"]
            p_pct = t.get("pnl_pct", 0.0)
            p_sign = "+" if p_pct >= 0 else "−"
            total_text = (
                f"{total_label}: {fmt_eur(total_val)} ({p_sign}{abs(p_pct):.2f}%)"
            )
            t_bbox = draw.textbbox((0, 0), total_text, font=font_qty)
            t_w = t_bbox[2] - t_bbox[0]
            t_x = WIDTH - SAFE_RIGHT - 40 - t_w
            draw.text((t_x, card_y + 130), total_text, fill="#ffffff", font=font_qty)

            # Reasoning word wrap
            words = t["reason"].split(" ")
            lines_reason = []
            cur_line = ""
            for w in words:
                test_line = cur_line + (" " if cur_line else "") + w
                w_bbox = draw.textbbox((0, 0), test_line, font=font_card_text)
                if w_bbox[2] - w_bbox[0] < (CONTENT_WIDTH - 80):
                    cur_line = test_line
                else:
                    lines_reason.append(cur_line)
                    cur_line = w
            if cur_line:
                lines_reason.append(cur_line)

            ry = card_y + 195
            for r_line in lines_reason[:5]:
                draw.text(
                    (SAFE_LEFT + 40, ry), r_line, fill=TEXT_COLOR, font=font_card_text
                )
                ry += 46

            card_y += card_height + 60

    if isinstance(filepath_or_t, str):
        # Merge with a static background gradient for mockup image saving
        bg = create_aurora_gradient(WIDTH, HEIGHT, 0.0)
        bg.paste(img, (0, 0), img)
        bg.save(filepath_or_t)
    else:
        return img

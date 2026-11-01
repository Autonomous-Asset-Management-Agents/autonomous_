import logging
from typing import Any, Dict, List

from PIL import Image, ImageDraw

from scripts.shorts_generator.video_branding import (
    FONT_INTER,
    HEIGHT,
    LOSS_COLOR,
    PROFIT_COLOR,
    WIDTH,
    create_aurora_gradient,
    draw_app_logo_icon,
    draw_header_logo,
    get_pil_font,
)
from scripts.shorts_generator.video_scene_2 import render_scene2_frame
from scripts.shorts_generator.video_scene_3 import render_scene3_frame
from scripts.shorts_generator.video_scenes_rest import render_scene4_frame
from scripts.shorts_generator.video_utils import apply_alpha_to_image

logger = logging.getLogger(__name__)


def compile_final_video(
    data: Dict[str, Any],
    script: Dict[str, str],
    chart_path: str,
    teaser_img: str,
    typing_frames: List[str],
    black_img: str,
    scene5_img: str,
    jingle_path: str,
    output_path: str,
):
    """Compiles all static assets, generated charts, and TTS audio into the final YouTube Shorts MP4."""
    import numpy as np
    from moviepy import AudioFileClip, CompositeVideoClip, VideoClip

    # Base background (aurora, fully opaque, plays through the whole 25 seconds)
    def make_bg_frame(t):
        img = create_aurora_gradient(WIDTH, HEIGHT, t)
        return np.array(img)

    bg_clip = VideoClip(make_bg_frame, duration=25.0)

    # --- SCENE 1: Brand Teaser (4.5s total visual duration) ---
    typing_pil_frames = [Image.open(f).convert("RGBA") for f in typing_frames]

    def make_s1_frame(t):
        # Direct Eye Catcher starts immediately at t=0.0
        img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Draw the giant watermark logo (Variant 3B-1) in the background
        wm_size = 3200
        wm_x = 800 - (wm_size // 2)
        wm_y = 960 - (wm_size // 2)

        # Fade out the giant logo before the daily recap (Scene 2) starts at 3.5s.
        wm_factor = 1.0 if t <= 2.5 else max(0.0, 3.5 - t)

        if wm_factor > 0:
            wm_img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
            wm_draw = ImageDraw.Draw(wm_img)
            draw_app_logo_icon(wm_draw, wm_x, wm_y, wm_size)
            wm_img = apply_alpha_to_image(wm_img, 0.05 * wm_factor)
            img.alpha_composite(wm_img)

        # Header logo
        draw_header_logo(draw)

        # --- TOP GROUP (Left-Aligned) ---
        left_x = 120
        y_number = 864

        # 1. YTD Value (Huge, Inter font for tight aesthetic kerning)
        ytd_pct = data.get("ytd_pct", 5.00)
        ytd_sign = "+" if ytd_pct >= 0 else "−"
        ytd_text = f"{ytd_sign}{abs(ytd_pct):.1f}%"
        ytd_color = PROFIT_COLOR if ytd_pct >= 0 else LOSS_COLOR
        font_val = get_pil_font(FONT_INTER, 260, bold=True)
        draw.text(
            (left_x, y_number), ytd_text, fill=ytd_color, font=font_val, anchor="ls"
        )

        # 2. P&L Since Start
        font_sub = get_pil_font(FONT_INTER, 40, bold=False)
        draw.text(
            (left_x + 10, y_number + 20),
            "P&L Since Start (01.02.2026)",
            fill="#aaaaaa",
            font=font_sub,
            anchor="lt",
        )

        # --- BOTTOM GROUP ---
        # 3. "autonomous_" typing sequence
        idx = min(int(t * 8), len(typing_pil_frames) - 1)
        type_frame = typing_pil_frames[idx].copy()
        img.alpha_composite(type_frame)

        # 4. "trading" fading in below
        factor_trading = max(0.0, min(1.0, (t - 1.2) / 0.8))  # starts fading in at 1.2s
        if factor_trading > 0:
            trade_img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
            trade_draw = ImageDraw.Draw(trade_img)
            font_trading = get_pil_font(FONT_INTER, 55, bold=False)
            trade_draw.text(
                (left_x, 1440 + 50),
                "trading",
                fill="#aaaaaa",
                font=font_trading,
                anchor="lm",
            )
            trade_img = apply_alpha_to_image(trade_img, factor_trading)
            img.alpha_composite(trade_img)

        # Fade out into Scene 2 (smooth transition)
        # Fading out from 2.5s to 4.0s for a very gentle disappearance
        factor = 1.0 if t <= 2.5 else max(0.0, (4.0 - t) / 1.5)

        img = apply_alpha_to_image(img, factor)
        return np.array(img)

    s1_clip = VideoClip(make_s1_frame, duration=4.5).with_start(0.0)

    # --- SCENE 2: Portfolio Status ---
    def make_s2_frame(t):
        # Fade in from 0-1.5s, fade out from 4.0-5.0s (fully transparent by 8.5s)
        factor = (t / 1.5) if t < 1.5 else (max(0.0, 5.0 - t) if t > 4.0 else 1.0)
        img = render_scene2_frame(data, script["scene2_caption"], chart_path, 3.5 + t)
        img = apply_alpha_to_image(img, factor)
        return np.array(img)

    s2_clip = VideoClip(make_s2_frame, duration=6.0).with_start(3.5)

    # --- SCENE 3: Trades Spotlight ---
    def make_s3_frame(t):
        # Fade in from 0-1.5s, fade out from 4.0-5.0s (fully transparent by 13.5s)
        factor = (t / 1.5) if t < 1.5 else (max(0.0, 5.0 - t) if t > 4.0 else 1.0)
        img = render_scene3_frame(data, script["scene3_caption"], 8.5 + t)
        img = apply_alpha_to_image(img, factor)
        return np.array(img)

    s3_clip = VideoClip(make_s3_frame, duration=6.0).with_start(8.5)

    # --- SCENE 4: Market Regime ---
    def make_s4_frame(t):
        # Fade in from 0-1.5s, fade out from 4.0-5.0s (fully transparent by 18.5s)
        factor = (t / 1.5) if t < 1.5 else (max(0.0, 5.0 - t) if t > 4.0 else 1.0)
        img = render_scene4_frame(data, script["scene4_caption"], 13.5 + t)
        img = apply_alpha_to_image(img, factor)
        return np.array(img)

    s4_clip = VideoClip(make_s4_frame, duration=6.0).with_start(13.5)

    # --- SCENE 5: Outro ---
    s5_pil = Image.open(scene5_img).convert("RGBA")

    def make_s5_frame(t):
        # Fade in from 0-1.5s
        factor = (t / 1.5) if t < 1.5 else (6.5 - t if t > 5.0 else 1.0)
        img = s5_pil.copy()
        img = apply_alpha_to_image(img, factor)
        return np.array(img)

    s5_clip = VideoClip(make_s5_frame, duration=6.5).with_start(18.5)

    # Compose all parts sequentially with bg_clip at the bottom (index 0) and transparent overlays on top
    final_clip = CompositeVideoClip(
        [bg_clip, s1_clip, s2_clip, s3_clip, s4_clip, s5_clip]
    ).with_duration(25.0)

    # Load background music and mix it
    bg_music = AudioFileClip(jingle_path)
    final_clip = final_clip.with_audio(bg_music)

    # Write to target file
    final_clip.write_videofile(
        output_path, fps=24, codec="libx264", audio_codec="aac", preset="medium"
    )

    # Close resources
    final_clip.close()
    bg_music.close()
    bg_clip.close()
    s1_clip.close()
    s2_clip.close()
    s3_clip.close()
    s4_clip.close()
    s5_clip.close()
    logger.info(
        "Successfully compiled daily YouTube Shorts recap video to %s", output_path
    )


# --- ENTRY POINT & MAIN RUNNER ---

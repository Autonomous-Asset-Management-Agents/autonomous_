import logging
import math
import os
from typing import List, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from scripts.shorts_generator.video_branding import (  # noqa: E402
    CARD_BG_COLOR,
    CHART_GRID_COLOR,
    CHART_LINE_COLOR,
    FONT_MONO,
    get_font_path,
)

logger = logging.getLogger(__name__)


def generate_portfolio_chart(points: List[Tuple[str, float]], filepath: str):
    """Generates the performance curve line chart (signature green on CARD_BG_COLOR, smooth spline, spines removed)."""
    fig, ax = plt.subplots(figsize=(9.0, 6.0), facecolor=CARD_BG_COLOR)
    ax.set_facecolor(CARD_BG_COLOR)

    if len(points) < 2:
        # Standard fallback line if no chart points are available
        x = list(range(10))
        y = [104000.0 + (i * 300) + (math.sin(i) * 500) for i in range(10)]
    else:
        x = list(range(len(points)))
        y = [p[1] for p in points]

    # Smooth the curve using cubic spline interpolation
    import numpy as np

    x_arr = np.array(x)
    y_arr = np.array(y)
    if len(x_arr) >= 2:
        x_smooth = np.linspace(x_arr.min(), x_arr.max(), 300)
        from scipy.interpolate import make_interp_spline

        k = min(3, len(x_arr) - 1)
        if k > 0:
            spl = make_interp_spline(x_arr, y_arr, k=k)
            y_smooth = spl(x_smooth)
        else:
            x_smooth = x_arr
            y_smooth = y_arr
    else:
        x_smooth = x_arr
        y_smooth = y_arr

    # Plot line with rounded caps/joins
    ax.plot(
        x_smooth,
        y_smooth,
        color=CHART_LINE_COLOR,
        linewidth=5,
        solid_capstyle="round",
        solid_joinstyle="round",
    )

    # Fill under curve (gradient style following smooth curve)
    min_y = min(y)
    max_y = max(y)
    p_range = max_y - min_y if max_y > min_y else 1000.0
    ax.fill_between(
        x_smooth, y_smooth, min_y - p_range * 0.1, color=CHART_LINE_COLOR, alpha=0.15
    )

    # Style grid
    ax.grid(True, which="both", color=CHART_GRID_COLOR, linestyle="--", linewidth=1.5)

    # Remove borders
    for spine in ["top", "right", "left", "bottom"]:
        ax.spines[spine].set_visible(False)

    # Format ticks
    ax.tick_params(colors="#8a8a8f", labelsize=18, length=0)

    # Apply JetBrains Mono font to tick labels to match the app
    import matplotlib.font_manager as fm

    font_path_mono = get_font_path(FONT_MONO, bold=False)
    if font_path_mono and os.path.exists(font_path_mono):
        try:
            fm.fontManager.addfont(font_path_mono)
            mono_font_name = fm.FontProperties(fname=font_path_mono).get_name()
            # Set the font family globally in Matplotlib for this session
            matplotlib.rcParams["font.family"] = mono_font_name
            # Force apply directly to tick labels
            for label in ax.get_xticklabels():
                label.set_fontname(mono_font_name)
            for label in ax.get_yticklabels():
                label.set_fontname(mono_font_name)
        except Exception as e:
            logger.warning(
                "Could not set Matplotlib tick font to JetBrains Mono: %s", e
            )

    # Adjust padding to fill figure
    plt.tight_layout()
    plt.savefig(filepath, dpi=120, facecolor=CARD_BG_COLOR)
    plt.close()
    logger.info("Saved premium portfolio curve chart to %s", filepath)

# flake8: noqa: E402,E501
# ai_trading_bot/scripts/generate_daily_shorts.py
# Generates daily YouTube Shorts video recap based on portfolio snapshots, trades, and decisions.
# Implements TDD and matches the storyboard and brand style guide specifications.

import argparse
import asyncio
import json
import logging
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from PIL import Image

# Ensure ai_trading_bot is on path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

from scripts.shorts_generator.video_audio import synthesize_jingle
from scripts.shorts_generator.video_branding import BG_COLOR, HEIGHT, WIDTH
from scripts.shorts_generator.video_charts import generate_portfolio_chart
from scripts.shorts_generator.video_compiler import compile_final_video
from scripts.shorts_generator.video_scenes_rest import (
    render_scene5_frame,
    render_teaser_typing_frames,
)
from scripts.shorts_generator.video_utils import generate_recaps_script


def load_snapshot_data(snapshot_path: str) -> Dict[str, Any]:
    """Loads a PublicSnapshot JSON file and maps it to the internal format expected by the video renderer."""
    import json

    with open(snapshot_path, "r", encoding="utf-8") as f:
        snap = json.load(f)

    date_str = snap.get("generated_at", "").split("T")[0]
    if not date_str:
        from datetime import datetime

        date_str = datetime.now().strftime("%Y-%m-%d")

    return {
        "date": date_str,
        "total_equity": snap.get("equity", 0.0),
        "cash": snap.get("cash", 0.0),
        "pnl_abs": snap.get("day_pl_abs", 0.0),
        "pnl_pct": snap.get("day_pl_pct", 0.0),
        "ytd_pct": snap.get("ytd_pct", 0.0),
        "market_regime": snap.get("market_regime", "UNKNOWN"),
        "vix": snap.get("vix", 0.0),
        "chart_points": [
            (pt["date"], pt["equity"]) for pt in snap.get("equity_curve", [])
        ],
        "trades": [
            {
                "symbol": d["symbol"],
                "side": d["action"].upper(),
                "qty": 0,  # not in decision, but we don't display it anyway in standard shorts
                "price": 0.0,
                "pnl_pct": 0.0,
                "reason": d.get("summary", ""),
            }
            for d in snap.get("decisions", [])
        ],
    }


# --- RENDER VISUAL SLIDES (PILLOW & MATPLOTLIB) ---


async def main_async(args: argparse.Namespace) -> Tuple[int, Optional[str]]:
    # Resolve default relative asset paths against the project root at run time
    # (not as an import-time side effect).
    os.chdir(PROJECT_ROOT)

    # 1. Fetch data
    try:
        data = load_snapshot_data(args.snapshot_path)
        # Add date if missing from snapshot or arguments
        if not data.get("date") and args.date:
            data["date"] = args.date
    except Exception as e:
        logger.error("Failed to load snapshot data: %s", e)
        return 1, None

    # 2. Get LLM Script
    script = await generate_recaps_script(data)

    if args.dry_run:
        logger.info("[Dry Run] Script content: %s", json.dumps(script, indent=2))
        return 0, None

    # Create temp rendering folder
    temp_dir = pathlib.Path(tempfile.gettempdir()) / "aaagents_shorts_render"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # File paths
        chart_path = str(temp_dir / "portfolio_chart.png")
        scene5_img = str(temp_dir / "scene5_outro.png")

        jingle_path = str(temp_dir / "teaser_jingle.wav")

        # Output video file
        date_str = data.get("date", args.date)
        out_video_name = f"recap_{date_str}_snapshot.mp4"
        output_video_path = str(
            pathlib.Path(args.output_dir or tempfile.gettempdir()) / out_video_name
        )

        # Generate jingle / background music track (25.0 seconds duration)
        synthesize_jingle(jingle_path, duration=25.0)

        # 4. Generate visual slides
        logger.info("Rendering frames and charts...")
        generate_portfolio_chart(data["chart_points"], chart_path)

        # Check teaser broker B&W image presence
        teaser_img_path = "assets/brand/broker.png"
        if not os.path.exists(teaser_img_path):
            # Create a fallback dark image if broker image is missing
            fallback_teaser = Image.new("RGB", (WIDTH, HEIGHT), BG_COLOR)
            # Completely clean dark fallback image (no text, as requested)
            fallback_teaser.save(str(temp_dir / "teaser_fallback.png"))
            teaser_img_path = str(temp_dir / "teaser_fallback.png")

        typing_frames = render_teaser_typing_frames(temp_dir)

        # Generate pure black image for teaser hold/fade
        black_img_path = str(temp_dir / "scene1_black.png")
        Image.new("RGB", (WIDTH, HEIGHT), BG_COLOR).save(black_img_path)

        render_scene5_frame(script["scene5_caption"], scene5_img)

        # 5. Compile Video
        logger.info("Compiling assets into MP4 recap video...")
        compile_final_video(
            data=data,
            script=script,
            chart_path=chart_path,
            teaser_img=teaser_img_path,
            typing_frames=typing_frames,
            black_img=black_img_path,
            scene5_img=scene5_img,
            jingle_path=jingle_path,
            output_path=output_video_path,
        )

        print(f"SUCCESS: Video generated at {output_video_path}")
        return 0, output_video_path

    except Exception as e:
        logger.error("Failed during video rendering/compilation: %s", e)
        return 1, None

    finally:
        # Cleanup stateless container space
        if not args.dry_run and temp_dir.exists():
            logger.info("Cleaning up temporary render files...")
            for f in temp_dir.glob("*"):
                try:
                    f.unlink()
                except Exception:
                    pass
            try:
                temp_dir.rmdir()
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Daily Recap Shorts Generator (Snapshot Consumer)"
    )
    parser.add_argument(
        "--snapshot-path",
        required=True,
        help="Path to the standard public snapshot.json",
    )
    parser.add_argument(
        "--date",
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        help="Optional override for target date",
    )
    parser.add_argument(
        "--output-dir", default=None, help="Directory to save final video"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print script summary without rendering video",
    )

    args = parser.parse_args()
    return asyncio.run(main_async(args))[0]


if __name__ == "__main__":
    sys.exit(main())

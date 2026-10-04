"""Shared helpers for the Daily Shorts renderer (R6-3a, #1675).

Re-authored from the stacked branch with explicit imports (no `from ... import *`), no
unused/heavy module-level deps, and lazy imports for the LLM seam and the branding
constant — so the module imports cleanly (import-smoke gate, unit tests) without pulling
core.llm or matplotlib.
"""

import json
import logging
from typing import Any, Dict

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


def fmt_eur(val: float) -> str:
    """Format a value as €105,000.00 (English number style)."""
    return f"€{val:,.2f}"


def fmt_pnl_eur(val: float) -> str:
    """Format a PnL as +€800.00 or −€800.00."""
    sign = "+" if val >= 0 else "−"
    return f"{sign}€{abs(val):,.2f}"


# --- LLM SCRIPT GENERATION ---


async def generate_recaps_script(data: Dict[str, Any]) -> Dict[str, str]:
    """Assemble the video voiceovers + captions via the sanctioned get_llm_provider() seam.

    Falls back to a deterministic template if the LLM is unavailable or returns an
    incomplete payload.
    """
    from core.llm.provider import get_llm_provider  # lazy: keep module import light

    llm = get_llm_provider()
    pnl_sign = "+" if data["pnl_pct"] >= 0 else "−"

    prompt = f"""You are a professional financial content creator and video editor.
Generate a structured script for a 60-second vertical video (YouTube Short) summarizing today's trading results.

Today's Trading Data (in EUR):
- Date: {data['date']}
- Account Equity: {fmt_eur(data['total_equity'])}
- Daily PnL: {fmt_pnl_eur(data['pnl_abs'])} ({pnl_sign}{data['pnl_pct']:.2f}%)
- Market Regime: {data['market_regime']}
- Volatility (VIX): {data['vix']}
- Executed Trades: {json.dumps(data['trades'], indent=2)}

You MUST output exactly a JSON object containing the voiceovers and captions for the scenes in ENGLISH.
Format the output EXACTLY as follows (do not output any markdown code blocks, text outside JSON, or HTML):
{{
  "scene2_voiceover": "Hey! Here is your daily AI portfolio update. Our total equity stands at [Value] today, up [Percent]!",
  "scene2_caption": "Account Update:\\n[Value]\\n([Percent])",
  "scene3_voiceover": "The AI executed [Count] trades today. We bought [Symbol] at [Price] due to [Reason] and sold [Symbol] due to [Reason].",
  "scene3_caption": "BUY [Symbol] @ [Price]\\nSELL [Symbol] @ [Price]",
  "scene4_voiceover": "The market regime remains [Regime] with a VIX level of [VIX]. Risk limits are actively monitored.",
  "scene4_caption": "Market Regime: [Regime]\\nVIX: [VIX]",
  "scene5_voiceover": "Subscribe to the channel to track the AI's daily trading. See you tomorrow!",
  "scene5_caption": "(c) autonomous asset management agents, 2026\\nhttps://autonomous-trading.de"
}}

Rules:
1. Voiceovers must be natural, engaging, and in English.
2. Timing constraints: Scene 2 voiceover (max 40 words), Scene 3 voiceover (max 100 words), Scene 4 voiceover (max 30 words).
3. Do not mention gold or any forbidden branding. The logo is strictly "autonomous_".
4. All currency values MUST use the Euro symbol (€) and English number formatting (e.g., €105,000.00 or +€800.00).
"""

    if not llm:
        logger.warning("LLM Provider not available. Using fallback script template.")
        return get_fallback_script(data)

    try:
        response_text = await llm.generate_content_async(prompt, max_output_tokens=1024)

        clean_text = response_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
        clean_text = clean_text.strip()

        script = json.loads(clean_text)
        required_keys = [
            "scene2_voiceover",
            "scene2_caption",
            "scene3_voiceover",
            "scene3_caption",
            "scene4_voiceover",
            "scene4_caption",
            "scene5_voiceover",
            "scene5_caption",
        ]
        if all(k in script for k in required_keys):
            return script
        logger.warning("LLM response missing keys. Falling back.")
        return get_fallback_script(data)
    except Exception as e:
        logger.warning("Failed to generate script via LLM: %s. Using fallback.", e)
        return get_fallback_script(data)


def get_fallback_script(data: Dict[str, Any]) -> Dict[str, str]:
    """Deterministic script template used when the LLM is unavailable."""
    pnl_sign = "+" if data["pnl_pct"] >= 0 else "−"
    pnl_str = f"{pnl_sign}{abs(data['pnl_pct']):.2f}%"
    equity_str = fmt_eur(data["total_equity"])
    pnl_abs_str = fmt_pnl_eur(data["pnl_abs"])

    if data["trades"]:
        t_strings = []
        c_strings = []
        for t in data["trades"][:2]:  # Cap at 2 for time constraints
            side_en = "Buy" if t["side"] == "BUY" else "Sell"
            t_strings.append(
                f"{side_en} of {t['symbol']} at {fmt_eur(t['price'])} due to: {t['reason']}"
            )
            c_strings.append(f"{t['side']} {t['symbol']} @ {fmt_eur(t['price'])}")
        trades_desc = " " + " Also: ".join(t_strings)
        trades_caption = "\n".join(c_strings)
    else:
        trades_desc = " No new positions were opened or closed today, the portfolio remains stable."
        trades_caption = "No Trades\nPortfolio Stable"

    return {
        "scene2_voiceover": f"Hey! Here is your daily AI portfolio update. Our total equity stands at {equity_str} today, a PnL of {pnl_abs_str} or {pnl_str}!",
        "scene2_caption": f"Account Update:\n{equity_str}\n({pnl_str})",
        "scene3_voiceover": f"The AI made the following decisions today.{trades_desc}",
        "scene3_caption": trades_caption,
        "scene4_voiceover": f"The market regime remains {data['market_regime']} with a volatility VIX of {data['vix']:.1f}.",
        "scene4_caption": f"Market Regime: {data['market_regime']}\nVIX: {data['vix']:.1f}",
        "scene5_voiceover": "That was the daily update from autonomous.",
        "scene5_caption": "(c) autonomous asset management agents, 2026\nhttps://autonomous-trading.de",
    }


# --- PIL RENDERING HELPERS ---


def draw_caption_box(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, cy: int
) -> None:
    """Draw a semi-transparent capsule behind each caption line for readability."""
    from scripts.shorts_generator.video_branding import WIDTH  # lazy: avoid import cost

    lines = text.split("\n")
    for line in reversed(lines):
        if not line.strip():
            continue
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        pad_x, pad_y = 30, 16
        cx0 = (WIDTH - tw) // 2 - pad_x
        cy0 = cy - pad_y
        cx1 = cx0 + tw + 2 * pad_x
        cy1 = cy0 + th + 2 * pad_y

        draw.rounded_rectangle([cx0, cy0, cx1, cy1], radius=16, fill="#121214")

        lx = (WIDTH - tw) // 2
        draw.text((lx, cy0 + pad_y), line, fill="#ffffff", font=font)
        cy -= th + 2 * pad_y + 20


def apply_alpha_to_image(img: Image.Image, alpha_factor: float) -> Image.Image:
    """Multiply the alpha channel of an RGBA image by ``alpha_factor`` (0.0–1.0)."""
    if alpha_factor >= 1.0:
        return img
    if alpha_factor <= 0.0:
        return Image.new("RGBA", img.size, (0, 0, 0, 0))
    r, g, b, a = img.split()
    a = a.point(lambda p: int(p * alpha_factor))
    return Image.merge("RGBA", (r, g, b, a))

import re

p = "tests/unit/test_config_paywall_parity.py"
t = open(p, "rb").read()
t = re.sub(
    b"cfg.LEMONSQUEEZY_PRICE_DISPLAY == .*",
    b'cfg.LEMONSQUEEZY_PRICE_DISPLAY == "9,99 \xe2\x82\xac"',
    t,
)
open(p, "wb").write(t)

# core/broker_authorization.py
# #5135 (GTM-1): the golden Alpaca authorization text, shown before any Alpaca key is validated or
# stored. Alpaca's template with `[Name]` -> `autonomous_`; three paragraphs (title, paragraph 1,
# paragraph 2) separated by one blank line. DENY / ALLOW are buttons, not part of the text.
#
# Mirrored byte-identically in src/console/desktop/brokerAuthorization.ts. Both sides pin the same
# SHA-256 (tests/unit/test_broker_authorization_seal.py, src/test/brokerAuthorization.test.tsx).
from __future__ import annotations

import hashlib

# ADR #5135: version of Alpaca's authorization template as shown. Every change to the wording
# (including the product name) MUST bump it — an answer to an older version no longer counts.
AUTHORIZATION_VERSION = "1.0.0"

AUTHORIZATION_TEXT = (
    "Authorize autonomous_\n\n"
    "By allowing autonomous_ to access your Alpaca account, you are granting autonomous_ access "
    "to your account information and authorization to place transactions at your direction.\n\n"
    "Alpaca does not warrant or guarantee that autonomous_ will work as advertised or expected. "
    "Before authorizing, learn more about autonomous_."
)

AUTHORIZATION_TEXT_SHA256 = hashlib.sha256(
    AUTHORIZATION_TEXT.encode("utf-8")
).hexdigest()

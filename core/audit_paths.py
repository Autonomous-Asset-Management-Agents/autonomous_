"""Durable audit-log path resolution + one-time legacy migration (#2586).

``compliance_audit.log`` and ``kill_switch_audit.log`` were opened RELATIVE to
the process CWD. Under a packaged desktop install the engine's CWD is the app
bundle (``<install>\\app\\ai_trading_bot``) — which the bootstrap REPLACES on
every upgrade, so both audit trails died with it, while ``audit_chain.jsonl``
(already under USER_DATA_DIR) survived. Regulatory audit trails must not die
with an app update (MiFID II Art. 16(6)/(7) record keeping / WORM narrative).

Resolution mirrors ``core/cloud_logger._resolve_fallback_dir`` (INV-02/INV-29):
``AAA_USER_DATA_DIR`` when set (the desktop launcher always sets it), else the
legacy CWD-relative filename UNCHANGED — cloud/dev behavior stays
byte-identical (BORA).

Both helpers are best-effort and NEVER raise: they run at module-import time of
the compliance / kill-switch modules, and a broken audit path must never block
engine boot (the sinks fall back to a StreamHandler there anyway).
"""

import logging
import os

logger = logging.getLogger(__name__)


def resolve_audit_log_path(filename: str) -> str:
    """Return the durable location for an audit-log ``filename``.

    ``<AAA_USER_DATA_DIR>/<filename>`` when the env var is set (desktop; the
    dir is created so ``logging.FileHandler`` can open the file), else the
    bare ``filename`` (legacy CWD-relative — cloud/dev, byte-identical).
    """
    user_data_dir = os.environ.get("AAA_USER_DATA_DIR", "").strip()
    if not user_data_dir:
        return filename
    try:
        os.makedirs(user_data_dir, exist_ok=True)
    except Exception as exc:  # noqa: BLE001 — boot must never break on this
        logger.warning(
            "audit_paths: could not create USER_DATA_DIR %r (%s) - falling "
            "back to CWD-relative %r (audit log will NOT survive an upgrade)",
            user_data_dir,
            exc,
            filename,
        )
        return filename
    return os.path.join(user_data_dir, filename)


def migrate_legacy_audit_log(filename: str, target_path: str) -> bool:
    """One-time migration of a legacy CWD-relative audit log to ``target_path``.

    If the legacy file exists and the target differs, move it (or append its
    content when the target already has entries — lines carry timestamps, so
    completeness beats ordering) and remove the legacy file. Returns True when
    a migration happened. Best-effort: failures log at WARNING and return
    False — NEVER raise into module import.
    """
    try:
        if os.path.abspath(filename) == os.path.abspath(target_path):
            return False
        if not os.path.isfile(filename):
            return False
        if not os.path.exists(target_path):
            try:
                os.replace(filename, target_path)
                return True
            except OSError:
                # cross-device / transient lock — fall through to append+remove
                pass
        with open(filename, "rb") as src, open(target_path, "ab") as dst:
            dst.write(src.read())
        os.remove(filename)
        return True
    except Exception as exc:  # noqa: BLE001 — boot must never break on this
        logger.warning(
            "audit_paths: legacy audit-log migration %r -> %r failed: %s "
            "(will retry at next engine start)",
            filename,
            target_path,
            exc,
        )
        return False

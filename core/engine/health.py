import shutil
import logging
import os

logger = logging.getLogger(__name__)

def check_startup_preconditions():
    """
    Checks environment preconditions before booting the engine.
    Raises RuntimeError if a critical precondition fails.
    """
    # 1. Disk Space Check
    # Check the data directory or current working directory if /app doesn't exist
    check_dir = "/app" if os.path.exists("/app") else "."
    
    try:
        total, used, free = shutil.disk_usage(check_dir)
        free_gb = free // (2**30)
        
        if free_gb < 1:
            logger.critical(
                f"FAIL-CLOSED: Less than 1GB disk space free on {check_dir} ({free_gb} GB). "
                "Aborting startup to prevent Docker restart loop."
            )
            raise RuntimeError(f"Insufficient disk space on {check_dir}: {free_gb}GB free. 1GB required.")
    except Exception as e:
        if isinstance(e, RuntimeError):
            raise
        logger.warning(f"Could not verify disk space on {check_dir}: {e}")


import os
import re

path = r"core/cloud_logger.py"
with open(path, "r", encoding="utf-8") as f:
    text = f.read()

mixin_code = """class SecretMaskMixin:
    _config_module = None
    _secrets_cache = []
    _last_cache_update = 0.0

    def _get_secrets(self):
        import time
        now = time.time()
        # Update cache every 60 seconds
        if now - self._last_cache_update < 60.0 and self._secrets_cache:
            return self._secrets_cache
            
        if self._config_module is None:
            try:
                import config
                self._config_module = config
            except ImportError:
                return self._secrets_cache
                
        secrets = []
        if self._config_module:
            try:
                state = self._config_module.get_config()
                for key in ["ALPACA_API_KEY", "ALPACA_SECRET_KEY", "GEMINI_API_KEY", "DATABENTO_API_KEY", "POLYGON_API_KEY"]:
                    val = getattr(state, key, None)
                    if val:
                        secret_val = val.get_secret_value() if hasattr(val, "get_secret_value") else str(val)
                        if secret_val and len(secret_val) > 4 and secret_val != "**********":
                            secrets.append(secret_val)
            except Exception:
                pass
        
        if secrets:
            self._secrets_cache = secrets
            self._last_cache_update = now
            
        return self._secrets_cache

    def _mask_string(self, msg: str) -> str:
        secrets = self._get_secrets()
        for secret in secrets:
            if secret in msg:
                msg = msg.replace(secret, "**********")
        return msg

class SecretMaskFormatter(logging.Formatter, SecretMaskMixin):
    def format(self, record):
        original_message = super().format(record)
        return self._mask_string(original_message)

"""

text = re.sub(
    r"class GcpJsonFormatter\(logging\.Formatter\):",
    mixin_code + "class GcpJsonFormatter(logging.Formatter, SecretMaskMixin):",
    text,
)

text = text.replace(
    "return json.dumps(log_entry)",
    "json_str = json.dumps(log_entry)\n        return self._mask_string(json_str)",
)

filter_pattern = r"class SecretMaskFilter\(logging\.Filter\):.*?(?=def setup_logging)"
text = re.sub(filter_pattern, "", text, flags=re.DOTALL)

text = text.replace(
    "handler.setFormatter(logging.Formatter(fmt))",
    "handler.setFormatter(SecretMaskFormatter(fmt))",
)
text = text.replace("    handler.addFilter(SecretMaskFilter())\n", "")

with open(path, "w", encoding="utf-8") as f:
    f.write(text)

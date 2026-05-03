# core/round_table/registry.py
import logging
import importlib.util
import os
import sys
from typing import Dict, Type, List, Callable, Optional
from core.round_table.base_agent import VotingAgent

logger = logging.getLogger(__name__)


class PluginRegistry:
    def __init__(self):
        self._agents: Dict[str, Type[VotingAgent]] = {}

    def register(self, name: str, agent_class: Type[VotingAgent]):
        self._agents[name] = agent_class
        logger.info(f"Registered Agent: {name}")

    def get_active_agents(self) -> List[VotingAgent]:
        # TODO: self._agents is a plain dict without a lock. If load_plugins_from_directory()
        # and get_active_agents() are called concurrently during async boot, there is a race condition.
        # Acceptable for OSS MVP but should be locked in future.
        return [cls() for cls in self._agents.values()]

    def load_plugins_from_directory(self, plugins_dir: str):
        if not os.path.exists(plugins_dir):
            logger.warning(f"Plugin directory {plugins_dir} does not exist.")
            return

        if os.environ.get("ALLOW_UNTRUSTED_PLUGINS", "false").lower() != "true":
            logger.warning(
                "Plugin loading blocked: ALLOW_UNTRUSTED_PLUGINS is not set to true. "
                "Loading plugins via exec_module is insecure without verification."
            )
            return

        for filename in os.listdir(plugins_dir):
            if filename.endswith(".py") and not filename.startswith("__"):
                filepath = os.path.join(plugins_dir, filename)
                module_name = f"plugins.{filename[:-3]}"

                try:
                    spec = importlib.util.spec_from_file_location(module_name, filepath)
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[module_name] = module
                        spec.loader.exec_module(module)
                        logger.info(f"Successfully loaded plugin file: {filename}")
                except Exception as e:
                    logger.error(
                        f"Failed to load plugin {filename}: {e}", exc_info=True
                    )


_global_registry = PluginRegistry()


def register_agent(name: str, registry: Optional[PluginRegistry] = None) -> Callable:
    """Decorator to register a VotingAgent class."""
    reg = registry or _global_registry

    def decorator(cls: Type[VotingAgent]) -> Type[VotingAgent]:
        reg.register(name, cls)
        return cls

    return decorator

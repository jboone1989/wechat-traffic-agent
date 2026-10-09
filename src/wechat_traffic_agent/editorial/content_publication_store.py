"""Compatibility alias. HTML materialization belongs to the website adapter."""
import sys as _sys
from importlib import import_module as _import_module
_sys.modules[__name__] = _import_module("wechat_traffic_agent.website.content_publication_store")

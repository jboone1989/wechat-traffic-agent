"""Compatibility import: implementation lives in wechat_traffic_agent.website.website_experiment_evaluation."""
from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module('wechat_traffic_agent.website.website_experiment_evaluation')

"""Feature package paths preserve import identity while Ferro migrates."""
from importlib import import_module
from pathlib import Path

import pytest

ROOT = "wechat_traffic_agent"
FEATURE_MODULES = {
    "editorial": (
        "content_artifact", "article_repository",
        "content_admission", "draft_inbox",
    ),
    "website": (
        "automatic_website", "web_editorial", "website_experiment", "content_publication_store",
        "site_audit",
    ),
    "platforms.browser": (
        "browser_runtime", "browser_session_stream", "account_registry",
    ),
    "platforms.x": (
        "x_jev_transaction", "x_social", "x_growth_mission", "x_outcomes",
    ),
}


@pytest.mark.parametrize(
    "package,module",
    [(package, module) for package, modules in FEATURE_MODULES.items() for module in modules],
)
def test_old_import_is_the_same_module_as_feature_implementation(package, module):
    legacy = import_module(f"{ROOT}.{module}")
    canonical = import_module(f"{ROOT}.{package}.{module}")
    assert legacy is canonical
    actual = Path(canonical.__file__).resolve()
    assert actual.parts[-(len(package.split(".")) + 1):] == tuple(package.split(".")) + (module + ".py",)


def test_prior_editorial_materializer_path_is_still_import_compatible():
    old = import_module(f"{ROOT}.editorial.content_publication_store")
    new = import_module(f"{ROOT}.website.content_publication_store")
    assert old is new

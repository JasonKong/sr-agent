from importlib.metadata import version

import sr_agent


def test_package_version_matches_installed_metadata() -> None:
    assert sr_agent.__version__ == version("sr-agent")

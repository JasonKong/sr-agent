from importlib.metadata import version

import sr_evals


def test_package_version_matches_installed_metadata() -> None:
    assert sr_evals.__version__ == version("sr-evals")

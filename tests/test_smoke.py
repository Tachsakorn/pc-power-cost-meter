import re
from importlib import metadata

import pytest

import power_meter


def test_version_is_plain_major_minor_patch() -> None:
    # The Windows version resource needs four numbers, built from these three.
    assert re.fullmatch(r"\d+\.\d+\.\d+", power_meter.__version__)


def test_installed_metadata_matches_package_version() -> None:
    try:
        installed = metadata.version("power-meter")
    except metadata.PackageNotFoundError:
        pytest.skip("power-meter is not installed")
    assert installed == power_meter.__version__

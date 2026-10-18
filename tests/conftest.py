from collections.abc import Callable, Iterator

import customtkinter as ctk
import pytest


@pytest.fixture
def set_scaling() -> Iterator[Callable[[float], None]]:
    """Changes customtkinter's scaling the way a move to a monitor with another DPI does."""

    def rescale(factor: float) -> None:
        ctk.set_widget_scaling(factor)
        ctk.set_window_scaling(factor)

    yield rescale
    rescale(1.0)

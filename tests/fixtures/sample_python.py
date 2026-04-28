"""Module docstring."""
import os
import sys
from pathlib import Path
from typing import Optional, List
from .utils import helper_func
from ..config import settings

__all__ = ["PublicClass", "public_function"]

CONSTANT = "value"
ANOTHER_CONSTANT = 42


class PublicClass:
    """A public class with methods."""

    def __init__(self, name: str) -> None:
        self.name = name

    def method(self, x: int) -> str:
        """A public method."""
        return str(x)

    def _private_method(self) -> None:
        pass


async def public_function(email: str, password: str) -> Optional[str]:
    """Async public function."""
    pass


def _private_function() -> None:
    pass


if __name__ == "__main__":
    pass

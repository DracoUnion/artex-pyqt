"""Common zero-Qt building blocks for ARTEX.

This package never imports PySide6, so the service and task layers remain
importable and testable headlessly.
"""

from common.events import EventHub
from common.context import AppContext

__all__ = ["EventHub", "AppContext"]
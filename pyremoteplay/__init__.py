"""Init file for pyremoteplay."""
import logging

from .ctrl import CTRL

# logging.basicConfig(level=logging.INFO)
# _LOGGER = logging.getLogger(__name__)


def run(host: str, regist_data: dict):
    """Run Remote Play Session."""
    ctrl = CTRL(host, regist_data)
    ctrl.start()

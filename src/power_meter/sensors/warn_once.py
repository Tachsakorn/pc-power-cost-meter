import logging


class WarnOnce:
    """Logs a warning only when it differs from the previous one.

    Sensors are polled every second; a persistent fault should leave one log line, not thousands.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._last = ""

    def __call__(self, message: str, *args: object) -> None:
        text = message % args if args else message
        if text != self._last:
            self._last = text
            self._logger.warning(text)

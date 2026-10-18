from power_meter.single_instance import MUTEX_NAME, acquire

ERROR_ALREADY_EXISTS = 183


class FakeKernel32:
    def __init__(self, handle: int = 0x44, last_error: int = 0) -> None:
        self.handle = handle
        self.last_error = last_error
        self.created: list[tuple[None, bool, str]] = []

    def CreateMutexW(self, attributes: None, initial_owner: bool, name: str) -> int:
        self.created.append((attributes, initial_owner, name))
        return self.handle

    def GetLastError(self) -> int:
        return self.last_error


def test_first_instance_acquires_the_named_mutex() -> None:
    kernel32 = FakeKernel32()

    assert acquire(kernel32) is True
    assert kernel32.created == [(None, False, MUTEX_NAME)]


def test_second_instance_detected() -> None:
    assert acquire(FakeKernel32(last_error=ERROR_ALREADY_EXISTS)) is False


def test_runs_anyway_when_the_mutex_cannot_be_created() -> None:
    assert acquire(FakeKernel32(handle=0, last_error=5)) is True

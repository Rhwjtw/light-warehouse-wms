"""Fail before test collection when the configured MySQL is not the isolated target."""
import pytest


def pytest_sessionstart(session):
    from scripts.check_test_target import check_test_target

    try:
        check_test_target()
    except Exception as exc:
        raise pytest.UsageError(f'后端测试已停止：{exc}') from exc

"""Pytest configuration and safety nets for Windows Computer Use test suites."""

import pytest

# Enforce a 30-second ceiling across any test to prevent OS pipe or deadlock hangs
def pytest_configure(config):
    config.addinivalue_line(
        "markers", "timeout(seconds): mark test with a execution timeout"
    )

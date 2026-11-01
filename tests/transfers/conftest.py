"""Fixtures for the transfer API suites: a live HTTP server per test."""

from __future__ import annotations

import pytest

from lib.api_server import ApiServer
from transfer_api.app import create_app


@pytest.fixture
def service(make_service):
    return make_service()


@pytest.fixture
def api(service):
    """The app on a real port. The worker is NOT started: tests call service.tick()
    themselves, so every status change happens exactly when the test says."""
    server = ApiServer(create_app(service)).start()
    yield server
    server.stop()


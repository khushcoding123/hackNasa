"""Offline enforcement must fail closed before any real network access."""
import importlib.util
from pathlib import Path
import socket

import pytest


def test_demo_offline_blocks_connections_and_dns():
    path = Path(__file__).parents[1]/'scripts'/'17_demo.py'
    spec = importlib.util.spec_from_file_location('offline_demo', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = socket.create_connection
    with module.offline():
        with pytest.raises(AssertionError, match='Network access forbidden'):
            socket.create_connection(('127.0.0.1', 1))
        with pytest.raises(AssertionError, match='Network access forbidden'):
            socket.getaddrinfo('localhost', 1)
        with socket.socket() as connection:
            with pytest.raises(AssertionError, match='Network access forbidden'):
                connection.connect(('127.0.0.1', 1))
    assert socket.create_connection is original

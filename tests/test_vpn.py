"""TDD RED phase: tests for VPN detection TCP probe fallback."""

import socket
from unittest.mock import MagicMock, patch

from db_mcp_py.tunnels import check_vpn


@patch("db_mcp_py.tunnels.socket.create_connection")
@patch("db_mcp_py.tunnels.subprocess.run")
def test_route_check_passes_skips_probe(mock_run, mock_conn):
    mock_run.return_value = MagicMock(stdout="10.202.0.0/16 dev tun0")
    assert check_vpn() is True
    mock_conn.assert_not_called()


@patch("db_mcp_py.tunnels.socket.create_connection")
@patch("db_mcp_py.tunnels.subprocess.run")
def test_route_fails_probe_succeeds(mock_run, mock_conn):
    mock_run.return_value = MagicMock(stdout="")
    mock_conn.return_value = MagicMock()
    assert check_vpn(probe_targets=[("10.202.171.138", 5433)]) is True


@patch("db_mcp_py.tunnels.socket.create_connection")
@patch("db_mcp_py.tunnels.subprocess.run")
def test_route_fails_all_probes_timeout(mock_run, mock_conn):
    mock_run.return_value = MagicMock(stdout="")
    mock_conn.side_effect = socket.timeout
    assert check_vpn(probe_targets=[("10.202.171.138", 5433)]) is False


@patch("db_mcp_py.tunnels.socket.create_connection")
@patch("db_mcp_py.tunnels.subprocess.run")
def test_route_fails_probe_targets_empty(mock_run, mock_conn):
    mock_run.return_value = MagicMock(stdout="")
    assert check_vpn(probe_targets=[]) is False


@patch("db_mcp_py.tunnels.socket.create_connection")
@patch("db_mcp_py.tunnels.subprocess.run")
def test_route_fails_probe_targets_none(mock_run, mock_conn):
    mock_run.return_value = MagicMock(stdout="")
    assert check_vpn(probe_targets=None) is False


@patch("db_mcp_py.tunnels.socket.create_connection")
@patch("db_mcp_py.tunnels.subprocess.run")
def test_probe_connection_refused_tries_next(mock_run, mock_conn):
    mock_run.return_value = MagicMock(stdout="")
    mock_conn.side_effect = [OSError("refused"), MagicMock()]
    assert check_vpn(probe_targets=[("10.1.1.1", 5432), ("10.202.171.138", 5433)]) is True


@patch("db_mcp_py.tunnels.socket.create_connection")
@patch("db_mcp_py.tunnels.subprocess.run")
def test_no_ip_command_falls_through_to_probe(mock_run, mock_conn):
    mock_run.side_effect = FileNotFoundError
    mock_conn.return_value = MagicMock()
    assert check_vpn(probe_targets=[("10.202.171.138", 5433)]) is True

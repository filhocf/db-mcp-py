"""SSH tunnel manager using asyncssh."""

from __future__ import annotations

import logging
import socket
import subprocess

import asyncssh

from .config import TunnelConfig

logger = logging.getLogger(__name__)


def _resolve_ssh_config(host_alias: str) -> dict:
    """Resolve ssh config for a host alias via ssh -G."""
    try:
        result = subprocess.run(
            ["ssh", "-G", host_alias],
            capture_output=True,
            text=True,
            timeout=5,
        )
        parsed: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, _, value = line.partition(" ")
            parsed[key] = value
        return {
            "hostname": parsed.get("hostname", host_alias),
            "user": parsed.get("user"),
            "port": int(parsed.get("port", 22)),
            "identity_file": parsed.get("identityfile"),
            "proxy_jump": parsed.get("proxyjump"),
        }
    except Exception:
        return {"hostname": host_alias}


class TunnelManager:
    """Manages SSH tunnels for database connections."""

    def __init__(self) -> None:
        self._tunnels: dict[str, asyncssh.SSHClientConnection] = {}
        self._jump_conns: dict[str, asyncssh.SSHClientConnection] = {}
        self._listeners: dict[str, asyncssh.SSHListener] = {}

    async def open(self, conn_id: str, tunnel: TunnelConfig) -> tuple[str, int]:
        """Open an SSH tunnel. Returns (local_host, local_port)."""
        if conn_id in self._tunnels:
            return "127.0.0.1", tunnel.local_port

        try:
            # Resolve target host ssh config
            target_cfg = _resolve_ssh_config(tunnel.ssh_host)
            target_hostname = target_cfg["hostname"]

            target_opts: dict = {
                "known_hosts": None,
                "username": tunnel.ssh_user or target_cfg.get("user"),
            }
            if tunnel.ssh_key:
                target_opts["client_keys"] = [tunnel.ssh_key]
            elif target_cfg.get("identity_file"):
                target_opts["client_keys"] = [target_cfg["identity_file"]]

            jump_conn = None
            if tunnel.jump_host:
                jump_cfg = _resolve_ssh_config(tunnel.jump_host)
                jump_opts: dict = {
                    "known_hosts": None,
                    "username": tunnel.jump_user or jump_cfg.get("user"),
                }
                if tunnel.jump_key:
                    jump_opts["client_keys"] = [tunnel.jump_key]
                elif jump_cfg.get("identity_file"):
                    jump_opts["client_keys"] = [jump_cfg["identity_file"]]

                logger.info("Tunnel %s: connecting via jump %s → %s", conn_id, tunnel.jump_host, tunnel.ssh_host)
                jump_conn = await asyncssh.connect(jump_cfg["hostname"], **jump_opts)
                self._jump_conns[conn_id] = jump_conn
                ssh_conn = await asyncssh.connect(target_hostname, tunnel=jump_conn, **target_opts)
            else:
                logger.info("Tunnel %s: connecting to %s", conn_id, tunnel.ssh_host)
                ssh_conn = await asyncssh.connect(target_hostname, **target_opts)

            listener = await ssh_conn.forward_local_port(
                "127.0.0.1",
                tunnel.local_port,
                tunnel.remote_host,
                tunnel.remote_port,
            )

            self._tunnels[conn_id] = ssh_conn
            self._listeners[conn_id] = listener
            logger.info(
                "Tunnel %s: 127.0.0.1:%d → %s:%d via %s",
                conn_id,
                tunnel.local_port,
                tunnel.remote_host,
                tunnel.remote_port,
                tunnel.ssh_host,
            )
            return "127.0.0.1", tunnel.local_port

        except OSError as e:
            import errno

            if e.errno == errno.EADDRINUSE:  # Address already in use — external tunnel?
                import asyncio

                try:
                    _, writer = await asyncio.wait_for(
                        asyncio.open_connection("127.0.0.1", tunnel.local_port),
                        timeout=2.0,
                    )
                    writer.close()
                    await writer.wait_closed()
                    logger.info("Tunnel %s: port %d already bound, reusing external tunnel", conn_id, tunnel.local_port)
                    return "127.0.0.1", tunnel.local_port
                except (asyncio.TimeoutError, OSError):
                    pass
            logger.exception("Tunnel %s: failed to open", conn_id)
            raise
        except Exception:
            logger.exception("Tunnel %s: failed to open", conn_id)
            raise

    async def close(self, conn_id: str) -> None:
        """Close a specific tunnel."""
        if listener := self._listeners.pop(conn_id, None):
            listener.close()
        if conn := self._tunnels.pop(conn_id, None):
            conn.close()
            await conn.wait_closed()
        if jump := self._jump_conns.pop(conn_id, None):
            jump.close()
            await jump.wait_closed()
        logger.info("Tunnel %s: closed", conn_id)

    async def close_all(self) -> None:
        """Close all tunnels."""
        for conn_id in list(self._tunnels):
            await self.close(conn_id)


def check_vpn(
    prefixes: list[str] | None = None,
    probe_targets: list[tuple[str, int]] | None = None,
    probe_timeout: float = 2.0,
) -> bool:
    """Check if VPN is active by route prefixes, falling back to TCP probe."""
    if not prefixes:
        prefixes = ["10.195.", "10.202.", "10.188."]
    try:
        result = subprocess.run(["ip", "route"], capture_output=True, text=True, timeout=5)
        if any(prefix in result.stdout for prefix in prefixes):
            return True
    except Exception:
        pass
    # Fallback: TCP probe
    if not probe_targets:
        return False
    for host, port in probe_targets:
        try:
            conn = socket.create_connection((host, port), timeout=probe_timeout)
            conn.close()
            return True
        except (OSError, socket.timeout):
            continue
    return False

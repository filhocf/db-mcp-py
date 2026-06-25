"""Configuration models with env var expansion."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class TunnelConfig(BaseModel):
    """SSH tunnel configuration."""

    ssh_host: str
    jump_host: str | None = None
    jump_user: str | None = None
    jump_key: str | None = None
    remote_host: str = "localhost"
    remote_port: int = 5432
    local_port: int
    ssh_user: str | None = None
    ssh_key: str | None = None


class ConnectionConfig(BaseModel):
    """Single database connection configuration."""

    id: str
    type: str = "postgresql"
    host: str = "localhost"
    port: int | None = None
    database: str
    user: str
    password: str | None = None
    schemas: list[str] = Field(default_factory=list)
    tunnel: TunnelConfig | None = None
    require_vpn: bool = False
    query_timeout: int | None = None
    max_connections: int | None = None
    read_only: bool | None = None
    ssl: bool | str = False
    permission: str = Field(default="readonly", pattern=r"^(readonly|readwrite|admin)$")

    @model_validator(mode="after")
    def resolve_default_port(self) -> ConnectionConfig:
        if self.port is None:
            defaults = {"postgresql": 5432, "mysql": 3306, "mongodb": 27017}
            self.port = defaults.get(self.type, 5432)
        return self


class DefaultsConfig(BaseModel):
    """Default settings applied to all connections."""

    read_only: bool = True
    query_timeout: int = 30
    max_connections: int = 5
    connect_timeout: int = 15
    vpn_route_prefixes: list[str] = Field(default_factory=list)
    schema_cache_ttl: int = 300


class Config(BaseModel):
    """Root configuration."""

    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    connections: list[ConnectionConfig]

    @model_validator(mode="after")
    def check_unique_ids(self) -> Config:
        ids = [c.id for c in self.connections]
        dupes = {x for x in ids if ids.count(x) > 1}
        if dupes:
            raise ValueError(f"Duplicate connection IDs: {dupes}")
        return self

    @model_validator(mode="after")
    def check_unique_local_ports(self) -> Config:
        ports: list[int] = []
        for conn in self.connections:
            if conn.tunnel:
                if conn.tunnel.local_port in ports:
                    raise ValueError(f"Duplicate local_port {conn.tunnel.local_port} in connection '{conn.id}'")
                ports.append(conn.tunnel.local_port)
        return self

    def get_effective(self, conn: ConnectionConfig) -> dict:
        """Get effective settings for a connection (connection overrides defaults)."""
        return {
            "read_only": conn.read_only if conn.read_only is not None else self.defaults.read_only,
            "query_timeout": conn.query_timeout or self.defaults.query_timeout,
            "max_connections": conn.max_connections or self.defaults.max_connections,
            "connect_timeout": self.defaults.connect_timeout,
            "schema_cache_ttl": self.defaults.schema_cache_ttl,
        }


def load_config(path: str) -> Config:
    """Load and validate config from JSON file."""
    raw = Path(path).read_text()
    raw = os.path.expandvars(raw)
    data = json.loads(raw)
    return Config(**data)

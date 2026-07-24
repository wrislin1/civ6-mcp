from __future__ import annotations

import json
import sys
import types
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

DEFAULT_URL = "https://calculator.brothereye.net/endpoints.json"
SUPPORTED_VERSION = 1
ENDPOINT_NETWORKS = {"lan", "host_loopback", "litellm"}
GATEWAY_NETWORKS = {"lan", "host_loopback", "compose"}


class RegistryLoadError(RuntimeError):
    pass


class UnsupportedRegistryVersion(RegistryLoadError):
    pass


@dataclass(frozen=True)
class Endpoint:
    id: str
    kind: str
    host_id: str
    gpu_indexes: tuple[int, ...]
    port: int
    urls: Mapping[str, str]
    units: tuple[str, ...]
    modes: tuple[str, ...]
    drain_by_hosts: tuple[str, ...]
    acquisition: str


@dataclass(frozen=True)
class Gpu:
    host_id: str
    index: int
    gpu_class: str
    lane_policy: str
    unmanaged_pinning: str


@dataclass(frozen=True)
class Registry:
    _endpoints: Mapping[str, Endpoint]
    _gpus: Mapping[tuple[str, int], Gpu]
    _gateway_roots: Mapping[str, str]
    _gateway_openai_urls: Mapping[str, str]

    def endpoint(self, endpoint_id: str) -> Endpoint:
        return self._endpoints[endpoint_id]

    def endpoint_ids(self) -> tuple[str, ...]:
        """Return the known endpoint ids in deterministic order."""
        return tuple(sorted(self._endpoints))

    def gpu(self, host_id: str, index: int) -> Gpu:
        return self._gpus[(host_id, index)]

    def url(
        self,
        endpoint_id: str,
        *,
        network: str,
        caller_host_id: str | None = None,
    ) -> str:
        endpoint = self.endpoint(endpoint_id)
        if network not in ENDPOINT_NETWORKS:
            raise ValueError(f"unknown endpoint network {network!r}")
        if network == "host_loopback" and caller_host_id != endpoint.host_id:
            raise ValueError("host_loopback requires the endpoint owner as caller")
        try:
            return endpoint.urls[network]
        except KeyError:
            raise ValueError(
                f"endpoint {endpoint_id!r} has no {network!r} URL"
            ) from None

    def gateway(self, *, network: str) -> str:
        if network not in GATEWAY_NETWORKS:
            raise ValueError(f"unknown gateway network {network!r}")
        return self._gateway_openai_urls[network]

    def gateway_root(self, *, network: str) -> str:
        if network not in GATEWAY_NETWORKS:
            raise ValueError(f"unknown gateway network {network!r}")
        return self._gateway_roots[network]


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RegistryLoadError(f"invalid {label}")
    return value


def _string_tuple(value: Any, label: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RegistryLoadError(f"invalid {label}")
    if (not allow_empty and not value) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise RegistryLoadError(f"invalid {label}")
    return tuple(value)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryLoadError(f"invalid {label}")
    return value


def _parse(payload: Any) -> Registry:
    root = _mapping(payload, "registry object")
    version = root.get("registry_version")
    if version != SUPPORTED_VERSION:
        raise UnsupportedRegistryVersion(
            f"unsupported registry version {version!r}"
        )
    _string(root.get("_generated"), "_generated")
    source = _mapping(root.get("source"), "source")
    if _string(source.get("manifest"), "source manifest") != "models.yaml":
        raise RegistryLoadError("invalid source manifest")
    if _string(source.get("generator"), "source generator") != (
        "tools/scripts/gen-configs.py"
    ):
        raise RegistryLoadError("invalid source generator")
    digest = _string(source.get("models_yaml_sha256"), "source digest")
    try:
        digest_valid = len(digest) == 64 and int(digest, 16) >= 0
    except ValueError:
        digest_valid = False
    if not digest_valid:
        raise RegistryLoadError("invalid source digest")

    hosts_raw = _mapping(root.get("hosts"), "hosts")
    host_ids = set(hosts_raw)
    for host_id, host in hosts_raw.items():
        _string(host_id, "host id")
        _string(_mapping(host, "host").get("lan_ip"), "host lan_ip")

    gpus: dict[tuple[str, int], Gpu] = {}
    gpu_rows = root.get("gpus")
    if not isinstance(gpu_rows, list):
        raise RegistryLoadError("invalid gpus")
    for row_value in gpu_rows:
        row = _mapping(row_value, "gpu")
        host_id = _string(row.get("host_id"), "gpu host_id")
        index = row.get("index")
        if host_id not in host_ids or type(index) is not int or index < 0:
            raise RegistryLoadError("GPU references unknown host or invalid index")
        key = (host_id, index)
        if key in gpus:
            raise RegistryLoadError("duplicate GPU")
        gpu_class = _string(row.get("gpu_class"), "gpu_class")
        pinning = _string(row.get("unmanaged_pinning"), "unmanaged_pinning")
        if gpu_class not in {"rtx-3090", "rtx-5060-ti"}:
            raise RegistryLoadError("invalid gpu_class")
        if pinning not in {"allowed", "forbidden"}:
            raise RegistryLoadError("invalid unmanaged_pinning")
        gpus[key] = Gpu(
            host_id=host_id,
            index=index,
            gpu_class=gpu_class,
            lane_policy=_string(row.get("lane_policy"), "lane_policy"),
            unmanaged_pinning=pinning,
        )

    endpoints: dict[str, Endpoint] = {}
    endpoint_rows = root.get("endpoints")
    if not isinstance(endpoint_rows, list):
        raise RegistryLoadError("invalid endpoints")
    for row_value in endpoint_rows:
        row = _mapping(row_value, "endpoint")
        endpoint_id = _string(row.get("id"), "endpoint id")
        if endpoint_id in endpoints:
            raise RegistryLoadError("duplicate endpoint id")
        host_id = _string(row.get("host_id"), "endpoint host_id")
        indexes_value = row.get("gpu_indexes")
        if not isinstance(indexes_value, list) or not indexes_value:
            raise RegistryLoadError("invalid endpoint GPU indexes")
        indexes = tuple(indexes_value)
        if (
            any(type(index) is not int or index < 0 for index in indexes)
            or len(set(indexes)) != len(indexes)
        ):
            raise RegistryLoadError("invalid endpoint GPU indexes")
        if any((host_id, index) not in gpus for index in indexes):
            raise RegistryLoadError("endpoint references unknown host/GPU")
        kind = _string(row.get("kind"), "endpoint kind")
        if kind not in {"ollama", "llamacpp", "llamacpp-session"}:
            raise RegistryLoadError("invalid endpoint kind")
        urls = _mapping(row.get("urls"), "endpoint urls")
        required_urls = {"lan", "host_loopback"}
        if kind != "llamacpp-session":
            required_urls.add("litellm")
        if not required_urls.issubset(urls):
            raise RegistryLoadError("endpoint has incomplete URL contexts")
        normalized_urls = {}
        for key, value in urls.items():
            value = _string(value, "endpoint URL")
            parsed = urllib.parse.urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise RegistryLoadError("invalid endpoint URL")
            normalized_urls[key] = value
        immutable_urls = types.MappingProxyType(normalized_urls)
        drains = _string_tuple(
            row.get("drain_by_hosts"), "drain_by_hosts", allow_empty=True
        )
        if any(owner not in host_ids for owner in drains):
            raise RegistryLoadError("endpoint references unknown drain owner")
        if kind == "llamacpp-session" and drains:
            raise RegistryLoadError("session endpoint cannot have drain owner")
        if kind != "llamacpp-session" and drains != (host_id,):
            raise RegistryLoadError("ordinary endpoint drain owner must match host")
        port = row.get("port")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise RegistryLoadError("invalid endpoint port")
        endpoints[endpoint_id] = Endpoint(
            id=endpoint_id,
            kind=kind,
            host_id=host_id,
            gpu_indexes=indexes,
            port=port,
            urls=immutable_urls,
            units=_string_tuple(row.get("units"), "endpoint units"),
            modes=_string_tuple(row.get("modes"), "endpoint modes"),
            drain_by_hosts=drains,
            acquisition=_string(row.get("acquisition"), "endpoint acquisition"),
        )

    gateway = _mapping(root.get("gateway"), "gateway")
    if _string(gateway.get("id"), "gateway id") != "litellm":
        raise RegistryLoadError("invalid gateway id")
    roots = _mapping(gateway.get("roots"), "gateway roots")
    openai_urls = _mapping(gateway.get("openai_urls"), "gateway OpenAI URLs")
    if set(roots) != GATEWAY_NETWORKS or set(openai_urls) != GATEWAY_NETWORKS:
        raise RegistryLoadError("invalid gateway contexts")
    for network in GATEWAY_NETWORKS:
        if openai_urls[network] != f"{str(roots[network]).rstrip('/')}/v1":
            raise RegistryLoadError("gateway OpenAI URL is not root + /v1")
    if urllib.parse.urlparse(str(roots["lan"])).scheme != "https":
        raise RegistryLoadError("gateway LAN root must use HTTPS")

    return Registry(
        _endpoints=types.MappingProxyType(endpoints),
        _gpus=types.MappingProxyType(gpus),
        _gateway_roots=types.MappingProxyType(dict(roots)),
        _gateway_openai_urls=types.MappingProxyType(dict(openai_urls)),
    )


def _decode(body: bytes) -> Registry:
    try:
        return _parse(json.loads(body))
    except UnsupportedRegistryVersion:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RegistryLoadError) as exc:
        raise RegistryLoadError("invalid registry payload") from None


def load(
    *,
    url: str = DEFAULT_URL,
    fallback: Path,
    timeout_s: float = 2.0,
) -> Registry:
    parsed_url = urllib.parse.urlparse(url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise RegistryLoadError("registry URL must use HTTPS")
    try:
        request = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": "brothereye-registry/1"}
        )
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = response.read()
        return _decode(body)
    except UnsupportedRegistryVersion:
        raise
    except Exception:
        print(
            "brothereye-registry: remote load failed; using vendored fallback",
            file=sys.stderr,
        )

    try:
        return _decode(Path(fallback).read_bytes())
    except Exception:
        raise RegistryLoadError("vendored registry fallback is missing or invalid") from None


def load_snapshot(path: Path) -> Registry:
    """Load a local snapshot only; never perform network I/O."""
    try:
        return _decode(Path(path).read_bytes())
    except UnsupportedRegistryVersion:
        raise
    except Exception:
        raise RegistryLoadError("registry snapshot is missing or invalid") from None

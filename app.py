#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
SESSION_COOKIE = "drm_session"
SESSION_TTL_SECONDS = 8 * 60 * 60

SESSIONS: dict[str, dict[str, Any]] = {}

MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.docker.distribution.manifest.v1+json",
    ]
)
CONFIG_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.config.v1+json",
        "application/vnd.docker.container.image.v1+json",
        "application/json",
    ]
)
MANIFEST_LIST_MEDIA_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}


class RegistryError(Exception):
    def __init__(self, status: int, message: str, detail: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"message": self.message}
        if self.detail:
            payload["detail"] = self.detail
        return payload


def parse_registry_url(value: str) -> tuple[str, str, str]:
    raw_value = (value or "").strip()
    if not raw_value:
        raise ValueError("请输入镜像仓库地址")
    if "://" not in raw_value:
        raw_value = f"https://{raw_value}"

    parsed = urllib.parse.urlparse(raw_value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("仓库地址只支持 http 或 https")
    if not parsed.netloc:
        raise ValueError("仓库地址缺少主机名")

    username = urllib.parse.unquote(parsed.username or "")
    password = urllib.parse.unquote(parsed.password or "")
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("仓库地址端口号无效") from exc
    netloc = f"{host}:{port}" if port else host
    path = parsed.path.rstrip("/")
    return urllib.parse.urlunparse((parsed.scheme, netloc, path, "", "", "")), username, password


def normalize_registry_url(value: str) -> str:
    registry_url, _, _ = parse_registry_url(value)
    return registry_url


def normalize_repository_prefix(value: str) -> str:
    raw_value = (value or "").strip()
    if not raw_value:
        return ""
    if "://" in raw_value:
        parsed = urllib.parse.urlparse(raw_value)
        raw_value = parsed.path
    # 只保留 Registry 中的 repository 路径，避免用户误填 /v2/ 或 tag 影响后续查询。
    prefix = raw_value.strip().strip("/")
    if prefix.startswith("v2/"):
        prefix = prefix.removeprefix("v2/").strip("/")
    if ":" in prefix.rsplit("/", 1)[-1]:
        prefix = prefix.rsplit(":", 1)[0]
    return re.sub(r"/+", "/", prefix)


def parse_www_authenticate(header_value: str | None) -> tuple[str, dict[str, str]] | None:
    if not header_value:
        return None

    value = header_value.strip()
    if not value:
        return None

    # Registry 通常只返回一个 challenge；这里优先处理 Bearer，其次兼容 Basic。
    lower = value.lower()
    bearer_index = lower.find("bearer ")
    if bearer_index >= 0:
        value = value[bearer_index:]

    parts = value.split(" ", 1)
    scheme = parts[0].lower()
    param_text = parts[1] if len(parts) > 1 else ""
    params: dict[str, str] = {}
    for match in re.finditer(r'([A-Za-z_][A-Za-z0-9_-]*)=(?:"((?:\\.|[^"])*)"|([^,]+))', param_text):
        raw = match.group(2) if match.group(2) is not None else match.group(3)
        params[match.group(1).lower()] = raw.replace(r"\"", '"').strip()

    return scheme, params


def next_last_from_link(link_header: str | None) -> str | None:
    if not link_header:
        return None
    match = re.search(r"<([^>]+)>;\s*rel=\"?next\"?", link_header)
    if not match:
        return None
    parsed = urllib.parse.urlparse(match.group(1))
    params = urllib.parse.parse_qs(parsed.query)
    values = params.get("last")
    return values[0] if values else None


def quote_repo_path(repository: str) -> str:
    return urllib.parse.quote(repository.strip("/"), safe="/")


def quote_reference(reference: str) -> str:
    return urllib.parse.quote(reference, safe=":@")


def docker_error_message(status: int, reason: str, body: bytes) -> tuple[str, str | None]:
    detail: str | None = None
    text = body.decode("utf-8", errors="replace").strip()
    if text:
        detail = text[:1000]
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                errors = data.get("errors")
                if isinstance(errors, list) and errors:
                    first_error = errors[0]
                    if isinstance(first_error, dict):
                        message = first_error.get("message") or first_error.get("code")
                        if message:
                            detail = json.dumps(errors, ensure_ascii=False)
                            return str(message), detail[:1000]
                message = data.get("message") or data.get("error")
                if message:
                    return str(message), detail
        except json.JSONDecodeError:
            pass

    friendly = {
        400: "远端仓库拒绝了请求参数",
        401: "认证失败或登录已失效",
        403: "当前账号没有该操作权限",
        404: "远端仓库没有找到对应资源",
        405: "远端仓库未启用删除能力或不支持该操作",
        429: "远端仓库请求过于频繁",
    }.get(status)
    return friendly or reason or f"Registry 请求失败（HTTP {status}）", detail


class RegistryClient:
    def __init__(
        self,
        registry_url: str,
        username: str = "",
        password: str = "",
        verify_tls: bool = True,
        repository_prefix: str = "",
    ):
        self.base_url, url_username, url_password = parse_registry_url(registry_url)
        self.username = username or url_username or ""
        self.password = password or url_password or ""
        self.verify_tls = verify_tls
        self.repository_prefix = normalize_repository_prefix(repository_prefix)
        self.timeout = 15
        self._tokens: dict[str, tuple[str, float]] = {}

    def public_info(self) -> dict[str, Any]:
        return {
            "registry": self.base_url,
            "username": self.username,
            "verifyTls": self.verify_tls,
            "repositoryPrefix": self.repository_prefix,
        }

    def ping(self) -> dict[str, Any]:
        status, headers, _ = self.request("GET", "/v2/")
        return {
            "ok": 200 <= status < 300,
            "status": status,
            "registry": self.base_url,
            "distributionVersion": headers.get("Docker-Distribution-Api-Version"),
        }

    def catalog(self, limit: int, last: str | None = None) -> dict[str, Any]:
        repositories: list[str] = []
        next_last = last
        scans = 0
        max_scans = 20 if self.repository_prefix else 1
        request_limit = 500 if self.repository_prefix else limit

        while scans < max_scans:
            query = {"n": str(request_limit)}
            if next_last:
                query["last"] = next_last
            _, headers, body = self.request(
                "GET",
                "/v2/_catalog",
                query=query,
                headers={"Accept": "application/json"},
                scope="registry:catalog:*",
            )
            data = self._json_body(body)
            raw_repositories = data.get("repositories") if isinstance(data, dict) else None
            page_repositories = raw_repositories if isinstance(raw_repositories, list) else []
            if self.repository_prefix:
                repositories.extend(
                    repo
                    for repo in page_repositories
                    if isinstance(repo, str)
                    and (repo == self.repository_prefix or repo.startswith(f"{self.repository_prefix}/"))
                )
            else:
                repositories.extend(repo for repo in page_repositories if isinstance(repo, str))

            scans += 1
            next_last = next_last_from_link(headers.get("Link"))
            if not self.repository_prefix or len(repositories) >= limit or not next_last:
                break

        return {
            "repositories": repositories,
            "nextLast": next_last,
            "repositoryPrefix": self.repository_prefix,
        }

    def tags(self, repository: str, limit: int, last: str | None = None) -> dict[str, Any]:
        query = {"n": str(limit)}
        if last:
            query["last"] = last
        _, headers, body = self.request(
            "GET",
            f"/v2/{quote_repo_path(repository)}/tags/list",
            query=query,
            headers={"Accept": "application/json"},
            scope=f"repository:{repository}:pull",
        )
        data = self._json_body(body)
        tags = data.get("tags") if isinstance(data, dict) else None
        return {
            "name": data.get("name", repository) if isinstance(data, dict) else repository,
            "tags": tags if isinstance(tags, list) else [],
            "nextLast": next_last_from_link(headers.get("Link")),
        }

    def tag_count(self, repository: str, max_pages: int) -> dict[str, Any]:
        count = 0
        next_last: str | None = None
        page_size = 500
        pages = 0

        while pages < max_pages:
            query = {"n": str(page_size)}
            if next_last:
                query["last"] = next_last
            _, headers, body = self.request(
                "GET",
                f"/v2/{quote_repo_path(repository)}/tags/list",
                query=query,
                headers={"Accept": "application/json"},
                scope=f"repository:{repository}:pull",
            )
            data = self._json_body(body)
            tags = data.get("tags") if isinstance(data, dict) else None
            count += len(tags) if isinstance(tags, list) else 0
            pages += 1

            next_last = next_last_from_link(headers.get("Link"))
            if not next_last:
                break

        has_more = bool(next_last)
        return {
            "repository": repository,
            "tagCount": count,
            "hasMore": has_more,
            "countText": f"{count}+" if has_more else str(count),
        }

    def storage_usage(
        self,
        repository: str | None = None,
        max_tags: int = 1000,
        max_tag_pages: int = 20,
    ) -> dict[str, Any]:
        repository = normalize_repository_prefix(repository or "")
        if not repository:
            raise ValueError("请选择要计算空间的镜像")
        manifest_cache: dict[tuple[str, str], dict[str, Any]] = {}
        blob_sizes: dict[str, int] = {}
        errors: list[dict[str, str]] = []
        summary = self._repository_storage_usage(
            repository,
            max_tags,
            max_tag_pages,
            blob_sizes,
            manifest_cache,
            errors,
        )

        total_bytes = sum(blob_sizes.values())
        return {
            "scope": "repository",
            "repository": repository,
            "repositoryPrefix": self.repository_prefix,
            "repositoriesScanned": 1,
            "repositoriesTruncated": False,
            "tagsScanned": summary["tagsScanned"],
            "tagsTruncated": summary["tagsTruncated"],
            "truncated": summary["tagsTruncated"],
            "unknownTags": summary["unknownTags"],
            "manifestErrors": errors,
            "manifestErrorCount": summary["manifestErrorCount"],
            "uniqueBlobCount": len(blob_sizes),
            "totalBytes": total_bytes,
            "totalText": self._format_size(total_bytes),
            "sumTagBytes": summary["sumTagBytes"],
            "sumTagText": summary["sumTagText"],
            "repositories": [summary],
        }

    def manifest(self, repository: str, reference: str) -> dict[str, Any]:
        path = f"/v2/{quote_repo_path(repository)}/manifests/{quote_reference(reference)}"
        headers = {"Accept": MANIFEST_ACCEPT}
        _, response_headers, body = self.request(
            "GET",
            path,
            headers=headers,
            scope=f"repository:{repository}:pull",
        )

        digest = response_headers.get("Docker-Content-Digest")
        if not digest:
            raise RegistryError(502, "远端仓库没有返回 Docker-Content-Digest，无法安全删除")

        manifest_data = self._json_body(body)
        media_type = self._media_type(response_headers.get("Content-Type"))
        created_at = self._created_at_from_manifest(repository, manifest_data)
        last_modified = response_headers.get("Last-Modified")
        image_size_bytes = self._image_size_from_manifest(manifest_data)

        return {
            "repository": repository,
            "reference": reference,
            "digest": digest,
            "mediaType": media_type or self._manifest_media_type(manifest_data),
            "size": response_headers.get("Content-Length"),
            "imageSizeBytes": image_size_bytes,
            "imageSizeText": self._format_size(image_size_bytes),
            "createdAt": created_at,
            "lastModified": last_modified,
            "timeSource": "lastModified" if last_modified else ("configCreated" if created_at else ""),
            "timeNote": "" if (last_modified or created_at) else "标准 Registry V2 API 未返回上传时间",
        }

    def _repository_storage_usage(
        self,
        repository: str,
        max_tags: int,
        max_tag_pages: int,
        global_blob_sizes: dict[str, int],
        manifest_cache: dict[tuple[str, str], dict[str, Any]],
        errors: list[dict[str, str]],
    ) -> dict[str, Any]:
        tags, tags_truncated = self._tags_for_usage(repository, max_tags, max_tag_pages)
        repository_blob_sizes: dict[str, int] = {}
        sum_tag_bytes = 0
        unknown_tags = 0
        error_count = 0

        for tag in tags:
            try:
                usage = self._manifest_storage_descriptors(repository, tag, manifest_cache, set())
            except RegistryError as exc:
                error_count += 1
                if len(errors) < 10:
                    errors.append({"repository": repository, "tag": tag, "message": exc.message})
                continue

            image_size_bytes = usage.get("imageSizeBytes")
            if isinstance(image_size_bytes, int) and image_size_bytes > 0:
                sum_tag_bytes += image_size_bytes
            else:
                unknown_tags += 1

            for descriptor in usage.get("descriptors", []):
                if not isinstance(descriptor, dict):
                    continue
                digest = descriptor.get("digest")
                size = descriptor.get("size")
                if isinstance(digest, str) and isinstance(size, int) and size > 0:
                    repository_blob_sizes[digest] = size
                    global_blob_sizes[digest] = size

        total_bytes = sum(repository_blob_sizes.values())
        return {
            "repository": repository,
            "tagsScanned": len(tags),
            "tagsTruncated": tags_truncated,
            "unknownTags": unknown_tags,
            "manifestErrorCount": error_count,
            "uniqueBlobCount": len(repository_blob_sizes),
            "totalBytes": total_bytes,
            "totalText": self._format_size(total_bytes),
            "sumTagBytes": sum_tag_bytes,
            "sumTagText": self._format_size(sum_tag_bytes),
        }

    def _tags_for_usage(self, repository: str, max_tags: int, max_tag_pages: int) -> tuple[list[str], bool]:
        tags: list[str] = []
        next_last: str | None = None
        pages = 0
        truncated = False

        while pages < max_tag_pages and len(tags) < max_tags:
            page_limit = min(500, max_tags - len(tags))
            payload = self.tags(repository, page_limit, next_last)
            page_tags = [tag for tag in payload["tags"] if isinstance(tag, str)]
            tags.extend(page_tags)
            pages += 1
            next_last = payload.get("nextLast")
            if len(tags) >= max_tags:
                truncated = True
                break
            if not next_last:
                break

        return tags, truncated or bool(next_last)

    def _manifest_storage_descriptors(
        self,
        repository: str,
        reference: str,
        cache: dict[tuple[str, str], dict[str, Any]],
        seen_manifest_digests: set[str],
    ) -> dict[str, Any]:
        cache_key = (repository, reference)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        path = f"/v2/{quote_repo_path(repository)}/manifests/{quote_reference(reference)}"
        _, response_headers, body = self.request(
            "GET",
            path,
            headers={"Accept": MANIFEST_ACCEPT},
            scope=f"repository:{repository}:pull",
        )
        manifest_digest = response_headers.get("Docker-Content-Digest") or reference
        digest_key = (repository, manifest_digest)
        cached_by_digest = cache.get(digest_key)
        if cached_by_digest is not None:
            cache[cache_key] = cached_by_digest
            return cached_by_digest
        if manifest_digest in seen_manifest_digests:
            return {"descriptors": [], "imageSizeBytes": 0}

        seen_manifest_digests.add(manifest_digest)
        manifest_data = self._json_body(body)
        media_type = self._media_type(response_headers.get("Content-Type")) or self._manifest_media_type(manifest_data)
        descriptors = self._storage_descriptors_from_manifest(
            repository,
            manifest_data,
            str(media_type or ""),
            cache,
            seen_manifest_digests,
        )
        result = {
            "descriptors": descriptors,
            "imageSizeBytes": self._unique_descriptor_total(descriptors),
        }
        cache[cache_key] = result
        cache[digest_key] = result
        return result

    def _storage_descriptors_from_manifest(
        self,
        repository: str,
        manifest_data: Any,
        media_type: str,
        cache: dict[tuple[str, str], dict[str, Any]],
        seen_manifest_digests: set[str],
    ) -> list[dict[str, Any]]:
        if not isinstance(manifest_data, dict):
            return []

        # 多架构镜像的顶层 manifest list 只保存子 manifest 描述符；递归读取子 manifest 后才能估算实际层大小。
        manifests = manifest_data.get("manifests")
        if media_type in MANIFEST_LIST_MEDIA_TYPES and isinstance(manifests, list):
            descriptors: list[dict[str, Any]] = []
            for child_manifest in manifests:
                child_digest = child_manifest.get("digest") if isinstance(child_manifest, dict) else ""
                if not isinstance(child_digest, str) or not child_digest:
                    continue
                try:
                    child_usage = self._manifest_storage_descriptors(repository, child_digest, cache, seen_manifest_digests)
                except RegistryError:
                    continue
                for descriptor in child_usage.get("descriptors", []):
                    if isinstance(descriptor, dict):
                        descriptors.append(descriptor)
            return descriptors

        descriptors = []
        config = manifest_data.get("config")
        if isinstance(config, dict):
            self._append_storage_descriptor(descriptors, config, "config")

        layers = manifest_data.get("layers")
        if isinstance(layers, list):
            for layer in layers:
                if isinstance(layer, dict):
                    self._append_storage_descriptor(descriptors, layer, "layer")
        return descriptors

    def _append_storage_descriptor(self, descriptors: list[dict[str, Any]], descriptor: dict[str, Any], kind: str) -> None:
        digest = descriptor.get("digest")
        if not isinstance(digest, str) or not digest:
            return
        raw_size = descriptor.get("size")
        if isinstance(raw_size, bool):
            return
        try:
            size = int(raw_size)
        except (TypeError, ValueError):
            return
        if size > 0:
            descriptors.append({"digest": digest, "size": size, "kind": kind})

    def _unique_descriptor_total(self, descriptors: list[dict[str, Any]]) -> int | None:
        unique_sizes: dict[str, int] = {}
        for descriptor in descriptors:
            digest = descriptor.get("digest")
            size = descriptor.get("size")
            if isinstance(digest, str) and isinstance(size, int) and size > 0:
                unique_sizes[digest] = size
        return sum(unique_sizes.values()) if unique_sizes else None

    def manifest_digest(self, repository: str, reference: str) -> str:
        path = f"/v2/{quote_repo_path(repository)}/manifests/{quote_reference(reference)}"
        headers = {"Accept": MANIFEST_ACCEPT}
        try:
            _, response_headers, _ = self.request(
                "HEAD",
                path,
                headers=headers,
                scope=f"repository:{repository}:pull",
            )
        except RegistryError as exc:
            if exc.status != 405:
                raise
            _, response_headers, _ = self.request(
                "GET",
                path,
                headers=headers,
                scope=f"repository:{repository}:pull",
            )

        digest = response_headers.get("Docker-Content-Digest")
        if not digest:
            raise RegistryError(502, "远端仓库没有返回 Docker-Content-Digest，无法安全删除")
        return digest

    def delete_tag(self, repository: str, tag: str) -> dict[str, Any]:
        digest = self.manifest_digest(repository, tag)
        path = f"/v2/{quote_repo_path(repository)}/manifests/{quote_reference(digest)}"
        try:
            status, _, _ = self.request(
                "DELETE",
                path,
                scope=f"repository:{repository}:delete",
            )
            delete_mode = "digest"
        except RegistryError as exc:
            if exc.status not in {400, 405}:
                raise
            # 部分兼容 OCI Distribution 的仓库支持按 tag 删除，作为 digest 删除失败后的兜底。
            tag_path = f"/v2/{quote_repo_path(repository)}/manifests/{quote_reference(tag)}"
            status, _, _ = self.request(
                "DELETE",
                tag_path,
                scope=f"repository:{repository}:delete",
            )
            delete_mode = "tag"

        return {
            "deleted": 200 <= status < 300,
            "status": status,
            "repository": repository,
            "tag": tag,
            "digest": digest,
            "mode": delete_mode,
        }

    def _created_at_from_manifest(self, repository: str, manifest_data: Any) -> str | None:
        if not isinstance(manifest_data, dict):
            return None

        schema_version = manifest_data.get("schemaVersion")
        if schema_version == 1:
            return self._created_at_from_schema1_history(manifest_data)

        config = manifest_data.get("config")
        config_digest = config.get("digest") if isinstance(config, dict) else ""
        if not config_digest:
            return None

        try:
            _, _, body = self.request(
                "GET",
                f"/v2/{quote_repo_path(repository)}/blobs/{quote_reference(str(config_digest))}",
                headers={"Accept": CONFIG_ACCEPT},
                scope=f"repository:{repository}:pull",
            )
        except RegistryError:
            return None
        config_data = self._json_body(body)
        if not isinstance(config_data, dict):
            return None

        created = config_data.get("created")
        if isinstance(created, str) and created:
            return created

        history = config_data.get("history")
        if isinstance(history, list):
            created_values = [
                item.get("created")
                for item in history
                if isinstance(item, dict) and isinstance(item.get("created"), str)
            ]
            if created_values:
                return sorted(created_values)[-1]

        return None

    def _created_at_from_schema1_history(self, manifest_data: dict[str, Any]) -> str | None:
        history = manifest_data.get("history")
        if not isinstance(history, list):
            return None
        created_values: list[str] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            raw = item.get("v1Compatibility")
            if not isinstance(raw, str):
                continue
            try:
                compatibility = json.loads(raw)
            except json.JSONDecodeError:
                continue
            created = compatibility.get("created") if isinstance(compatibility, dict) else None
            if isinstance(created, str) and created:
                created_values.append(created)
        return sorted(created_values)[-1] if created_values else None

    def _image_size_from_manifest(self, manifest_data: Any) -> int | None:
        if not isinstance(manifest_data, dict):
            return None

        # Schema v2 / OCI image manifest 的 config 和 layers descriptor 带 size，可近似表示该 tag 对应镜像大小。
        size_values: list[int] = []
        config = manifest_data.get("config")
        if isinstance(config, dict):
            self._append_descriptor_size(size_values, config)

        layers = manifest_data.get("layers")
        if isinstance(layers, list):
            for layer in layers:
                if isinstance(layer, dict):
                    self._append_descriptor_size(size_values, layer)

        if size_values:
            return sum(size_values)

        # 多架构 manifest list / OCI index 没有单一镜像大小；这里先给出 descriptor 总大小兜底，避免页面完全空白。
        manifests = manifest_data.get("manifests")
        if isinstance(manifests, list):
            for child_manifest in manifests:
                if isinstance(child_manifest, dict):
                    self._append_descriptor_size(size_values, child_manifest)

        return sum(size_values) if size_values else None

    def _append_descriptor_size(self, size_values: list[int], descriptor: dict[str, Any]) -> None:
        raw_size = descriptor.get("size")
        if isinstance(raw_size, bool):
            return
        try:
            size = int(raw_size)
        except (TypeError, ValueError):
            return
        if size > 0:
            size_values.append(size)

    def _format_size(self, size_bytes: int | None) -> str:
        if not size_bytes:
            return ""
        mb = size_bytes / 1024 / 1024
        if mb < 1024:
            return f"{mb:.2f} MB"
        return f"{mb / 1024:.2f} GB"

    def _manifest_media_type(self, manifest_data: Any) -> str | None:
        if isinstance(manifest_data, dict) and isinstance(manifest_data.get("mediaType"), str):
            return manifest_data["mediaType"]
        return None

    def _media_type(self, value: str | None) -> str | None:
        if not value:
            return None
        return value.split(";", 1)[0].strip() or None

    def request(
        self,
        method: str,
        path: str,
        query: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        scope: str | None = None,
    ) -> tuple[int, Any, bytes]:
        request_scope = scope or ""
        last_auth_header = ""
        for _ in range(3):
            url = self._url(path, query)
            request_headers = {
                "User-Agent": "docker-remote-manage/0.1",
            }
            request_headers.update(headers or {})
            auth_header = self._auth_header(request_scope)
            if auth_header:
                request_headers["Authorization"] = auth_header

            request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout, context=self._ssl_context()) as response:
                    return response.status, response.headers, response.read()
            except urllib.error.HTTPError as exc:
                challenge = parse_www_authenticate(exc.headers.get("WWW-Authenticate"))
                challenge_key = exc.headers.get("WWW-Authenticate") or ""
                if exc.code == 401 and challenge and challenge[0] == "bearer" and challenge_key != last_auth_header:
                    last_auth_header = challenge_key
                    request_scope = self._fetch_bearer_token(challenge[1], request_scope)
                    continue
                raise self._registry_error(exc) from exc
            except urllib.error.URLError as exc:
                raise RegistryError(502, "无法连接远端仓库", str(exc.reason)) from exc

        raise RegistryError(401, "认证失败或当前 token 无法访问该资源")

    def _url(self, path: str, query: dict[str, str] | None = None) -> str:
        base = f"{self.base_url.rstrip('/')}/"
        url = urllib.parse.urljoin(base, path.lstrip("/"))
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        return url

    def _ssl_context(self) -> ssl.SSLContext | None:
        if self.verify_tls:
            return None
        return ssl._create_unverified_context()

    def _basic_header(self) -> str | None:
        if not (self.username or self.password):
            return None
        raw = f"{self.username}:{self.password}".encode("utf-8")
        return f"Basic {base64.b64encode(raw).decode('ascii')}"

    def _auth_header(self, scope: str) -> str | None:
        cached = self._tokens.get(scope)
        if cached and cached[1] > time.time():
            return f"Bearer {cached[0]}"
        return self._basic_header()

    def _fetch_bearer_token(self, params: dict[str, str], scope_hint: str) -> str:
        realm = params.get("realm")
        if not realm:
            raise RegistryError(401, "远端仓库返回了无效的 Bearer 认证信息")

        scope = params.get("scope") or scope_hint or ""
        query: dict[str, str] = {}
        if params.get("service"):
            query["service"] = params["service"]
        if scope:
            query["scope"] = scope

        headers: dict[str, str] = {"Accept": "application/json"}
        basic = self._basic_header()
        if basic:
            headers["Authorization"] = basic

        token_errors: list[RegistryError] = []
        for request in self._token_requests(realm, query, headers):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout, context=self._ssl_context()) as response:
                    data = self._json_body(response.read())
            except urllib.error.HTTPError as exc:
                token_errors.append(self._registry_error(exc))
                continue
            except urllib.error.URLError as exc:
                token_errors.append(RegistryError(502, "无法连接 token 服务", str(exc.reason)))
                continue

            token = ""
            if isinstance(data, dict):
                token = str(data.get("token") or data.get("access_token") or "")
            if not token:
                token_errors.append(RegistryError(401, "token 服务没有返回可用 token"))
                continue

            expires_in = 300
            if isinstance(data, dict):
                try:
                    expires_in = int(data.get("expires_in") or expires_in)
                except (TypeError, ValueError):
                    expires_in = 300

            self._tokens[scope] = (token, time.time() + max(expires_in - 30, 30))
            return scope

        preferred_error = (
            next((error for error in token_errors if error.status in {401, 403}), None)
            or next((error for error in token_errors if error.status >= 500), None)
            or (token_errors[-1] if token_errors else None)
        )
        if preferred_error:
            raise preferred_error
        raise RegistryError(401, "token 服务认证失败")

    def _token_requests(
        self,
        realm: str,
        query: dict[str, str],
        headers: dict[str, str],
    ) -> list[urllib.request.Request]:
        # 部分私有仓库的 token 服务要求 account 参数；密码仍通过 Basic 头传递，不放入 URL。
        requests = [
            urllib.request.Request(self._with_query(realm, query), headers=headers, method="GET"),
        ]
        if self.username:
            account_query = {**query, "account": self.username}
            requests.append(urllib.request.Request(self._with_query(realm, account_query), headers=headers, method="GET"))
        return requests

    def _with_query(self, url: str, query: dict[str, str]) -> str:
        if not query:
            return url
        separator = "&" if urllib.parse.urlparse(url).query else "?"
        return f"{url}{separator}{urllib.parse.urlencode(query)}"

    def _registry_error(self, exc: urllib.error.HTTPError) -> RegistryError:
        body = exc.read()
        message, detail = docker_error_message(exc.code, exc.reason, body)
        return RegistryError(exc.code, message, detail)

    def _json_body(self, body: bytes) -> Any:
        if not body:
            return {}
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RegistryError(502, "远端仓库返回了非 JSON 数据", body.decode("utf-8", errors="replace")[:1000]) from exc


class AppHandler(BaseHTTPRequestHandler):
    server_version = "DockerRemoteManage/0.1"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._serve_static("index.html")
            return
        if parsed.path.startswith("/static/"):
            self._serve_static(parsed.path.removeprefix("/static/"))
            return
        if parsed.path.startswith("/api/"):
            self._handle_api("GET", parsed)
            return
        self._send_json(404, {"message": "页面不存在"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api("POST", parsed)
            return
        self._send_json(404, {"message": "接口不存在"})

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api("DELETE", parsed)
            return
        self._send_json(404, {"message": "接口不存在"})

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _handle_api(self, method: str, parsed: urllib.parse.ParseResult) -> None:
        try:
            path = parsed.path
            params = urllib.parse.parse_qs(parsed.query)

            if method == "POST" and path == "/api/login":
                self._api_login()
                return
            if method == "POST" and path == "/api/logout":
                self._api_logout()
                return
            if method == "GET" and path == "/api/me":
                self._api_me()
                return

            client = self._require_client()
            if method == "POST" and path == "/api/scope":
                self._api_scope(client)
                return

            if method == "GET" and path == "/api/catalog":
                limit = self._limit(params)
                last = self._first_param(params, "last")
                self._send_json(200, client.catalog(limit, last))
                return

            repository_storage_match = re.fullmatch(r"/api/repositories/([^/]+)/storage", path)
            if method == "GET" and repository_storage_match:
                repository = urllib.parse.unquote(repository_storage_match.group(1))
                self._send_json(200, client.storage_usage(repository=repository, **self._storage_options(params)))
                return

            tag_count_match = re.fullmatch(r"/api/repositories/([^/]+)/tags/count", path)
            if method == "GET" and tag_count_match:
                repository = urllib.parse.unquote(tag_count_match.group(1))
                max_pages = self._max_pages(params)
                self._send_json(200, client.tag_count(repository, max_pages))
                return

            manifest_match = re.fullmatch(r"/api/repositories/([^/]+)/tags/([^/]+)/manifest", path)
            if method == "GET" and manifest_match:
                repository = urllib.parse.unquote(manifest_match.group(1))
                tag = urllib.parse.unquote(manifest_match.group(2))
                self._send_json(200, client.manifest(repository, tag))
                return

            tags_match = re.fullmatch(r"/api/repositories/([^/]+)/tags", path)
            if method == "GET" and tags_match:
                repository = urllib.parse.unquote(tags_match.group(1))
                limit = self._limit(params)
                last = self._first_param(params, "last")
                self._send_json(200, client.tags(repository, limit, last))
                return

            delete_match = re.fullmatch(r"/api/repositories/([^/]+)/tags/([^/]+)", path)
            if method == "DELETE" and delete_match:
                repository = urllib.parse.unquote(delete_match.group(1))
                tag = urllib.parse.unquote(delete_match.group(2))
                self._send_json(200, client.delete_tag(repository, tag))
                return

            self._send_json(404, {"message": "接口不存在"})
        except RegistryError as exc:
            status = exc.status if 400 <= exc.status <= 599 else 502
            self._send_json(status, exc.to_payload())
        except ValueError as exc:
            self._send_json(400, {"message": str(exc)})
        except Exception as exc:  # noqa: BLE001 - 顶层 HTTP 边界需要兜底，避免进程直接中断。
            self._send_json(500, {"message": "服务内部错误", "detail": str(exc)})

    def _api_login(self) -> None:
        data = self._json_request()
        client = RegistryClient(
            str(data.get("registry") or ""),
            str(data.get("username") or ""),
            str(data.get("password") or ""),
            verify_tls=not bool(data.get("insecure")),
            repository_prefix=str(data.get("repositoryPrefix") or ""),
        )
        ping = client.ping()

        self._cleanup_sessions()
        session_id = secrets.token_urlsafe(32)
        SESSIONS[session_id] = {
            "client": client,
            "created_at": time.time(),
            "last_seen": time.time(),
        }
        cookie_value = f"{SESSION_COOKIE}={session_id}; HttpOnly; SameSite=Lax; Path=/"
        self._send_json(200, {"authenticated": True, "connection": client.public_info(), "ping": ping}, cookie_value)

    def _api_scope(self, client: RegistryClient) -> None:
        data = self._json_request()
        # 只变更当前 session 的仓库前缀，不重新认证；下一次 catalog 查询会按新前缀过滤。
        client.repository_prefix = normalize_repository_prefix(str(data.get("repositoryPrefix") or ""))
        client._tokens.clear()
        self._send_json(200, {"connection": client.public_info()})

    def _api_logout(self) -> None:
        session_id = self._session_id()
        if session_id:
            SESSIONS.pop(session_id, None)
        expired = f"{SESSION_COOKIE}=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/"
        self._send_json(200, {"authenticated": False}, expired)

    def _api_me(self) -> None:
        session = self._session()
        if not session:
            self._send_json(200, {"authenticated": False})
            return
        client: RegistryClient = session["client"]
        self._send_json(200, {"authenticated": True, "connection": client.public_info()})

    def _require_client(self) -> RegistryClient:
        session = self._session()
        if not session:
            raise RegistryError(401, "请先登录镜像仓库")
        return session["client"]

    def _session(self) -> dict[str, Any] | None:
        session_id = self._session_id()
        if not session_id:
            return None
        session = SESSIONS.get(session_id)
        if not session:
            return None
        if time.time() - float(session.get("last_seen", 0)) > SESSION_TTL_SECONDS:
            SESSIONS.pop(session_id, None)
            return None
        session["last_seen"] = time.time()
        return session

    def _session_id(self) -> str | None:
        header = self.headers.get("Cookie")
        if not header:
            return None
        parsed = cookies.SimpleCookie(header)
        morsel = parsed.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def _cleanup_sessions(self) -> None:
        now = time.time()
        expired = [
            session_id
            for session_id, session in SESSIONS.items()
            if now - float(session.get("last_seen", 0)) > SESSION_TTL_SECONDS
        ]
        for session_id in expired:
            SESSIONS.pop(session_id, None)

    def _json_request(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length > 1024 * 1024:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("请求体不是有效 JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def _serve_static(self, raw_path: str) -> None:
        requested = (STATIC_DIR / raw_path).resolve()
        if not requested.is_file() or STATIC_DIR.resolve() not in requested.parents:
            self._send_json(404, {"message": "静态资源不存在"})
            return

        content_type = mimetypes.guess_type(str(requested))[0] or "application/octet-stream"
        content = requested.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, status: int, payload: dict[str, Any], cookie_value: str | None = None) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        if cookie_value:
            self.send_header("Set-Cookie", cookie_value)
        self.end_headers()
        self.wfile.write(content)

    def _limit(self, params: dict[str, list[str]]) -> int:
        raw_value = self._first_param(params, "limit") or "100"
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ValueError("limit 必须是数字") from exc
        return max(1, min(value, 500))

    def _max_pages(self, params: dict[str, list[str]]) -> int:
        raw_value = self._first_param(params, "maxPages") or "20"
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ValueError("maxPages 必须是数字") from exc
        return max(1, min(value, 200))

    def _storage_options(self, params: dict[str, list[str]]) -> dict[str, int]:
        return {
            "max_tags": self._bounded_int(params, "maxTags", 1000, 5000),
            "max_tag_pages": self._bounded_int(params, "maxTagPages", 20, 200),
        }

    def _bounded_int(self, params: dict[str, list[str]], name: str, default: int, max_value: int) -> int:
        raw_value = self._first_param(params, name) or str(default)
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ValueError(f"{name} 必须是数字") from exc
        return max(1, min(value, max_value))

    def _first_param(self, params: dict[str, list[str]], name: str) -> str | None:
        values = params.get(name)
        if not values:
            return None
        return values[0] or None


def main() -> None:
    parser = argparse.ArgumentParser(description="Docker remote registry web manager")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP listen host")
    parser.add_argument("--port", default=8765, type=int, help="HTTP listen port")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    print(f"docker_remote_manage running at http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

"""Case documents in their own S3 bucket (ADR 0021), on the deployment's client.

Implements `CaseDocumentStoragePort` (the handlers) and
`CaseDocumentObjectListingPort` (the orphan sweep). The client is built once
by each composition root (`dw_api`, `dw_worker`) and handed in; this class
only knows the bucket, which is `case_documents_bucket` in both processes'
settings. Its own bucket, not the feedback one or the knowledge one: a case
document has its own lifecycle and its own offboarding prefix.

The MinIO SDK is blocking, so every call runs in a worker thread. A write
creates the bucket on first use, as `MinioAttachmentStorage` does.

A failure names the object key in the log and never in the error: the API
serialises an error's details into its response, and the key is a storage
path that stays on the server.
"""

from __future__ import annotations

import asyncio
import io
import itertools
import logging
from dataclasses import dataclass

from minio import Minio
from minio.error import MinioException, S3Error

from dw_kernel.errors import InfrastructureError, NotFoundError
from dw_supply_chain.application.ports import StoredObject

logger = logging.getLogger(__name__)

_MISSING = frozenset({"NoSuchKey", "NoSuchBucket"})


@dataclass
class MinioCaseDocumentStorage:
    client: Minio
    bucket: str

    def _put_sync(self, key: str, data: bytes, content_type: str) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)
        self.client.put_object(
            self.bucket, key, io.BytesIO(data), length=len(data), content_type=content_type
        )

    def _get_sync(self, key: str) -> bytes:
        response = self.client.get_object(self.bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def _list_sync(self, prefix: str, start_after: str | None, limit: int) -> list[StoredObject]:
        listed = self.client.list_objects(
            self.bucket, prefix=prefix, recursive=True, start_after=start_after
        )
        return [
            StoredObject(key=obj.object_name, last_modified=obj.last_modified)
            for obj in itertools.islice(listed, limit)
            if obj.object_name is not None and obj.last_modified is not None
        ]

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            await asyncio.to_thread(self._put_sync, key, data, content_type)
        except MinioException as exc:
            logger.warning("case document write failed: %s", key)
            raise InfrastructureError("document storage write failed") from exc

    async def get(self, key: str) -> bytes:
        try:
            return await asyncio.to_thread(self._get_sync, key)
        except S3Error as exc:
            logger.warning("case document read failed (%s): %s", exc.code, key)
            if exc.code in _MISSING:
                raise NotFoundError("document content not found") from exc
            raise InfrastructureError("document storage read failed") from exc
        except MinioException as exc:
            logger.warning("case document read failed: %s", key)
            raise InfrastructureError("document storage read failed") from exc

    async def delete(self, key: str) -> None:
        try:
            await asyncio.to_thread(self.client.remove_object, self.bucket, key)
        except MinioException as exc:
            logger.warning("case document delete failed: %s", key)
            raise InfrastructureError("document storage delete failed") from exc

    async def list_after(
        self, prefix: str, *, start_after: str | None, limit: int
    ) -> list[StoredObject]:
        try:
            return await asyncio.to_thread(self._list_sync, prefix, start_after, limit)
        except S3Error as exc:
            if exc.code == "NoSuchBucket":
                return []  # nothing uploaded yet
            raise InfrastructureError(
                "document storage list failed", details={"prefix": prefix}
            ) from exc
        except MinioException as exc:
            raise InfrastructureError(
                "document storage list failed", details={"prefix": prefix}
            ) from exc

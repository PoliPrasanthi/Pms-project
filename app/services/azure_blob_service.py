from azure.storage.blob import BlobServiceClient, ContentSettings
from app.core.config import settings
from uuid import uuid4
from os.path import splitext
from typing import BinaryIO, Optional
import logging

logger = logging.getLogger("app.azure_blob")


class AzureBlobService:
    def __init__(self):
        self.connection_string = settings.AZURE_STORAGE_CONNECTION_STRING
        self.container_name = settings.AZURE_STORAGE_CONTAINER_NAME
        self._blob_service_client = None
        self._container_client = None

    def _get_container_client(self):
        """Lazily initialise the client on first use so startup never fails."""
        if self._container_client is not None:
            return self._container_client

        if not self.connection_string or not self.container_name:
            raise Exception(
                "Azure Blob Storage is not configured: "
                "AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_CONTAINER_NAME is missing."
            )

        self._blob_service_client = BlobServiceClient.from_connection_string(
            self.connection_string
        )
        self._container_client = self._blob_service_client.get_container_client(
            self.container_name
        )

        try:
            if not self._container_client.exists():
                self._container_client.create_container()
        except Exception as e:
            logger.warning("Could not verify/create blob container: %s", e)

        return self._container_client

    def upload_file(
        self, file_stream: BinaryIO, filename: str, content_type: str
    ) -> Optional[str]:
        container_client = self._get_container_client()

        file_ext = splitext(filename)[1]
        blob_name = f"{uuid4()}{file_ext}"

        blob_client = container_client.get_blob_client(blob_name)
        content_settings = ContentSettings(content_type=content_type)

        try:
            blob_client.upload_blob(
                file_stream, content_settings=content_settings, overwrite=True
            )
        except Exception as e:
            logger.exception("Azure Blob upload failed for %s", filename)
            raise Exception(f"Upload to Azure Blob Storage failed: {e}") from e

        return blob_name

    def download_file(self, blob_name: str) -> BinaryIO:
        container_client = self._get_container_client()
        blob_client = container_client.get_blob_client(blob_name)
        return blob_client.download_blob().chunks()

    def get_blob_properties(self, blob_name: str):
        container_client = self._get_container_client()
        blob_client = container_client.get_blob_client(blob_name)
        return blob_client.get_blob_properties()

    def delete_file(self, blob_name: str) -> bool:
        try:
            container_client = self._get_container_client()
            blob_client = container_client.get_blob_client(blob_name)
            if blob_client.exists():
                blob_client.delete_blob()
            return True
        except Exception:
            logger.exception("Azure Blob delete failed for %s", blob_name)
            return False


azure_blob_service = AzureBlobService()

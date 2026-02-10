from abc import ABC, abstractmethod
from typing import Optional, Tuple, List


class CloudProvider(ABC):
    """Abstract base class for cloud storage providers."""

    @abstractmethod
    def get_prompt_prefix(self) -> str:
        """Return the string prefix for the prompt (e.g., 's3://bucket/')."""
        pass

    @abstractmethod
    def head_bucket(self):
        """Check if the bucket exists and is accessible."""
        pass

    @abstractmethod
    def list_objects(
        self,
        prefix: str,
        sort_key: str = 'name',
        limit: Optional[int] = None,
        next_token: Optional[str] = None,
    ) -> Tuple[List[str], List[dict], Optional[str]]:
        """List directories (prefixes) and files (objects) under a given prefix."""
        pass

    @abstractmethod
    def resolve_path(self, current_prefix: str, input_path: str, is_directory: bool = False) -> str:
        """Resolve an input path relative to the current prefix for this provider."""
        pass

    @abstractmethod
    def get_object(self, key: str) -> bytes:
        """Get the content of an object as bytes."""
        pass

    @abstractmethod
    def download_file(self, key: str, local_path: str):
        """Download an object to a local file path."""
        pass

    @abstractmethod
    def upload_file(self, local_path: str, key: str):
        """Upload a local file to a specific object key."""
        pass

    @abstractmethod
    def read_object_range(self, key: str, size: int) -> bytes:
        """Read the first 'size' bytes of an object."""
        pass

    @abstractmethod
    def get_object_metadata(self, key: str) -> dict:
        """Get metadata for an object (size, last_modified, content_type)."""
        pass

    @abstractmethod
    def get_bucket_stats(self) -> dict:
        """Get basic statistics about the bucket/container."""
        pass

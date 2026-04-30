from abc import ABC, abstractmethod
from typing import Any, Optional


class BaseCloudProvider(ABC):
    """Abstract interface all cloud providers must implement.

    Implement this to support AWS, GCP, Azure, or local Floci.
    """

    # --- Event Streaming ---

    @abstractmethod
    def publish_event(self, stream: str, data: dict, partition_key: str) -> None:
        """Publish a single event to a stream (Kinesis, Pub/Sub, Event Hub)."""

    @abstractmethod
    def publish_batch(self, stream: str, records: list[dict]) -> None:
        """Publish multiple events in a single batch."""

    # --- Queue ---

    @abstractmethod
    def enqueue(self, queue: str, message: dict, delay_seconds: int = 0) -> str:
        """Send a message to a queue. Returns message ID."""

    @abstractmethod
    def dequeue(self, queue: str, max_messages: int = 10) -> list[dict]:
        """Poll messages from a queue."""

    @abstractmethod
    def delete_message(self, queue: str, receipt: str) -> None:
        """Acknowledge and delete a processed message."""

    # --- Key-Value / Document Store ---

    @abstractmethod
    def put_item(self, table: str, item: dict) -> None:
        """Write an item to a document store (DynamoDB, Firestore, CosmosDB)."""

    @abstractmethod
    def get_item(self, table: str, key: dict) -> Optional[dict]:
        """Read an item by primary key."""

    @abstractmethod
    def update_item(self, table: str, key: dict, updates: dict) -> None:
        """Partial update an existing item."""

    @abstractmethod
    def query_items(self, table: str, index: str, key_condition: dict) -> list[dict]:
        """Query items using a secondary index."""

    # --- Object Storage ---

    @abstractmethod
    def upload_file(self, bucket: str, key: str, data: bytes) -> None:
        """Upload bytes to object storage."""

    @abstractmethod
    def download_file(self, bucket: str, key: str) -> bytes:
        """Download an object from storage."""

    @abstractmethod
    def list_objects(self, bucket: str, prefix: str = "") -> list[str]:
        """List object keys under a prefix."""

    # --- Secrets ---

    @abstractmethod
    def get_secret(self, name: str) -> str:
        """Retrieve a secret value by name."""

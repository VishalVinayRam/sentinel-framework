import json
from typing import Optional

from sentinel.providers.base.cloud import BaseCloudProvider


class GCPProvider(BaseCloudProvider):
    """Google Cloud Platform provider.

    Requires: google-cloud-pubsub, google-cloud-firestore,
              google-cloud-storage, google-cloud-secret-manager
    """

    def __init__(self, project_id: str):
        self._project = project_id

    def _pubsub(self):
        from google.cloud import pubsub_v1
        return pubsub_v1.PublisherClient(), pubsub_v1.SubscriberClient()

    def _firestore(self):
        from google.cloud import firestore
        return firestore.Client(project=self._project)

    def _storage(self):
        from google.cloud import storage
        return storage.Client(project=self._project)

    def _secret_manager(self):
        from google.cloud import secretmanager
        return secretmanager.SecretManagerServiceClient()

    # --- Event Streaming (Pub/Sub) ---

    def publish_event(self, stream: str, data: dict, partition_key: str) -> None:
        publisher, _ = self._pubsub()
        topic_path = publisher.topic_path(self._project, stream)
        publisher.publish(topic_path, json.dumps(data).encode(), partition_key=partition_key)

    def publish_batch(self, stream: str, records: list[dict]) -> None:
        publisher, _ = self._pubsub()
        topic_path = publisher.topic_path(self._project, stream)
        futures = [publisher.publish(topic_path, json.dumps(r).encode()) for r in records]
        for f in futures:
            f.result()

    # --- Queue (Pub/Sub subscriptions as queues) ---

    def enqueue(self, queue: str, message: dict, delay_seconds: int = 0) -> str:
        publisher, _ = self._pubsub()
        topic_path = publisher.topic_path(self._project, queue)
        future = publisher.publish(topic_path, json.dumps(message).encode())
        return future.result()

    def dequeue(self, queue: str, max_messages: int = 10) -> list[dict]:
        _, subscriber = self._pubsub()
        sub_path = subscriber.subscription_path(self._project, queue)
        resp = subscriber.pull(request={"subscription": sub_path, "max_messages": max_messages})
        return [
            {"Body": json.loads(m.message.data), "ReceiptHandle": m.ack_id}
            for m in resp.received_messages
        ]

    def delete_message(self, queue: str, receipt: str) -> None:
        _, subscriber = self._pubsub()
        sub_path = subscriber.subscription_path(self._project, queue)
        subscriber.acknowledge(request={"subscription": sub_path, "ack_ids": [receipt]})

    # --- Document Store (Firestore) ---

    def put_item(self, table: str, item: dict) -> None:
        doc_id = item.get("alert_id") or item.get("pr_id") or item.get("aggregate_id")
        self._firestore().collection(table).document(doc_id).set(item)

    def get_item(self, table: str, key: dict) -> Optional[dict]:
        doc_id = list(key.values())[0]
        doc = self._firestore().collection(table).document(doc_id).get()
        return doc.to_dict() if doc.exists else None

    def update_item(self, table: str, key: dict, updates: dict) -> None:
        doc_id = list(key.values())[0]
        self._firestore().collection(table).document(doc_id).update(updates)

    def query_items(self, table: str, index: str, key_condition: dict) -> list[dict]:
        ref = self._firestore().collection(table)
        for field, value in key_condition.items():
            ref = ref.where(field, "==", value)
        return [d.to_dict() for d in ref.stream()]

    # --- Object Storage (GCS) ---

    def upload_file(self, bucket: str, key: str, data: bytes) -> None:
        self._storage().bucket(bucket).blob(key).upload_from_string(data)

    def download_file(self, bucket: str, key: str) -> bytes:
        return self._storage().bucket(bucket).blob(key).download_as_bytes()

    def list_objects(self, bucket: str, prefix: str = "") -> list[str]:
        return [b.name for b in self._storage().list_blobs(bucket, prefix=prefix)]

    # --- Secrets ---

    def get_secret(self, name: str) -> str:
        client = self._secret_manager()
        secret_path = f"projects/{self._project}/secrets/{name}/versions/latest"
        return client.access_secret_version(request={"name": secret_path}).payload.data.decode()

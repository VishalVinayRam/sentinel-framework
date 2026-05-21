"""Ships structured JSON logs to Kinesis sentinel-logs in background batches."""
import json
import queue
import threading
import time
import uuid
from datetime import datetime, timezone

import boto3

FLOCI = dict(
    endpoint_url="http://localhost:4566",
    region_name="us-east-1",
    aws_access_key_id="test",
    aws_secret_access_key="test",
)
STREAM = "sentinel-logs"


class LogShipper:
    def __init__(self, service_name: str):
        self.service = service_name
        self._q: queue.Queue = queue.Queue()
        self._client = boto3.client("kinesis", **FLOCI)
        t = threading.Thread(target=self._flush_loop, daemon=True)
        t.start()

    def info(self, msg: str, **kw):
        self._enqueue("INFO", msg, **kw)

    def warn(self, msg: str, **kw):
        self._enqueue("WARN", msg, **kw)

    def error(self, msg: str, **kw):
        self._enqueue("ERROR", msg, **kw)

    def fatal(self, msg: str, **kw):
        self._enqueue("FATAL", msg, **kw)

    def _enqueue(self, level: str, msg: str, **kw):
        entry = {
            "level": level,
            "message": msg,
            "service": self.service,
            "ts": datetime.now(timezone.utc).isoformat(),
            **kw,
        }
        print(f"[{level}] [{self.service}] {msg}", flush=True)
        self._q.put(json.dumps(entry))

    def _flush_loop(self):
        while True:
            time.sleep(4)
            self._flush()

    def _flush(self):
        logs = []
        try:
            while True:
                logs.append(self._q.get_nowait())
        except queue.Empty:
            pass
        if not logs:
            return
        batch = {
            "incident_id": str(uuid.uuid4()),
            "service_name": self.service,
            "logs": logs,
            "shipped_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._client.put_record(
                StreamName=STREAM,
                Data=json.dumps(batch),
                PartitionKey=self.service,
            )
        except Exception as e:
            print(f"[log_shipper] Kinesis flush failed: {e}", flush=True)

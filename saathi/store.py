"""Per-destination memory of what a phone number has said before.

This is the only genuinely stateful thing in the system, and it exists because
the strongest signal available is cross-call: an IVR prompt is byte-identical
on every call to that number, and a person never is. Without somewhere to keep
that between calls the signal cannot exist at all -- which is why the store is
load-bearing rather than infrastructure for its own sake.

Design rule: THE DETECTOR NEVER WAITS ON THE NETWORK. A destination's known
utterances are fetched once, when the call starts, and matched in memory for
the rest of it. Putting a round trip inside the loop that decides whether to
fetch the user would put latency on the one decision where latency is trust.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

TABLE = os.environ.get("SAATHI_TABLE", "saathi-utterances")


def utterance_key(text: str) -> str:
    from saathi.evidence.repetition import normalise
    return hashlib.sha256(" ".join(normalise(text)).encode()).hexdigest()[:32]


@runtime_checkable
class Store(Protocol):
    def known(self, destination: str) -> list[str]: ...
    def remember(self, destination: str, text: str) -> None: ...


@dataclass
class MemoryStore:
    """The default. Keeps tests offline, deterministic and free."""

    data: dict[str, dict[str, str]] = field(default_factory=dict)

    def known(self, destination: str) -> list[str]:
        return list(self.data.get(destination, {}).values())

    def remember(self, destination: str, text: str) -> None:
        self.data.setdefault(destination, {})[utterance_key(text)] = text


class DynamoStore:
    """DynamoDB, keyed by destination so one query loads a whole call's index.

    Partition on the destination and sort on a hash of the normalised
    utterance: the access pattern is 'everything this number has ever said',
    which is exactly one Query, and writes are idempotent because the same
    prompt hashes to the same key however many times it is heard.
    """

    def __init__(self, table: str = TABLE, region: str | None = None,
                 profile: str | None = None) -> None:
        import boto3
        from saathi.voice import PROFILE
        name = profile if profile is not None else (PROFILE or None)
        session = boto3.Session(profile_name=name) if name else boto3.Session()
        self.ddb = session.resource("dynamodb",
                                    region_name=region or session.region_name)
        self.table = self.ddb.Table(table)
        self.name = table

    def known(self, destination: str) -> list[str]:
        from boto3.dynamodb.conditions import Key
        out, kw = [], {"KeyConditionExpression": Key("destination").eq(destination)}
        while True:
            r = self.table.query(**kw)
            out.extend(i["text"] for i in r.get("Items", []))
            if "LastEvaluatedKey" not in r:
                return out
            kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]

    def remember(self, destination: str, text: str) -> None:
        self.table.update_item(
            Key={"destination": destination, "utterance": utterance_key(text)},
            UpdateExpression="SET #t = :t ADD times_heard :one",
            ExpressionAttributeNames={"#t": "text"},
            ExpressionAttributeValues={":t": text, ":one": 1},
        )

    def ensure_table(self) -> str:
        """Create the table if absent. Returns its status."""
        import botocore.exceptions
        try:
            self.table.load()
            return self.table.table_status
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                raise
        self.ddb.create_table(
            TableName=self.name,
            KeySchema=[{"AttributeName": "destination", "KeyType": "HASH"},
                       {"AttributeName": "utterance", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "destination", "AttributeType": "S"},
                {"AttributeName": "utterance", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
            Tags=[{"Key": "project", "Value": "saathi"}])
        self.table.wait_until_exists()
        return self.table.table_status


def default_store() -> Store:
    """DynamoDB when configured, memory otherwise.

    Falling back rather than failing is deliberate: a cold destination simply
    contributes no repetition evidence, which is the same as an unreachable
    store. The detector degrades to its other four families instead of the
    call dying because a table was missing.
    """
    if os.environ.get("SAATHI_STORE", "").lower() == "dynamodb":
        try:
            return DynamoStore()
        except Exception as e:                       # noqa: BLE001
            print(f"  [store] dynamodb unavailable, using memory: {e}")
    return MemoryStore()

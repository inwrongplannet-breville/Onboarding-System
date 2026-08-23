"""
DynamoDB access.

Two clients on purpose:

  `table`  - the resource-level API. Marshals plain Python types for you, so
             get/query/scan/update read cleanly. Used by five of the six handlers.
  `client` - the low-level API. The only one with transact_write_items, which
             create and delete need for atomicity. It speaks AttributeValue dicts,
             hence `serialize` below.

Note the client is built with boto3.client(), NOT resource.meta.client. They look
interchangeable and are not: boto3 hangs its type-transformation handlers off the
resource's client, so passing it already-serialized AttributeValues gets them
serialized a second time and the transaction fails with an opaque TypeError.

Both are built at module scope so a warm Lambda reuses the connection instead of
re-handshaking TLS on every invocation.
"""
import os

import boto3
from boto3.dynamodb.types import TypeSerializer

TABLE_NAME = os.environ['TABLE_NAME']

table = boto3.resource('dynamodb').Table(TABLE_NAME)
client = boto3.client('dynamodb')

_serializer = TypeSerializer()


def serialize(item):
    """Plain dict -> the {'S': ...} / {'BOOL': ...} form transact_write_items wants."""
    return {key: _serializer.serialize(value) for key, value in item.items()}

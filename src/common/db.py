"""
DynamoDB access.

One client, the resource-level API: it marshals plain Python types for you, so
get/scan/update/put read cleanly and the embedded checklist comes back as a plain
list of dicts rather than AttributeValue envelopes.

There used to be a second, low-level client here purely for
transact_write_items - create wrote ten items and PATCH needed a ConditionCheck
against a sibling row. An employee is one item now, so every write is a single
GetItem/PutItem/UpdateItem and there is nothing left to keep atomic across items.

Built at module scope so a warm Lambda reuses the connection instead of
re-handshaking TLS on every invocation.
"""
import os

import boto3

TABLE_NAME = os.environ['TABLE_NAME']

table = boto3.resource('dynamodb').Table(TABLE_NAME)

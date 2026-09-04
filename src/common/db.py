"""
DynamoDB access.

One client, the resource-level API: it marshals plain Python types for you, so
get/scan/update/put read cleanly and an embedded checklist comes back as a plain
list of dicts rather than AttributeValue envelopes.

Two tables now, not one - OnboardingTable and EmployeeTable - each its own
`Table` resource from its own env var. EmployeeTable holds both onboarded
employees and onboarded interns, told apart by `entityType`
(common/models.py) rather than by a table of their own - see the module
docstring in common/keys.py for why one shared key works for both.

There used to be a second, low-level client here purely for
transact_write_items - create wrote ten items and PATCH needed a
ConditionCheck against a sibling row. An employee was one item after that, so
every write was a single GetItem/PutItem/UpdateItem and there was nothing left
to keep atomic across items.

It stays that way even across two tables. Promotion, un-promotion and manager
reassignment are each a short sequence of independently callable endpoints -
see src/handlers/promote_to_employee.py and its siblings - rather than one
cross-table transaction, so there is still nothing here to transact. No
low-level client, no TypeSerializer.

Built at module scope so a warm Lambda reuses the connection instead of
re-handshaking TLS on every invocation.

TABLE_NAME does not exist any more. That is deliberate: a handler still importing
`table` (the pre-split name) gets an ImportError at cold start instead of quietly
reading or writing the wrong table, which is a Scan of the wrong records or - far
worse - a write nobody notices landed in the wrong place.
"""
import os

import boto3

ONBOARDING_TABLE_NAME = os.environ['ONBOARDING_TABLE_NAME']
EMPLOYEE_TABLE_NAME = os.environ['EMPLOYEE_TABLE_NAME']

_resource = boto3.resource('dynamodb')

onboarding_table = _resource.Table(ONBOARDING_TABLE_NAME)
employee_table = _resource.Table(EMPLOYEE_TABLE_NAME)

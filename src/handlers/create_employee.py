"""
POST /employees - brief task 2.

Writes 9 items (1 profile + 8 checklist rows) in a single TransactWriteItems.

Why a transaction and not BatchWriteItem: batch is not atomic. A partial success
returns UnprocessedItems and leaves an employee holding, say, 5 of 8 checklist
rows - a state no handler downstream knows how to reason about. Batch also can't
carry condition expressions. 10 items is comfortably inside the 100-item limit.

The tenth item is the email uniqueness guard. It rides along in the same
transaction on purpose: check-then-write in two calls is a race two concurrent
POSTs will win, and a duplicate work email is exactly the kind of thing nobody
notices until payroll does.
"""
from datetime import datetime, timezone
from uuid import uuid4

from botocore.exceptions import ClientError

from common import responses
from common.db import TABLE_NAME, client, serialize
from common.handler import api_handler, failed_at, is_transaction_cancelled, parse_body
from common.keys import PROFILE_SK, chk_sk, email_pk, EMAIL_SK, pk
from common.models import (
    new_checklist_items,
    pick_editable,
    to_api_employee,
    validate_employee,
)


@api_handler
def lambda_handler(event, context):
    body = parse_body(event)
    values = pick_editable(body)

    errors = validate_employee(values)
    if errors:
        return responses.bad_request('Employee details are not valid.', errors)

    employee_id = str(uuid4())
    partition = pk(employee_id)
    now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')

    profile = dict(values)
    profile.update({
        'PK': partition,
        'SK': PROFILE_SK,
        'entityType': 'Employee',
        'employeeId': employee_id,
        'createdAt': now,
        'updatedAt': now,
    })

    checklist_items = []
    for item in new_checklist_items():
        row = dict(item)
        row.update({
            'PK': partition,
            'SK': chk_sk(item['itemId']),
            'entityType': 'ChecklistItem',
            'updatedAt': now,
        })
        checklist_items.append(row)

    all_items = [profile] + checklist_items

    guard = {
        'PK': email_pk(values['email']),
        'SK': EMAIL_SK,
        'entityType': 'EmailGuard',
        'employeeId': employee_id,
        'email': values['email'],
        'createdAt': now,
    }

    # Order matters below: the profile is item 0 and the guard is last. That
    # position is how a UUID collision is told apart from a duplicate email when
    # the transaction comes back cancelled.
    transact_items = [
        {
            'Put': {
                'TableName': TABLE_NAME,
                'Item': serialize(item),
                # Guards against a UUID collision. Astronomically unlikely,
                # but the alternative is a silent overwrite of a real person.
                'ConditionExpression': 'attribute_not_exists(PK)',
            }
        }
        for item in all_items
    ]
    transact_items.append({
        'Put': {
            'TableName': TABLE_NAME,
            'Item': serialize(guard),
            'ConditionExpression': 'attribute_not_exists(PK)',
        }
    })

    try:
        client.transact_write_items(TransactItems=transact_items)
    except ClientError as error:
        if not is_transaction_cancelled(error):
            raise
        # Read which condition actually fired rather than assuming. A cancelled
        # transaction is also how throughput limits and item-size problems
        # arrive, and reporting one of those as "that email is taken" sends the
        # reader looking for a duplicate that does not exist.
        if failed_at(error, len(transact_items) - 1):
            return responses.conflict(
                'That work email is already on another employee.',
                {'email': 'Already in use by another employee.'},
            )
        if failed_at(error, 0):
            return responses.conflict('That employee id already exists.')
        raise

    employee = to_api_employee(all_items)
    return responses.created(employee, '/employees/' + employee_id)

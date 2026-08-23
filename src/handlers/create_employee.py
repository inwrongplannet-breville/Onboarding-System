"""
POST /employees - brief task 2.

Writes 9 items (1 profile + 8 checklist rows) in a single TransactWriteItems.

Why a transaction and not BatchWriteItem: batch is not atomic. A partial success
returns UnprocessedItems and leaves an employee holding, say, 5 of 8 checklist
rows - a state no handler downstream knows how to reason about. Batch also can't
carry condition expressions. 9 items is comfortably inside the 100-item limit.
"""
from datetime import datetime, timezone
from uuid import uuid4

from botocore.exceptions import ClientError

from common import responses
from common.db import TABLE_NAME, client, serialize
from common.handler import api_handler, parse_body
from common.keys import PROFILE_SK, chk_sk, pk
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

    try:
        client.transact_write_items(TransactItems=[
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
        ])
    except ClientError as error:
        if error.response['Error']['Code'] == 'TransactionCanceledException':
            return responses.conflict('That employee id already exists.')
        raise

    employee = to_api_employee(all_items)
    return responses.created(employee, '/employees/' + employee_id)

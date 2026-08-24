"""
POST /employees - brief task 2.

Writes one item: the profile fields plus the eight checklist entries embedded as a
list attribute. One employee is one item, so there is nothing to keep atomic
across items and nothing to transact - a single conditional PutItem does it.

This used to be a ten-item TransactWriteItems: a profile row, eight CHK# rows and
an email uniqueness guard in its own partition. The guard is gone, and with it the
only thing enforcing one employee per work email - DynamoDB can enforce uniqueness
on a partition key and nothing else, and the partition key here is a UUID. A
duplicate work email is now possible and will not be rejected.
"""
from datetime import datetime, timezone
from uuid import uuid4

from botocore.exceptions import ClientError

from common import responses
from common.db import table
from common.handler import api_handler, is_condition_failure, parse_body
from common.keys import pk
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
    now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')

    item = dict(values)
    item.update({
        'PK': pk(employee_id),
        'entityType': 'Employee',
        'employeeId': employee_id,
        'createdAt': now,
        'updatedAt': now,
        # In CHECKLIST_TEMPLATE order, and it has to stay that way: every PATCH
        # addresses an entry as checklist[i] using CHECKLIST_INDEX, which is
        # built from that same order.
        'checklist': [dict(entry, updatedAt=now) for entry in new_checklist_items()],
    })

    try:
        table.put_item(
            Item=item,
            # Guards against a UUID collision. Astronomically unlikely, but the
            # alternative is a silent overwrite of a real person - and PutItem
            # overwrites by default, so leaving this off is not a smaller risk,
            # it is a different one.
            ConditionExpression='attribute_not_exists(PK)',
        )
    except ClientError as error:
        if is_condition_failure(error):
            return responses.conflict('That employee id already exists.')
        raise

    employee = to_api_employee(item)
    return responses.created(employee, '/employees/' + employee_id)

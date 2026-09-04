"""
POST /employees - brief task 2.

Writes one item: the profile fields plus the eight checklist entries embedded as a
list attribute. One employee is one item, so there is nothing to keep atomic
across items and nothing to transact - a single conditional PutItem does it.

The id comes from the caller now. HR types the employee number on the form and it
becomes the partition key, where it used to be a UUID minted here. That turns the
`NOT_EXISTS` condition below from a formality into the feature: it
was guarding against a UUID collision nobody would ever see, and it now enforces
one record per employee number - the only uniqueness DynamoDB can actually give
you, since it can only be had on a partition key.

So a duplicate employee number is a 409 that names the field, and the form paints
it under the input. The trade is that the id is fixed at creation and cannot be
edited afterwards; see EDITABLE_FIELDS in common/models.py.

Note what this still does *not* guarantee. The old ten-item TransactWriteItems
carried an email uniqueness guard in its own partition; that item is gone and the
key change does not bring it back. One employee per number, yes. One employee per
work email, no - two records may still share a mailbox.
"""
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from common import responses
from common.db import onboarding_table
from common.handler import api_handler, is_condition_failure, parse_body, require_official
from common.keys import KEY_ATTRIBUTE, NOT_EXISTS, pk
from common.models import (
    clean_employee_id,
    new_checklist_items,
    pick_editable,
    to_api_employee,
    validate_employee,
    validate_employee_id,
)


@api_handler
def lambda_handler(event, context):
    # Before parse_body, so a read-only caller gets 403 about their role rather
    # than 400 about a body that was never going to be written.
    require_official(event)

    body = parse_body(event)
    values = pick_editable(body)

    # Not part of pick_editable, and deliberately not: that whitelist is what PUT
    # uses too, and the id must never be reachable from an update body.
    employee_id = clean_employee_id(body.get('employeeId'))

    errors = validate_employee(values)
    id_error = validate_employee_id(employee_id)
    if id_error:
        errors['employeeId'] = id_error
    if errors:
        return responses.bad_request('Employee details are not valid.', errors)

    now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')

    item = dict(values)
    item.update({
        KEY_ATTRIBUTE: pk(employee_id),
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
        onboarding_table.put_item(
            Item=item,
            # The uniqueness guarantee, and the reason the id belongs in the key.
            # PutItem overwrites by default, so without this a second hire typed
            # in under an existing employee number would silently replace a real
            # person and their entire onboarding history.
            ConditionExpression=NOT_EXISTS,
        )
    except ClientError as error:
        if is_condition_failure(error):
            # `fields` so the message lands under the Employee ID input rather
            # than in the page-level banner - it is a problem with one thing the
            # user typed, and they need to know which.
            return responses.conflict(
                'Employee ID ' + employee_id + ' is already taken.',
                {'employeeId': 'That employee ID is already in use.'},
            )
        raise

    employee = to_api_employee(item)
    return responses.created(employee, '/employees/' + employee_id)

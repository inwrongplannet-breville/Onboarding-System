"""
PUT /employees/{id} - brief task 5.

UpdateItem on the profile row only. The checklist rows are separate items and are
not touched, which is what preserves onboarding progress across an edit.

The condition expression is load-bearing: UpdateItem *upserts* by default, so a
PUT to a deleted id would happily create a profile with no checklist behind it.

Two shapes of write, decided by whether the email changed:

  unchanged  a plain UpdateItem, exactly as before.
  changed    a transaction - put the new email guard, delete the old one, update
             the profile - so the guard can never disagree with the profile it
             describes. Doing it in three separate calls leaves a window where a
             crash strands a guard on an address nobody holds, and that address
             is then unusable forever with nothing in the table to explain why.
"""
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from common import responses
from common.db import TABLE_NAME, client, serialize, table
from common.handler import (
    api_handler,
    failed_at,
    is_condition_failure,
    is_transaction_cancelled,
    parse_body,
    path_param,
)
from common.keys import EMAIL_SK, PROFILE_SK, email_pk, pk
from common.models import EDITABLE_FIELDS, pick_editable, validate_employee
from common.repository import current_email, load_employee


def _profile_update(values, now):
    """The SET clause shared by both write paths."""
    assignments = []
    names = {}
    values_map = {}

    # Built from the whitelist, so `id`, `checklist` or anything else a caller
    # invents in the body simply never reaches the SET clause.
    for field in EDITABLE_FIELDS:
        assignments.append('#' + field + ' = :' + field)
        names['#' + field] = field
        values_map[':' + field] = values[field]

    assignments.append('#updatedAt = :updatedAt')
    names['#updatedAt'] = 'updatedAt'
    values_map[':updatedAt'] = now

    return 'SET ' + ', '.join(assignments), names, values_map


@api_handler
def lambda_handler(event, context):
    employee_id = path_param(event, 'id')
    body = parse_body(event)
    values = pick_editable(body)

    errors = validate_employee(values)
    if errors:
        return responses.bad_request('Employee details are not valid.', errors)

    now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    expression, names, values_map = _profile_update(values, now)

    existing_email = current_email(employee_id)
    if existing_email is None:
        return responses.not_found('No employee with id ' + employee_id + '.')

    moving_email = email_pk(existing_email) != email_pk(values['email'])

    if moving_email:
        conflict = _swap_email_and_update(
            employee_id, existing_email, values, now, expression, names, values_map)
        if conflict is not None:
            return conflict
    else:
        try:
            table.update_item(
                Key={'PK': pk(employee_id), 'SK': PROFILE_SK},
                UpdateExpression=expression,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values_map,
                ConditionExpression='attribute_exists(PK) AND attribute_exists(SK)',
            )
        except ClientError as error:
            if is_condition_failure(error):
                return responses.not_found('No employee with id ' + employee_id + '.')
            raise

    # Re-read so the response carries the checklist too, matching GET exactly.
    # Consistently, because an eventually consistent Query here can hand back the
    # profile as it was before the UpdateItem a moment ago.
    employee = load_employee(employee_id, consistent=True)
    if employee is None:
        return responses.not_found('No employee with id ' + employee_id + '.')
    return responses.ok(employee)


def _swap_email_and_update(employee_id, existing_email, values, now,
                           expression, names, values_map):
    """Returns an error response, or None when the write went through."""
    guard = {
        'PK': email_pk(values['email']),
        'SK': EMAIL_SK,
        'entityType': 'EmailGuard',
        'employeeId': employee_id,
        'email': values['email'],
        'createdAt': now,
    }

    transact_items = [
        {
            'Put': {
                'TableName': TABLE_NAME,
                'Item': serialize(guard),
                'ConditionExpression': 'attribute_not_exists(PK)',
            }
        },
        {
            # No condition. An employee created before guards existed has none to
            # delete, and a delete of an absent item is a no-op - which is the
            # behaviour we want rather than a failed edit.
            'Delete': {
                'TableName': TABLE_NAME,
                'Key': serialize({'PK': email_pk(existing_email), 'SK': EMAIL_SK}),
            }
        },
        {
            'Update': {
                'TableName': TABLE_NAME,
                'Key': serialize({'PK': pk(employee_id), 'SK': PROFILE_SK}),
                'UpdateExpression': expression,
                'ExpressionAttributeNames': names,
                'ExpressionAttributeValues': serialize(values_map),
                'ConditionExpression': 'attribute_exists(PK) AND attribute_exists(SK)',
            }
        },
    ]

    try:
        client.transact_write_items(TransactItems=transact_items)
    except ClientError as error:
        if not is_transaction_cancelled(error):
            raise
        if failed_at(error, 0):
            return responses.conflict(
                'That work email is already on another employee.',
                {'email': 'Already in use by another employee.'},
            )
        if failed_at(error, 2):
            # Deleted between the read above and this write.
            return responses.not_found('No employee with id ' + employee_id + '.')
        raise

    return None

"""
POST /staff/employees/{id}/interns - link one intern to their reporting manager.

Step two of the intern promote sequence (see promote_to_intern.py) and step two
of manager reassignment (see set_intern_manager.py). Idempotent: calling it
twice with the same internId leaves the list with one entry, not two, which is
what makes re-running an interrupted sequence from the start safe.

The `interns` attribute on EmployeeTable is deliberately sparse - present only
on a manager who has at least one intern, absent everywhere else. That is
enforced here with `if_not_exists`, and unlinked back to absent by
remove_manager_intern.py, never left behind as an empty list.

The condition expression does the deduplication, not a read-then-write: SET ...
list_append is paired with `NOT contains(#interns, :internId)`, so two
overlapping requests for the same intern cannot both succeed and neither can
land a duplicate - DynamoDB's own conditional write is the race-free check, not
a GetItem this handler would otherwise have to trust.

The intern lookup checks `is_intern_item`, not just that the id resolves to a
row in EmployeeTable - the manager and the intern are both rows in that same
table now, and without the check this route would happily link one employee to
another's `interns` list as if they were an intern.
"""
from botocore.exceptions import ClientError

from common import responses
from common.db import employee_table
from common.handler import (
    api_handler,
    employee_id_param,
    is_condition_failure,
    parse_body,
    require_official,
)
from common.keys import EXISTS, key
from common.models import clean_employee_id, is_intern_item, to_api_staff_employee

_INTERN_ID_REQUIRED = 'internId is required.'

_INTERN_NOT_FOUND = 'That intern could not be found on the intern dashboard.'


@api_handler
def lambda_handler(event, context):
    require_official(event)

    manager_id = employee_id_param(event)
    body = parse_body(event)
    intern_id = clean_employee_id(body.get('internId'))
    if not intern_id:
        return responses.bad_request(_INTERN_ID_REQUIRED, {'internId': _INTERN_ID_REQUIRED})

    intern_item = employee_table.get_item(Key=key(intern_id)).get('Item')
    if intern_item is None or not is_intern_item(intern_item):
        return responses.bad_request(_INTERN_NOT_FOUND, {'internId': _INTERN_NOT_FOUND})

    # :one is the list SET appends; :internId is the scalar the condition tests
    # for membership - DynamoDB will not let one ExpressionAttributeValue serve
    # both a list and a scalar shape, so they are two separate names.
    try:
        result = employee_table.update_item(
            Key=key(manager_id),
            UpdateExpression='SET #interns = list_append(if_not_exists(#interns, :empty), :one)',
            ExpressionAttributeNames={'#interns': 'interns'},
            ExpressionAttributeValues={
                ':empty': [],
                ':one': [intern_id],
                ':internId': intern_id,
            },
            ConditionExpression=(
                EXISTS + ' AND (attribute_not_exists(#interns) OR NOT contains(#interns, :internId))'
            ),
            ReturnValues='ALL_NEW',
        )
    except ClientError as error:
        if is_condition_failure(error):
            manager_item = employee_table.get_item(Key=key(manager_id)).get('Item')
            if manager_item is None:
                return responses.not_found('No employee with id ' + manager_id + '.')
            # The manager exists but already lists this intern - idempotent
            # success, not an error the caller has to special-case.
            return responses.ok(to_api_staff_employee(manager_item))
        raise

    return responses.ok(to_api_staff_employee(result['Attributes']))

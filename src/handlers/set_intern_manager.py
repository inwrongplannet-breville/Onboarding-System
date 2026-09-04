"""
PUT /staff/interns/{id}/manager - HR manually reassigns an intern's reporting
manager.

The first of a three-step sequence:

    1. PUT /staff/interns/{id}/manager                          (this file)
    2. POST /staff/employees/{newManagerId}/interns              (handlers/add_manager_intern.py)
    3. DELETE /staff/employees/{oldManagerId}/interns/{id}        (handlers/remove_manager_intern.py)

New link added before the old one is removed, on purpose: if step 3 never runs,
the intern is reachable from both managers' `interns` lists, which is a harmless
duplicate. The reverse order would risk the opposite - an intern linked to
nobody - which is the state this sequence exists to avoid.

Returns the *previous* reportingManagerId in the response body, because that is
the one thing the frontend cannot otherwise know once this call has already
overwritten it - and it needs it to call step 3 with the right manager id.

Idempotent: reassigning to the same manager they already had returns 200 with
no change, not a 409 - it is not really a conflict from the caller's side.

The target of this PUT and the new manager it names are both EmployeeTable
items, and only `entityType` tells them apart. `employee_id_param(event)`
alone would happily match an *employee's* id here too, so the intern lookup
below checks `is_intern_item` explicitly - and the new-manager lookup checks
`is_employee_item`, for the same reason promote_to_intern.py does: an intern
naming another intern as their manager is exactly the mistake sharing one
table makes newly possible.
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
from common.models import clean_employee_id, is_employee_item, is_intern_item, to_api_intern

_MANAGER_REQUIRED = 'reportingManagerId is required.'

_MANAGER_NOT_FOUND = ('That reporting manager could not be found on the employee '
                       'dashboard. They must be promoted as an employee first.')


@api_handler
def lambda_handler(event, context):
    require_official(event)

    intern_id = employee_id_param(event)
    body = parse_body(event)
    new_manager_id = clean_employee_id(body.get('reportingManagerId'))
    if not new_manager_id:
        return responses.bad_request(_MANAGER_REQUIRED, {'reportingManagerId': _MANAGER_REQUIRED})

    intern_item = employee_table.get_item(Key=key(intern_id), ConsistentRead=True).get('Item')
    if intern_item is None or not is_intern_item(intern_item):
        return responses.not_found('No intern with id ' + intern_id + '.')

    previous_manager_id = intern_item.get('reportingManagerId') or ''

    if new_manager_id == previous_manager_id:
        # Reassigning to the manager they already have - idempotent no-op.
        payload = to_api_intern(intern_item)
        payload['previousReportingManagerId'] = previous_manager_id
        return responses.ok(payload)

    new_manager = employee_table.get_item(Key=key(new_manager_id)).get('Item')
    if new_manager is None or not is_employee_item(new_manager):
        return responses.bad_request(
            _MANAGER_NOT_FOUND, {'reportingManagerId': _MANAGER_NOT_FOUND})

    try:
        result = employee_table.update_item(
            Key=key(intern_id),
            UpdateExpression='SET #reportingManagerId = :managerId',
            ExpressionAttributeNames={'#reportingManagerId': 'reportingManagerId'},
            ExpressionAttributeValues={':managerId': new_manager_id},
            ConditionExpression=EXISTS,
            ReturnValues='ALL_NEW',
        )
    except ClientError as error:
        if is_condition_failure(error):
            return responses.not_found('No intern with id ' + intern_id + '.')
        raise

    payload = to_api_intern(result['Attributes'])
    payload['previousReportingManagerId'] = previous_manager_id
    return responses.ok(payload)

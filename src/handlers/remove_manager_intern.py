"""
DELETE /staff/employees/{id}/interns/{internId} - unlink one intern from their
manager.

Used by un-promote (an intern moving back to onboarding drops the manager link
first) and by manager reassignment (the old manager's link is removed after the
new one is added - see set_intern_manager.py for why that order).

Idempotent, and REMOVE rather than a filtered SET: this reads the manager's
current list, and if the intern is not in it there is nothing to do and the
call succeeds anyway - re-running an interrupted sequence must not fail on a
link that was already cleaned up. When the intern named is the last one on the
list, the whole `interns` attribute is REMOVEd rather than left as `[]`, which
is what keeps the attribute genuinely sparse (see the note on it in
common/db.py's promoted_item() callers).

A plain read-then-write, not a conditional expression keyed on list position:
DynamoDB has no "remove this value from the list" primitive, only "remove the
element at this index", so the index has to be found in Python first. The
window between the read and the write is accepted here - a lost update means
the same intern is removed twice, which the idempotent contract above already
treats as success.
"""
from common import responses
from common.db import employee_table
from common.handler import api_handler, employee_id_param, path_param, require_official
from common.keys import key
from common.models import to_api_staff_employee


@api_handler
def lambda_handler(event, context):
    require_official(event)

    manager_id = employee_id_param(event)
    intern_id = path_param(event, 'internId')

    manager_item = employee_table.get_item(Key=key(manager_id), ConsistentRead=True).get('Item')
    if manager_item is None:
        return responses.not_found('No employee with id ' + manager_id + '.')

    interns = list(manager_item.get('interns') or [])
    if intern_id not in interns:
        # Already unlinked, or never was - idempotent success either way.
        return responses.ok(to_api_staff_employee(manager_item))

    interns.remove(intern_id)

    if interns:
        result = employee_table.update_item(
            Key=key(manager_id),
            UpdateExpression='SET #interns = :interns',
            ExpressionAttributeNames={'#interns': 'interns'},
            ExpressionAttributeValues={':interns': interns},
            ReturnValues='ALL_NEW',
        )
    else:
        result = employee_table.update_item(
            Key=key(manager_id),
            UpdateExpression='REMOVE #interns',
            ExpressionAttributeNames={'#interns': 'interns'},
            ReturnValues='ALL_NEW',
        )

    return responses.ok(to_api_staff_employee(result['Attributes']))

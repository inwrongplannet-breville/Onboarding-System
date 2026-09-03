"""
DELETE /employees/{id} - archive, not erase.

This used to delete the whole partition in one transaction. It no longer deletes
anything. HR needs the history: who was hired, how far their onboarding got, and
what the notes on it said. A row that is gone answers none of those, and "we
deleted it" is the wrong answer to an audit.

So the item is stamped with a terminal state and the employee drops out of
GET /employees. Which state depends on where the checklist had got to:

    checklist complete     ->  Onboarded
    checklist incomplete   ->  Onboarding Cancelled

The record freezes: PUT and PATCH both refuse an archived employee, which is what
keeps the stamp honest - a cancelled onboarding cannot be ticked up to 100%
afterwards and left sitting there still claiming it was cancelled.

Note the archived record still holds that work email, and nothing reserves it any
more. Re-hiring under the same address now succeeds and produces a second record
sharing one mailbox; the uniqueness guard that used to make it a 409 is gone.

Still a DELETE and still the same route. From the caller's side "take this person
off the list" is exactly what happens; what changed is that it is now reversible
by someone with table access rather than by nobody.
"""
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from common import responses
from common.db import onboarding_table
from common.handler import (
    api_handler,
    employee_id_param,
    is_condition_failure,
    require_official,
)
from common.keys import EXISTS, key
from common.models import archive_state
from common.repository import load_employee


@api_handler
def lambda_handler(event, context):
    require_official(event)

    employee_id = employee_id_param(event)

    # Consistent, because the stamp is computed from this read. An eventually
    # consistent Query can hand back a checklist one tick behind, and that tick
    # is the whole difference between archiving as Onboarded and as Onboarding
    # Cancelled - a distinction nobody would ever think to go back and check.
    employee = load_employee(employee_id, consistent=True)
    if employee is None:
        return responses.not_found('No employee with id ' + employee_id + '.')

    if not employee['archived']:
        _stamp(employee_id, archive_state(employee['checklist']))

    # Re-read rather than patching the three fields onto the object above: it is
    # the same round trip PUT and PATCH make, and it means this response cannot
    # drift from what GET would say a moment later. Also covers the archived
    # case, where the right answer is the stamp the *first* DELETE wrote.
    archived = load_employee(employee_id, consistent=True)
    if archived is None:
        return responses.not_found('No employee with id ' + employee_id + '.')
    return responses.ok(archived)


def _stamp(employee_id, state):
    now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')

    try:
        onboarding_table.update_item(
            Key=key(employee_id),
            UpdateExpression=('SET #archivedAs = :archivedAs, #archivedAt = :archivedAt, '
                              '#updatedAt = :updatedAt'),
            ExpressionAttributeNames={
                '#archivedAs': 'archivedAs',
                '#archivedAt': 'archivedAt',
                '#updatedAt': 'updatedAt',
            },
            ExpressionAttributeValues={
                ':archivedAs': state,
                ':archivedAt': now,
                ':updatedAt': now,
            },
            # EXISTS for the same reason PUT carries it - UpdateItem
            # upserts, and an archive of a missing id would otherwise conjure an
            # employee out of nothing but a stamp.
            #
            # attribute_not_exists(archivedAs) makes a second stamp impossible, so
            # two DELETEs racing cannot produce a record whose archivedAt says one
            # thing and whose history says another. The loser fails here and the
            # caller still gets a 200, because from its side the employee is
            # archived either way.
            ConditionExpression=EXISTS + ' AND attribute_not_exists(#archivedAs)',
        )
    except ClientError as error:
        if not is_condition_failure(error):
            raise

"""
DELETE /employees/{id} - archive, not erase.

This used to delete the whole partition in one transaction. It no longer deletes
anything. HR needs the history: who was hired, how far their onboarding got, and
what the notes on it said. A row that is gone answers none of those, and "we
deleted it" is the wrong answer to an audit.

So the profile is stamped with a terminal state and the employee drops out of
GET /employees. Which state depends on where the checklist had got to:

    checklist complete     ->  Onboarded
    checklist incomplete   ->  Onboarding Cancelled

Two consequences, both deliberate:

  The email guard stays put. The address remains reserved to the archived
  employee, so re-hiring under the same work email is a 409 rather than a second
  record quietly sharing one mailbox. Nothing in the UI releases it - freeing an
  address is now a deliberate act against the table.

  The record freezes. PUT and PATCH both refuse an archived employee, which is
  what keeps the stamp honest: a cancelled onboarding cannot be ticked up to 100%
  afterwards and left sitting there still claiming it was cancelled.

Still a DELETE and still the same route. From the caller's side "take this person
off the list" is exactly what happens; what changed is that it is now reversible
by someone with table access rather than by nobody.
"""
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from common import responses
from common.db import table
from common.handler import api_handler, is_condition_failure, path_param
from common.keys import PROFILE_SK, pk
from common.models import archive_state
from common.repository import load_employee


@api_handler
def lambda_handler(event, context):
    employee_id = path_param(event, 'id')

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
        table.update_item(
            Key={'PK': pk(employee_id), 'SK': PROFILE_SK},
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
            # attribute_exists(SK) for the same reason PUT carries it - UpdateItem
            # upserts, and an archive of a missing id would otherwise conjure a
            # profile with no checklist behind it.
            #
            # attribute_not_exists(archivedAs) makes a second stamp impossible, so
            # two DELETEs racing cannot produce a record whose archivedAt says one
            # thing and whose history says another. The loser fails here and the
            # caller still gets a 200, because from its side the employee is
            # archived either way.
            ConditionExpression='attribute_exists(SK) AND attribute_not_exists(#archivedAs)',
        )
    except ClientError as error:
        if not is_condition_failure(error):
            raise

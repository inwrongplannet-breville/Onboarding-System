"""
PUT /employees/{id} - brief task 5.

One UpdateItem, one write path, whatever the body says.

The checklist now lives on the same item this writes, so "an edit preserves
onboarding progress" is no longer a property of touching a different row - it is a
property of touching different *attributes*. The SET clause is built from
EDITABLE_FIELDS and never names `checklist`, which is the only reason a PUT does
not flatten eight ticks and their comments. That whitelist is load-bearing; see
_profile_update.

The condition expression is load-bearing too: UpdateItem *upserts* by default, so
a PUT to an unknown id would happily create a half-employee with no checklist
behind it. That is what the EXISTS half of PROFILE_GUARD is for.

An archived employee is refused with a 409 - see handlers/delete_employee for why
the record freezes. The check happens once, as a condition on the write itself, so
an archive landing mid-request cannot be overwritten; _guard_failure then reads
back to say which half of the condition fired.

There is no rename here, and there cannot be. `employeeId` is the partition key,
and an UpdateItem naming a different Key does not move an item - it upserts a new
one and leaves the original in place. So the id is absent from EDITABLE_FIELDS
like the checklist is, and for a harder reason: the whitelist protects the
checklist from being flattened, and it protects the table from being forked.

This used to have a second, transactional write path for when the email changed,
moving a uniqueness guard item in step with the profile. Both are gone: there is
no guard any more, so a work email is just another editable field and two
employees may hold the same one.
"""
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from common import responses
from common.db import table
from common.handler import api_handler, employee_id_param, is_condition_failure, parse_body
from common.keys import EXISTS, key
from common.models import ARCHIVED_MESSAGE, EDITABLE_FIELDS, pick_editable, validate_employee
from common.repository import load_archive_state, load_employee


# The employee has to exist and must not be archived.
PROFILE_GUARD = EXISTS + ' AND attribute_not_exists(#archivedAs)'


def _guard_failure(employee_id):
    """
    Turn a fired PROFILE_GUARD into the right status code.

    Re-reads rather than guessing which half of the condition it was, because
    'archived' and 'never existed' are a 409 and a 404 and telling a caller the
    wrong one sends them looking in the wrong place.
    """
    state = load_archive_state(employee_id)
    if state is not None and state['archivedAs']:
        return responses.conflict(ARCHIVED_MESSAGE)
    return responses.not_found('No employee with id ' + employee_id + '.')


def _profile_update(values, now):
    """The SET clause. Whitelisted, and that is what protects the checklist."""
    assignments = []
    names = {}
    values_map = {}

    # Built from the whitelist, so `id`, `checklist` or anything else a caller
    # invents in the body simply never reaches the SET clause. Since the checklist
    # is an attribute of the very item being updated, this is now the only thing
    # standing between a profile edit and an erased checklist.
    for field in EDITABLE_FIELDS:
        assignments.append('#' + field + ' = :' + field)
        names['#' + field] = field
        values_map[':' + field] = values[field]

    assignments.append('#updatedAt = :updatedAt')
    names['#updatedAt'] = 'updatedAt'
    values_map[':updatedAt'] = now

    # Not in the SET clause - it is here for PROFILE_GUARD, which shares this
    # names map. DynamoDB rejects an ExpressionAttributeNames entry that no
    # expression uses, so this is only legal because the two always travel
    # together.
    names['#archivedAs'] = 'archivedAs'

    return 'SET ' + ', '.join(assignments), names, values_map


@api_handler
def lambda_handler(event, context):
    employee_id = employee_id_param(event)
    body = parse_body(event)
    values = pick_editable(body)

    errors = validate_employee(values)
    if errors:
        return responses.bad_request('Employee details are not valid.', errors)

    now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    expression, names, values_map = _profile_update(values, now)

    try:
        table.update_item(
            Key=key(employee_id),
            UpdateExpression=expression,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values_map,
            ConditionExpression=PROFILE_GUARD,
        )
    except ClientError as error:
        if is_condition_failure(error):
            return _guard_failure(employee_id)
        raise

    # Re-read so the response carries the checklist too, matching GET exactly.
    # Consistently, because an eventually consistent read here can hand back the
    # item as it was before the UpdateItem a moment ago.
    employee = load_employee(employee_id, consistent=True)
    if employee is None:
        return responses.not_found('No employee with id ' + employee_id + '.')
    return responses.ok(employee)

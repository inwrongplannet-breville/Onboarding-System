"""
PATCH /employees/{id}/contact - the one write an employee may make.

Its own route rather than a role branch inside PUT /employees/{id}. PUT is a full
replace of the nine fields HR owns, so an employee sending three of them would
blank the other six; and a handler that answered "which fields may this caller
set?" would be one whitelist away from letting a new hire promote themselves.
Separate route, separate audience, separate whitelist.

A sub-resource rather than PATCH on the employee itself, for the same reason: the
path says what may change. `/employees/E1024/contact` cannot be mistaken for a way
to edit the employment.

PATCH, not PUT, because all three fields are optional. A full replace would have
no required-field backstop - a curl naming one field would silently clear the
other two, and validate_self_fields would have nothing to complain about. So a key
that is present is written, a key that is present and empty is cleared, and a key
that is absent is left alone.

The employee owns two of these three outright: `personalEmail` and `address` are
absent from EDITABLE_FIELDS, so HR's PUT cannot set them and the officials form
never shows them. `phone` is on both lists and has two writers - see the note in
common/models.py.

Officials get a 403 here, not a bypass. They have PUT, and their username folds to
something that is not an employee number, so require_self refuses them without a
special case.
"""
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from common import responses
from common.db import table
from common.handler import (
    api_handler,
    employee_id_param,
    is_condition_failure,
    parse_body,
    require_role,
    require_self,
)
from common.keys import key
from common.models import (
    SELF_EDITABLE_FIELDS,
    own_profile_view,
    pick_self,
    validate_self_fields,
)
from common.repository import ACTIVE_GUARD, guard_failure_response, load_employee

# Every field named here is optional, so a body naming none of them is not a
# cleared profile - it is a request that forgot to say what it wanted.
_NOTHING_TO_DO = ('Send at least one of "' + '", "'.join(SELF_EDITABLE_FIELDS) +
                  '".')


def _contact_update(values, now):
    """
    The update expression for whichever fields this request named.

    SET for a value, REMOVE for an empty one. The split matters only to somebody
    reading the raw item in the console - `item.get(field, '')` in to_api_employee
    reads an absent attribute and an empty string identically - and it is the same
    choice handlers/set_checklist_item makes when a comment is cleared.

    `updatedAt` is always in the SET clause, and that is load-bearing rather than
    decorative: a request that cleared every field it named would otherwise build
    a REMOVE-only expression, and 'SET ' + ', '.join([]) is a syntax error.

    Names are added only for the fields actually named, because DynamoDB rejects an
    ExpressionAttributeNames entry no expression uses. `#archivedAs` is the
    deliberate exception - it belongs to ACTIVE_GUARD, which shares this map, so
    the two always travel together.
    """
    assignments = ['#updatedAt = :updatedAt']
    removals = []
    names = {'#updatedAt': 'updatedAt', '#archivedAs': 'archivedAs'}
    values_map = {':updatedAt': now}

    for field in SELF_EDITABLE_FIELDS:
        if field not in values:
            continue
        names['#' + field] = field
        if values[field]:
            assignments.append('#' + field + ' = :' + field)
            values_map[':' + field] = values[field]
        else:
            removals.append('#' + field)

    expression = 'SET ' + ', '.join(assignments)
    if removals:
        expression += ' REMOVE ' + ', '.join(removals)

    return expression, names, values_map


@api_handler
def lambda_handler(event, context):
    # Identity before body, matching handlers/create_employee: a caller who was
    # never going to be allowed to write this record should get a 403 about their
    # account, not a 400 about a body nobody was going to read.
    require_role(event)
    employee_id = employee_id_param(event)
    require_self(event, employee_id)

    try:
        values = pick_self(parse_body(event))
    except ValueError as error:
        return responses.bad_request('Your details are not valid.', error.args[0])

    if not values:
        return responses.bad_request(_NOTHING_TO_DO)

    errors = validate_self_fields(values)
    if errors:
        return responses.bad_request('Your details are not valid.', errors)

    now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    expression, names, values_map = _contact_update(values, now)

    try:
        table.update_item(
            Key=key(employee_id),
            UpdateExpression=expression,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values_map,
            ConditionExpression=ACTIVE_GUARD,
        )
    except ClientError as error:
        if is_condition_failure(error):
            return guard_failure_response(employee_id)
        raise

    # Consistently, because the UI repaints from this response and an eventually
    # consistent read here can hand back the item as it was before the write.
    employee = load_employee(employee_id, consistent=True)
    if employee is None:
        return responses.not_found('No employee with id ' + employee_id + '.')

    # own_profile_view, not `employee`. This is the one line in the file that
    # would leak: the full re-read carries every checklist comment HR has written
    # on this record, and a write path that skipped the trim the read path applies
    # would hand them all back on a 200 that looked entirely correct.
    return responses.ok(own_profile_view(employee))

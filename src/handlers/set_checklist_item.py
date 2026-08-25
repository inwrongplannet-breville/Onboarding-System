"""
PATCH /employees/{id}/checklist/{itemId}

Not a numbered row in the brief, but App.store.setChecklistItem already exists in
js/store.js and Phase 3 needs it, so it ships with the rest of the backend.

The checklist is a list attribute on the employee's item, and a tick is still a
single UpdateItem - because the entry is addressed as a *document path*,
`checklist[i].done`, and DynamoDB applies that mutation server-side. Nothing here
reads the list into Python and writes it back, which is what would make two
simultaneous ticks a lost update. DynamoDB serialises writes to one item and
applies each expression against the latest committed value, so a `done` PATCH and
a `comment` PATCH cannot clobber each other even when they name the same entry.

The index comes from CHECKLIST_INDEX, which is static. Its `itemId` condition
below is not defensive decoration: SET on an out-of-range list index *appends*
rather than failing, so without it a desynced list would silently grow a ninth
entry. See common/checklist_template.py.

PATCH rather than PUT because the body is a partial mutation of a sub-resource:
{"done": true}, {"comment": "Waiting on payroll"}, or both. Whichever keys are
present are the ones that change - which is what PATCH means, and why the comment
needed no endpoint of its own. Sending neither is a 400 rather than a silent
no-op, because a request that asks for nothing is a bug at the caller.

An archived employee is frozen: this returns 409 and writes nothing. The archive
stamp and the checklist now live on the same item, so that is one more term in
this write's own ConditionExpression rather than a ConditionCheck in a
transaction. The reason it matters is unchanged: the stamp records how far
onboarding got when someone cancelled it, and a tick sneaking in afterwards would
leave a record marked Onboarding Cancelled sitting at 8 of 8 with nothing in the
table to say which of the two was the lie.
"""
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from common import responses
from common.checklist_template import CHECKLIST_INDEX, VALID_ITEM_IDS
from common.db import table
from common.handler import (
    BadRequest,
    api_handler,
    employee_id_param,
    is_condition_failure,
    parse_body,
    path_param,
    require_official,
)
from common.keys import EXISTS, key
from common.models import ARCHIVED_MESSAGE, COMMENT_MAX_LENGTH, clean_comment
from common.repository import load_archive_state, load_employee


def _changes(body, entry):
    """
    The SET and REMOVE pieces for whatever this body actually asked to change,
    with every path rooted at `entry` - the one checklist element being changed.

    Returns (assignments, removals, names, values). Raises BadRequest if the body
    asks for nothing, or asks for something malformed.
    """
    assignments = []
    removals = []
    names = {}
    values = {}

    if 'done' in body:
        done = body['done']
        if not isinstance(done, bool):
            raise BadRequest('Field "done" must be true or false.',
                             {'done': 'Must be a boolean.'})
        assignments.append(entry + '.#done = :done')
        names['#done'] = 'done'
        values[':done'] = done

    if 'comment' in body:
        comment = body['comment']
        if comment is not None and not isinstance(comment, str):
            raise BadRequest('Field "comment" must be text.',
                             {'comment': 'Must be text.'})

        comment = clean_comment(comment)
        if len(comment) > COMMENT_MAX_LENGTH:
            raise BadRequest(
                'That comment is too long.',
                {'comment': 'Keep it under {} characters.'.format(COMMENT_MAX_LENGTH)},
            )

        names['#comment'] = 'comment'
        if comment:
            assignments.append(entry + '.#comment = :comment')
            values[':comment'] = comment
        else:
            # Cleared, not stored empty. An absent key and an empty string read
            # back identically through to_api_checklist_item, and REMOVE keeps
            # the item small and the intent legible in the console.
            #
            # Note this removes the `comment` key *inside* the entry, never the
            # entry itself - `REMOVE checklist[i]` would delete the element and
            # shift every later index down, permanently desyncing
            # CHECKLIST_INDEX.
            removals.append(entry + '.#comment')

    if not assignments and not removals:
        raise BadRequest('Send "done", "comment", or both.')

    return assignments, removals, names, values


def _write_failure(employee_id, item_id):
    """
    Why the conditional write bounced. Only reached on failure, so the extra read
    is off the hot path.

    Three outcomes, and the third is the interesting one: if the employee exists
    and is active, then the only remaining term is the `itemId` guard, which means
    the stored list does not match the template it was written from. That is a
    corrupted record, not a bad request - so it raises and becomes a 500 with a
    stack trace in CloudWatch. Reporting it as a 404 would hide it forever.
    """
    state = load_archive_state(employee_id)
    if state is None:
        return responses.not_found('No employee with id ' + employee_id + '.')
    if state['archivedAs']:
        return responses.conflict(ARCHIVED_MESSAGE)

    raise RuntimeError(
        'Employee {} is active but checklist[{}] is not {!r} - the stored list has '
        'drifted from CHECKLIST_TEMPLATE.'.format(
            employee_id, CHECKLIST_INDEX[item_id], item_id
        )
    )


@api_handler
def lambda_handler(event, context):
    require_official(event)

    employee_id = employee_id_param(event)
    item_id = path_param(event, 'itemId')
    body = parse_body(event)

    # Before _changes, so an unknown item is a 404 whatever the body says - and so
    # CHECKLIST_INDEX below cannot raise a KeyError. This is the only thing that
    # validates the item id now; there is no sibling row whose absence would.
    if item_id not in VALID_ITEM_IDS:
        return responses.not_found('No checklist item called ' + item_id + '.')

    entry = '#checklist[{}]'.format(CHECKLIST_INDEX[item_id])
    assignments, removals, names, values = _changes(body, entry)

    now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    # Both the entry and the employee are stamped, from one value. Disjoint paths,
    # so one placeholder serves both.
    assignments.append(entry + '.#updatedAt = :updatedAt')
    assignments.append('#updatedAt = :updatedAt')
    names['#updatedAt'] = 'updatedAt'
    values[':updatedAt'] = now

    names['#checklist'] = 'checklist'
    names['#archivedAs'] = 'archivedAs'
    names['#itemId'] = 'itemId'
    values[':itemId'] = item_id

    expression = 'SET ' + ', '.join(assignments)
    if removals:
        expression += ' REMOVE ' + ', '.join(removals)

    try:
        table.update_item(
            Key=key(employee_id),
            UpdateExpression=expression,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            # A plain string, not Attr(...): boto3's condition builder splits on
            # '.' for nesting but does not parse '[i]', so it would emit a
            # placeholder for the literal name "checklist[3]".
            ConditionExpression=(
                EXISTS + ' '
                'AND attribute_not_exists(#archivedAs) '
                'AND ' + entry + '.#itemId = :itemId'
            ),
        )
    except ClientError as error:
        if is_condition_failure(error):
            return _write_failure(employee_id, item_id)
        raise

    # Return the whole employee. The UI re-renders from the response rather than
    # trusting the checkbox, so it needs the recomputed status, not just the item.
    #
    # Consistent, because this read is a millisecond behind the write above and an
    # eventually consistent read is entitled to miss it. That would repaint the box
    # back to where the user just moved it from - the one failure this endpoint
    # absolutely must not produce.
    employee = load_employee(employee_id, consistent=True)
    if employee is None:
        return responses.not_found('No employee with id ' + employee_id + '.')

    return responses.ok(employee)

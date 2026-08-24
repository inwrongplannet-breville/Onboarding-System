"""
PATCH /employees/{id}/checklist/{itemId}

Not a numbered row in the brief, but App.store.setChecklistItem already exists in
js/store.js and Phase 3 needs it, so it ships with the rest of the backend.

This is the payoff for storing checklist items as separate rows: ticking a box is
an UpdateItem against one small item. No read-modify-write of a nested list, so
two people ticking different boxes at the same time cannot clobber each other.
The HR comment rides on that same row for the same reason - it is a property of
one checklist item, not of the employee.

PATCH rather than PUT because the body is a partial mutation of a sub-resource:
{"done": true}, {"comment": "Waiting on payroll"}, or both. Whichever keys are
present are the ones that change - which is what PATCH means, and why the comment
needed no endpoint of its own. Sending neither is a 400 rather than a silent
no-op, because a request that asks for nothing is a bug at the caller.
"""
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from common import responses
from common.checklist_template import VALID_ITEM_IDS
from common.db import table
from common.handler import BadRequest, api_handler, is_condition_failure, parse_body, path_param
from common.keys import chk_sk, pk
from common.models import COMMENT_MAX_LENGTH, clean_comment
from common.repository import load_employee


def _changes(body):
    """
    The SET and REMOVE pieces for whatever this body actually asked to change.

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
        assignments.append('#done = :done')
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
            assignments.append('#comment = :comment')
            values[':comment'] = comment
        else:
            # Cleared, not stored empty. An absent attribute and an empty string
            # read back identically through to_api_checklist_item, and REMOVE
            # keeps the item small and the intent legible in the console.
            removals.append('#comment')

    if not assignments and not removals:
        raise BadRequest('Send "done", "comment", or both.')

    return assignments, removals, names, values


@api_handler
def lambda_handler(event, context):
    employee_id = path_param(event, 'id')
    item_id = path_param(event, 'itemId')
    body = parse_body(event)

    assignments, removals, names, values = _changes(body)

    if item_id not in VALID_ITEM_IDS:
        return responses.not_found('No checklist item called ' + item_id + '.')

    now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    assignments.append('#updatedAt = :updatedAt')
    names['#updatedAt'] = 'updatedAt'
    values[':updatedAt'] = now

    expression = 'SET ' + ', '.join(assignments)
    if removals:
        expression += ' REMOVE ' + ', '.join(removals)

    try:
        table.update_item(
            Key={'PK': pk(employee_id), 'SK': chk_sk(item_id)},
            UpdateExpression=expression,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ConditionExpression='attribute_exists(PK) AND attribute_exists(SK)',
        )
    except ClientError as error:
        if is_condition_failure(error):
            return responses.not_found(
                'No checklist item ' + item_id + ' for employee ' + employee_id + '.'
            )
        raise

    # Return the whole employee. The UI re-renders from the response rather than
    # trusting the checkbox, so it needs the recomputed status, not just the item.
    #
    # Consistent, because this read is a millisecond behind the write above and
    # an eventually consistent Query is entitled to miss it. That would repaint
    # the box back to where the user just moved it from - the one failure this
    # endpoint absolutely must not produce.
    employee = load_employee(employee_id, consistent=True)
    if employee is None:
        return responses.not_found('No employee with id ' + employee_id + '.')

    return responses.ok(employee)

"""
PATCH /employees/{id}/checklist/{itemId}

Not a numbered row in the brief, but App.store.setChecklistItem already exists in
js/store.js and Phase 3 needs it, so it ships with the rest of the backend.

This is the payoff for storing checklist items as separate rows: ticking a box is
an UpdateItem against one small item. No read-modify-write of a nested list, so
two people ticking different boxes at the same time cannot clobber each other.

PATCH rather than PUT because the body is a partial mutation of a sub-resource:
{"done": true}.
"""
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from common import responses
from common.checklist_template import VALID_ITEM_IDS
from common.db import table
from common.handler import BadRequest, api_handler, is_condition_failure, parse_body, path_param
from common.keys import chk_sk, pk
from common.models import to_api_checklist_item
from handlers.get_employee import lambda_handler as get_employee


@api_handler
def lambda_handler(event, context):
    employee_id = path_param(event, 'id')
    item_id = path_param(event, 'itemId')
    body = parse_body(event)

    done = body.get('done')
    if not isinstance(done, bool):
        raise BadRequest('Field "done" must be true or false.', {'done': 'Must be a boolean.'})

    if item_id not in VALID_ITEM_IDS:
        return responses.not_found('No checklist item called ' + item_id + '.')

    now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')

    try:
        result = table.update_item(
            Key={'PK': pk(employee_id), 'SK': chk_sk(item_id)},
            UpdateExpression='SET #done = :done, #updatedAt = :updatedAt',
            ExpressionAttributeNames={'#done': 'done', '#updatedAt': 'updatedAt'},
            ExpressionAttributeValues={':done': done, ':updatedAt': now},
            ConditionExpression='attribute_exists(PK) AND attribute_exists(SK)',
            ReturnValues='ALL_NEW',
        )
    except ClientError as error:
        if is_condition_failure(error):
            return responses.not_found(
                'No checklist item ' + item_id + ' for employee ' + employee_id + '.'
            )
        raise

    # Return the whole employee. The UI re-renders from the response rather than
    # trusting the checkbox, so it needs the recomputed status, not just the item.
    employee_response = get_employee(event, context)
    if employee_response['statusCode'] == 200:
        return employee_response

    return responses.ok({'item': to_api_checklist_item(result['Attributes'])})

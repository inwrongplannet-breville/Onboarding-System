"""
PUT /employees/{id} - brief task 5.

UpdateItem on the profile row only. The checklist rows are separate items and are
not touched, which is what preserves onboarding progress across an edit.

The condition expression is load-bearing: UpdateItem *upserts* by default, so a
PUT to a deleted id would happily create a profile with no checklist behind it.
"""
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from common import responses
from common.db import table
from common.handler import api_handler, is_condition_failure, parse_body, path_param
from common.keys import PROFILE_SK, pk
from common.models import EDITABLE_FIELDS, pick_editable, validate_employee
from handlers.get_employee import lambda_handler as get_employee


@api_handler
def lambda_handler(event, context):
    employee_id = path_param(event, 'id')
    body = parse_body(event)
    values = pick_editable(body)

    errors = validate_employee(values)
    if errors:
        return responses.bad_request('Employee details are not valid.', errors)

    now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')

    # Built from the whitelist, so `id`, `checklist` or anything else a caller
    # invents in the body simply never reaches the SET clause.
    assignments = []
    names = {}
    values_map = {}
    for field in EDITABLE_FIELDS:
        assignments.append('#' + field + ' = :' + field)
        names['#' + field] = field
        values_map[':' + field] = values[field]

    assignments.append('#updatedAt = :updatedAt')
    names['#updatedAt'] = 'updatedAt'
    values_map[':updatedAt'] = now

    try:
        table.update_item(
            Key={'PK': pk(employee_id), 'SK': PROFILE_SK},
            UpdateExpression='SET ' + ', '.join(assignments),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values_map,
            ConditionExpression='attribute_exists(PK) AND attribute_exists(SK)',
        )
    except ClientError as error:
        if is_condition_failure(error):
            return responses.not_found('No employee with id ' + employee_id + '.')
        raise

    # Re-read so the response carries the checklist too, matching GET exactly.
    return get_employee(event, context)

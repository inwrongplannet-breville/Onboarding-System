"""
DELETE /employees/{id} - brief task 6.

Deleting an employee means deleting the whole partition, not one item. Query for
the keys first, then remove them all in one TransactWriteItems so a half-deleted
employee is not a reachable state.

If the child count were unbounded you could not do this - you would page the
Query and chunk BatchWriteItem 25 at a time with an UnprocessedItems retry loop,
giving up atomicity. A fixed 8-item checklist means atomicity is free, so take it.
"""
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from common import responses
from common.db import TABLE_NAME, client, serialize, table
from common.handler import api_handler, path_param
from common.keys import PROFILE_SK, is_profile, pk


@api_handler
def lambda_handler(event, context):
    employee_id = path_param(event, 'id')
    partition = pk(employee_id)

    result = table.query(
        KeyConditionExpression=Key('PK').eq(partition),
        ProjectionExpression='PK, SK',
    )
    items = result.get('Items', [])

    # Projection is keys-only, so check for the profile row directly rather
    # than trying to rebuild an employee object out of it.
    if not any(is_profile(item) for item in items):
        return responses.not_found('No employee with id ' + employee_id + '.')

    transact_items = []
    for item in items:
        delete = {
            'TableName': TABLE_NAME,
            'Key': serialize({'PK': item['PK'], 'SK': item['SK']}),
        }
        # Only the profile carries the guard - if it vanished between the Query
        # and this write, the whole transaction aborts and the caller gets a 404
        # instead of a 204 that deleted nothing.
        if item['SK'] == PROFILE_SK:
            delete['ConditionExpression'] = 'attribute_exists(SK)'
        transact_items.append({'Delete': delete})

    try:
        client.transact_write_items(TransactItems=transact_items)
    except ClientError as error:
        if error.response['Error']['Code'] == 'TransactionCanceledException':
            return responses.not_found('No employee with id ' + employee_id + '.')
        raise

    return responses.no_content()


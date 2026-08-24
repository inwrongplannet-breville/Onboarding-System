"""
DELETE /employees/{id} - brief task 6.

Deleting an employee means deleting the whole partition, not one item. Query for
the keys first, then remove them all in one TransactWriteItems so a half-deleted
employee is not a reachable state.

If the child count were unbounded you could not do this - you would page the
Query and chunk BatchWriteItem 25 at a time with an UnprocessedItems retry loop,
giving up atomicity. A fixed 8-item checklist means atomicity is free, so take it.

The employee's email uniqueness guard lives outside the partition, so the Query
cannot see it - it has to be read off the profile and deleted by name. Miss it
and that address is permanently unusable by anyone, with only an orphan row in a
partition nobody lists to say why.
"""
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from common import responses
from common.db import TABLE_NAME, client, serialize, table
from common.handler import api_handler, is_transaction_cancelled, path_param
from common.keys import EMAIL_SK, PROFILE_SK, email_pk, is_profile, pk


@api_handler
def lambda_handler(event, context):
    employee_id = path_param(event, 'id')
    partition = pk(employee_id)

    result = table.query(
        KeyConditionExpression=Key('PK').eq(partition),
        # Keys plus the one attribute that is not in the keys and still has to be
        # deleted. Only the profile row carries it; the checklist rows return the
        # keys alone.
        ProjectionExpression='PK, SK, #email',
        ExpressionAttributeNames={'#email': 'email'},
        ConsistentRead=True,
    )
    items = result.get('Items', [])

    # Near-keys-only projection, so check for the profile row directly rather
    # than trying to rebuild an employee object out of it.
    profile = next((item for item in items if is_profile(item)), None)
    if profile is None:
        return responses.not_found('No employee with id ' + employee_id + '.')

    transact_items = []
    for item in items:
        delete = {
            'TableName': TABLE_NAME,
            'Key': serialize({'PK': item['PK'], 'SK': item['SK']}),
        }
        # Only the profile row carries a condition - if it vanished between the
        # Query and this write, the whole transaction aborts and the caller gets
        # a 404 instead of a 204 that deleted nothing.
        if item['SK'] == PROFILE_SK:
            delete['ConditionExpression'] = 'attribute_exists(SK)'
        transact_items.append({'Delete': delete})

    # Unconditional: employees created before uniqueness guards existed have no
    # guard, and the delete of a missing item is a harmless no-op.
    if profile.get('email'):
        transact_items.append({'Delete': {
            'TableName': TABLE_NAME,
            'Key': serialize({'PK': email_pk(profile['email']), 'SK': EMAIL_SK}),
        }})

    try:
        client.transact_write_items(TransactItems=transact_items)
    except ClientError as error:
        # The only condition in this transaction is the profile guard, so a
        # cancellation here means it went between the Query and the write.
        if is_transaction_cancelled(error):
            return responses.not_found('No employee with id ' + employee_id + '.')
        raise

    return responses.no_content()


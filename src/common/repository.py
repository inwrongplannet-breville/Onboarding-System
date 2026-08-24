"""
The one way to read a whole employee back out of the table.

It exists for the `consistent` flag. DynamoDB Query is *eventually consistent by
default*, so a handler that writes and then re-reads to build its response can
legitimately be handed the pre-write values - and PUT and PATCH both do exactly
that. The UI then repaints from that response (paintChecklist in js/app.js), so a
stale read shows the user a checkbox snapping back to where it was.

GET pays no such price and stays eventually consistent: nothing it returns was
written a millisecond earlier by the same caller, and consistent reads cost twice
as much.
"""
from boto3.dynamodb.conditions import Key

from common.db import table
from common.keys import PROFILE_SK, pk
from common.models import to_api_employee


def load_employee(employee_id, consistent=False):
    """The full API employee - profile plus checklist - or None if there is none."""
    result = table.query(
        KeyConditionExpression=Key('PK').eq(pk(employee_id)),
        ConsistentRead=consistent,
    )
    return to_api_employee(result.get('Items', []))


def load_profile(employee_id):
    """
    The two profile facts a write has to check first, or None if there is no
    such employee: the email it might have to move a guard for, and whether the
    record has been archived and is therefore frozen.

    One GetItem rather than two, and a projection rather than the whole row -
    neither caller wants the profile itself, they want permission to proceed.

    Read consistently. The email decides whether the update moves the uniqueness
    guard, and acting on a stale address there could strand a guard on an email
    nobody holds any more. The archive flag decides whether the write happens at
    all, and a stale read of that one lets an edit land on a frozen record.
    """
    result = table.get_item(
        Key={'PK': pk(employee_id), 'SK': PROFILE_SK},
        ProjectionExpression='#email, #archivedAs',
        ExpressionAttributeNames={'#email': 'email', '#archivedAs': 'archivedAs'},
        ConsistentRead=True,
    )
    item = result.get('Item')
    if item is None:
        return None
    return {'email': item.get('email', ''), 'archivedAs': item.get('archivedAs', '')}

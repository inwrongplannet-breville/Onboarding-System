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


def current_email(employee_id):
    """
    The email on the profile right now, or None if the employee does not exist.

    Read consistently: it decides whether the update has to move the uniqueness
    guard, and acting on a stale address there could strand a guard on an email
    nobody holds any more.
    """
    result = table.get_item(
        Key={'PK': pk(employee_id), 'SK': PROFILE_SK},
        ProjectionExpression='#email',
        ExpressionAttributeNames={'#email': 'email'},
        ConsistentRead=True,
    )
    item = result.get('Item')
    return None if item is None else item.get('email', '')

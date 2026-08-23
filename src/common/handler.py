"""
Cross-cutting handler plumbing: JSON body parsing and the outermost error net.

Without the decorator every handler would repeat the same try/except six times,
and the one that got it wrong would return a raw stack trace to the caller.
"""
import functools
import json
import logging

from botocore.exceptions import ClientError

from common import responses

logger = logging.getLogger()
logger.setLevel(logging.INFO)


class BadRequest(Exception):
    """Raised for malformed input. Carries the 400 body with it."""

    def __init__(self, message, fields=None):
        super().__init__(message)
        self.message = message
        self.fields = fields


def api_handler(func):
    """
    Last line of defence. Anything that escapes a handler becomes a 500 with a
    generic body and a full stack trace in CloudWatch - never the other way round.
    """
    @functools.wraps(func)
    def wrapper(event, context):
        try:
            return func(event, context)
        except BadRequest as error:
            return responses.bad_request(error.message, error.fields)
        except ClientError:
            logger.exception('DynamoDB call failed')
            return responses.server_error()
        except Exception:
            logger.exception('Unhandled error')
            return responses.server_error()

    return wrapper


def parse_body(event):
    """Request body as a dict, or a 400 if it is not a JSON object."""
    raw = event.get('body') or '{}'
    try:
        body = json.loads(raw)
    except ValueError:
        raise BadRequest('Request body must be valid JSON.')

    if not isinstance(body, dict):
        raise BadRequest('Request body must be a JSON object.')
    return body


def path_param(event, name):
    value = (event.get('pathParameters') or {}).get(name)
    if not value:
        raise BadRequest('Missing path parameter: ' + name + '.')
    return value


def is_condition_failure(error):
    return error.response['Error']['Code'] == 'ConditionalCheckFailedException'

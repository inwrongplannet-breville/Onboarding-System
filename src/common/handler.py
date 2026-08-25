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
from common.accounts import ROLE_EMPLOYEE, ROLE_OFFICIAL, ROLES
from common.models import clean_employee_id

logger = logging.getLogger()
logger.setLevel(logging.INFO)


class BadRequest(Exception):
    """Raised for malformed input. Carries the 400 body with it."""

    def __init__(self, message, fields=None):
        super().__init__(message)
        self.message = message
        self.fields = fields


class Forbidden(Exception):
    """
    Raised when the caller is authenticated but their role does not permit this.

    Separate from BadRequest because it is not a problem with the request: the
    body was fine, the employee exists, and the answer is still no.
    """

    def __init__(self, message):
        super().__init__(message)
        self.message = message


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
        except Forbidden as error:
            return responses.forbidden(error.message)
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


def employee_id_param(event):
    """
    The {id} path parameter, normalised the same way the create form normalises
    it.

    This exists because the id in the URL is now the employee number rather than
    a UUID, and people type employee numbers. The stored key is upper-cased at
    creation, so without the same fold here a perfectly correct
    /employees/e1024 would 404 against a record that plainly exists - and it
    would do it on GET, PUT, PATCH and DELETE alike.

    Note this only folds case and trims; it does not validate the shape. An id
    that could never exist simply misses, and "no employee with id ..." is the
    honest answer to a lookup for one.
    """
    return clean_employee_id(path_param(event, 'id'))


def is_condition_failure(error):
    return error.response['Error']['Code'] == 'ConditionalCheckFailedException'


def caller_role(event):
    """
    The role API Gateway's authorizer put on this request, or None.

    None means "no identified caller": no authorizer context at all, or a role
    string this build does not recognise. It used to mean ROLE_EMPLOYEE, and the
    audit is what changed it. Defaulting to the least *privileged* role sounds
    like failing closed and is not, because the employee role is not a closed
    door - it reads the whole staff directory. So a stack that lost its Auth
    block would have served every name, department, job title, start date and
    onboarding status to anyone who asked, with every test still passing.

    None is a closed door. require_role() below turns it into a 403, and both
    reads and writes go through one of these two functions.

    An unrecognised role string collapses to None for the same reason: renaming a
    role in common/accounts.py must not leave old tokens falling through to
    whatever the default happens to be.

    Note this reads the authorizer's context, not the token. The token was
    verified once, by handlers/authorizer.py, before this Lambda was invoked;
    nothing in the request body or headers can reach this value.
    """
    context = ((event.get('requestContext') or {}).get('authorizer') or {})
    role = context.get('role')
    return role if role in ROLES else None


def is_official(event):
    return caller_role(event) == ROLE_OFFICIAL


def require_role(event):
    """
    The caller's role, or 403. First line of the two reading endpoints.

    The message is deliberately vague where require_official's is specific.
    Reaching here means the request arrived with no usable identity at all, which
    is a misconfiguration rather than something the caller can act on - and
    naming the mechanism would be telling them about it.
    """
    role = caller_role(event)
    if role is None:
        raise Forbidden('This request carried no usable credentials.')
    return role


def require_official(event):
    """
    Gate for the four writing endpoints. First line of each handler.

    The message says what the caller is rather than what they are missing -
    "your account is read-only" is actionable, where "insufficient permissions"
    invites a retry.
    """
    if not is_official(event):
        raise Forbidden('Your account has read-only access to employee records.')

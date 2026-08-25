"""
The API Gateway REQUEST authorizer - the gate in front of the other six routes.

Runs before any handler Lambda is invoked. It verifies the bearer token's
signature and hands the caller's identity down in `context`, where
common/handler.caller_role reads it. A request that gets past here has proven its
role cryptographically; a request that does not never reaches a handler at all.

Two decisions worth stating.

*It allows the whole API rather than a method-scoped policy.* API Gateway will
happily let an authorizer return "Allow GET, deny POST", which sounds like the
tighter option. It puts authorization in an IAM policy document, where pytest
cannot see it, and it interacts badly with authorizer result caching: the cached
policy is keyed on the token, so it is reused for the next request whatever
method that one used. The role check therefore lives in Python -
require_official, called by the four writing handlers - where it is one line per
handler and covered by tests.

*It raises rather than returning a Deny.* Raising Exception('Unauthorized') is
the documented way to make API Gateway emit a 401; returning an explicit Deny
policy produces a 403. The distinction is the one the frontend acts on - 401
means the session is over and sign out, 403 means this account cannot do this
particular thing - so it matters which one an expired token produces.
"""
import logging

from common.accounts import ROLES
from common.tokens import InvalidToken, secret, verify

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# The string API Gateway looks for. It maps this exact message to a 401; any
# other exception becomes a 500.
UNAUTHORIZED = 'Unauthorized'


def _bearer_token(event):
    """
    The token from the Authorization header, or None.

    Case-folds the header names first. HTTP header names are case-insensitive and
    API Gateway does not normalise them for you - a client sending
    `authorization:` and one sending `Authorization:` both arrive as typed, so a
    single-spelling lookup rejects perfectly valid requests depending on which
    HTTP library the caller used.
    """
    headers = event.get('headers') or {}
    value = None
    for name, header_value in headers.items():
        if isinstance(name, str) and name.lower() == 'authorization':
            value = header_value
            break

    if not isinstance(value, str):
        return None

    parts = value.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        return None
    return parts[1].strip()


class MalformedArn(Exception):
    """The methodArn was not the shape API Gateway always sends."""


def api_arn(method_arn):
    """
    Every method of this API and stage, from the ARN of the one being called.

    A methodArn looks like

        arn:aws:execute-api:eu-north-1:123:abc123/dev/GET/employees/E1024

    and the wildcard form keeps the last two segments open, so one cached policy
    covers the whole API.

    Raises rather than falling back. It used to return '*' on anything it could
    not parse, on the reasoning that a caller with a good token should not get a
    500 because of an ARN they never sent - which had this function answer "allow
    everything" in the one place whose entire job is to say no. Unreachable in
    practice, since API Gateway always supplies the ARN; the point is that the
    unreachable branch should not be the permissive one.
    """
    if not isinstance(method_arn, str):
        raise MalformedArn(repr(method_arn))
    parts = method_arn.split('/')
    if len(parts) < 2 or not parts[0]:
        raise MalformedArn(method_arn)
    return '/'.join(parts[:2]) + '/*/*'


def _allow(username, role, method_arn):
    resource = api_arn(method_arn)
    return {
        'principalId': username,
        'policyDocument': {
            'Version': '2012-10-17',
            'Statement': [{
                'Action': 'execute-api:Invoke',
                'Effect': 'Allow',
                'Resource': resource,
            }],
        },
        # Strings only. API Gateway silently drops context values that are not
        # strings, numbers or booleans, and converts what it keeps to strings -
        # so anything structured put here arrives as something else or not at all.
        'context': {
            'username': username,
            'role': role,
        },
    }


def lambda_handler(event, context):
    token = _bearer_token(event)
    if token is None:
        # Info, not a warning. An unauthenticated request is an ordinary event -
        # every first page load makes one - and logging it as a problem trains
        # people to ignore the log.
        logger.info('Rejected: no bearer token')
        raise Exception(UNAUTHORIZED)

    try:
        claims = verify(token, secret())
    except InvalidToken as error:
        logger.info('Rejected: %s', error)
        raise Exception(UNAUTHORIZED)

    username = claims.get('sub')
    role = claims.get('role')

    # A signed token is not automatically a usable one. These two claims are what
    # the rest of the system reads, and a token signed by an older version of
    # this stack - or by a version that spelled a role differently - has a valid
    # signature over claims that no longer mean anything. Checked here so that
    # caller_role downstream is never the thing deciding what an unknown role is.
    if not isinstance(username, str) or not username or role not in ROLES:
        logger.info('Rejected: token claims are not usable')
        raise Exception(UNAUTHORIZED)

    try:
        return _allow(username, role, event.get('methodArn'))
    except MalformedArn as error:
        # Refuse, rather than widening the policy to cover whatever this was.
        logger.warning('Rejected: unparseable methodArn %s', error)
        raise Exception(UNAUTHORIZED)

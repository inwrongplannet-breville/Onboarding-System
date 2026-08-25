"""
API Gateway proxy responses.

Every handler returns through here so status codes, headers and the error body
shape stay identical across all six routes.

CORS headers are set now even though Phase 2 is tested with curl only - the
browser needs them the moment Phase 3 points the UI at this API, and a missing
Access-Control-Allow-Origin is the classic first-day-of-integration wall.
"""
import decimal
import json
import os

# Which site may call this API from a browser.
#
# It was '*'. The audit's point was not CSRF - tokens live in sessionStorage and
# are attached by hand, so nothing is sent automatically - it was that '*' lets
# any page on the internet POST to /login and read the response, which turns
# every visitor to a malicious site into a source of password guesses against an
# endpoint with no rate limit. Naming the one origin that serves this UI closes
# that; the throttle in template.yaml covers what is left.
#
# Falls back to '*' only if the variable is missing, which in a deployed stack it
# is not - template.yaml sets it on all eight functions. The fallback exists so
# that `sam local` and the tests do not have to care.
ALLOWED_ORIGIN = os.environ.get('ALLOWED_ORIGIN') or '*'

_HEADERS = {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': ALLOWED_ORIGIN,
    # Authorization, because every call except POST /login now carries a bearer
    # token. A preflight that does not list it fails the request before the
    # browser ever sends the real one, and the error it reports is a CORS error
    # rather than anything mentioning the header it objected to.
    'Access-Control-Allow-Headers': 'Content-Type,Authorization',
    'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS',
}


class _DecimalEncoder(json.JSONEncoder):
    """DynamoDB hands back every number as Decimal, which json.dumps refuses."""

    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return int(o) if o % 1 == 0 else float(o)
        return super().default(o)


def _response(status, body=None, extra_headers=None):
    headers = dict(_HEADERS)
    if extra_headers:
        headers.update(extra_headers)

    result = {'statusCode': status, 'headers': headers}
    if body is not None:
        result['body'] = json.dumps(body, cls=_DecimalEncoder)
    return result


def ok(body):
    return _response(200, body)


def created(body, location):
    return _response(201, body, {'Location': location})


def no_content():
    return _response(204)


def _error(status, code, message, fields=None):
    payload = {'error': {'code': code, 'message': message}}
    if fields:
        payload['error']['fields'] = fields
    return _response(status, payload)


def bad_request(message, fields=None):
    return _error(400, 'ValidationError', message, fields)


def not_found(message):
    return _error(404, 'NotFound', message)


def conflict(message, fields=None):
    # `fields` for the same reason bad_request has it: a duplicate work email is
    # a problem with one input, and the form paints it under that input.
    return _error(409, 'Conflict', message, fields)


def unauthorized(message):
    """
    No usable credentials. The caller may retry with some.

    API Gateway returns this on its own when the authorizer rejects a token, so
    this function only covers the one route the authorizer does not sit in front
    of: POST /login. See GatewayResponses in template.yaml for why the
    gateway-generated version needs help with its CORS headers.
    """
    return _error(401, 'Unauthorized', message)


def forbidden(message):
    """
    Valid credentials, wrong role - an employee attempting a write.

    Distinct from 401 on purpose, and the distinction is load-bearing for the
    UI: a 401 means the session is gone and the frontend signs out, while a 403
    means the session is fine and this action simply is not theirs. Collapsing
    the two would log an employee out for clicking something they cannot do.
    """
    return _error(403, 'Forbidden', message)


def server_error():
    # Deliberately vague. The real exception is already in CloudWatch; leaking
    # stack traces to callers is how internals end up in someone's browser.
    return _error(500, 'InternalError', 'Something went wrong. Try again.')

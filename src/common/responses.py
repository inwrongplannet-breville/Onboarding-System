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

_HEADERS = {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type',
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


def server_error():
    # Deliberately vague. The real exception is already in CloudWatch; leaking
    # stack traces to callers is how internals end up in someone's browser.
    return _error(500, 'InternalError', 'Something went wrong. Try again.')

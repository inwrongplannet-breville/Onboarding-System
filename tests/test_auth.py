"""
POST /login and the authorizer - the two halves of the gate.

test_tokens.py covers the signing itself. These cover the routes: what a login
hands back, and what the authorizer does with it. Between them and the role tests
at the bottom of test_handlers.py, the path from a password to a permitted write
is covered end to end without a deploy.
"""
import json
import os

import pytest

from common.accounts import ROLE_EMPLOYEE, ROLE_OFFICIAL
from common.tokens import sign

# The demo passwords. Written here because a login test cannot avoid knowing
# them; common/accounts.py holds only the hashes.
OFFICIAL_LOGIN = {'username': 'hr.admin', 'password': 'onboard-2026'}
EMPLOYEE_LOGIN = {'username': 'employee', 'password': 'welcome-2026'}

METHOD_ARN = 'arn:aws:execute-api:eu-north-1:123456789012:abc123/dev/GET/employees'


def body(response):
    return json.loads(response['body'])


def login(handlers, payload):
    return handlers['login']({'body': json.dumps(payload)}, None)


def authorize(handlers, header_value, header_name='Authorization'):
    headers = {} if header_value is None else {header_name: header_value}
    return handlers['authorizer']({'headers': headers, 'methodArn': METHOD_ARN}, None)


def token_for(role, username='someone'):
    return sign({'sub': username, 'role': role}, os.environ['JWT_SECRET'])


# ------------------------------------------------------------------- login

def test_an_official_can_sign_in_and_gets_an_official_token(handlers):
    response = login(handlers, OFFICIAL_LOGIN)
    assert response['statusCode'] == 200

    result = body(response)
    assert result['role'] == ROLE_OFFICIAL
    assert result['displayName'] == 'HR Admin'
    assert result['expiresIn'] > 0
    assert result['token']


def test_an_employee_can_sign_in_and_gets_an_employee_token(handlers):
    result = body(login(handlers, EMPLOYEE_LOGIN))
    assert result['role'] == ROLE_EMPLOYEE


def test_the_token_a_login_returns_is_accepted_by_the_authorizer(handlers):
    """The two halves, joined. This is the flow the browser actually performs."""
    token = body(login(handlers, OFFICIAL_LOGIN))['token']
    result = authorize(handlers, 'Bearer ' + token)

    assert result['principalId'] == 'hr.admin'
    assert result['context'] == {'username': 'hr.admin', 'role': ROLE_OFFICIAL}
    assert result['policyDocument']['Statement'][0]['Effect'] == 'Allow'


def test_a_password_never_appears_in_the_response(handlers):
    assert 'onboard-2026' not in login(handlers, OFFICIAL_LOGIN)['body']


def test_a_wrong_password_is_401(handlers):
    response = login(handlers, dict(OFFICIAL_LOGIN, password='wrong'))
    assert response['statusCode'] == 401
    assert 'token' not in body(response)


def test_an_unknown_username_is_401_with_the_same_body_as_a_wrong_password(handlers):
    """
    Byte-identical, deliberately. Any difference between these two is a way to
    find out which usernames exist before starting to guess at their passwords.
    """
    unknown = login(handlers, {'username': 'nobody', 'password': 'onboard-2026'})
    wrong = login(handlers, dict(OFFICIAL_LOGIN, password='wrong'))

    assert unknown['statusCode'] == wrong['statusCode'] == 401
    assert unknown['body'] == wrong['body']


@pytest.mark.parametrize('payload', [
    {}, {'username': 'hr.admin'}, {'password': 'onboard-2026'},
    {'username': '', 'password': ''}, {'username': 12, 'password': 34},
])
def test_a_login_missing_its_credentials_is_400_not_401(handlers, payload):
    """
    400, because the problem is the request rather than the credentials - and a
    401 here would tell the caller their empty body was nearly right.
    """
    assert login(handlers, payload)['statusCode'] == 400


def test_a_login_body_that_is_not_json_is_400(handlers):
    assert handlers['login']({'body': 'not json'}, None)['statusCode'] == 400


def test_the_login_response_carries_cors_headers(handlers):
    # Login is the first call the browser makes. Without these it never gets a
    # token and the failure looks like the API is down.
    for response in (login(handlers, OFFICIAL_LOGIN),
                     login(handlers, dict(OFFICIAL_LOGIN, password='wrong'))):
        assert response['headers']['Access-Control-Allow-Origin'] == '*'
        assert 'Authorization' in response['headers']['Access-Control-Allow-Headers']


# -------------------------------------------------------------- authorizer

def test_the_authorizer_allows_the_whole_api_for_one_token(handlers):
    """
    One cached policy has to cover every route, because API Gateway caches it
    against the token and will reuse it for a request to a different method.
    """
    result = authorize(handlers, 'Bearer ' + token_for(ROLE_OFFICIAL))
    resource = result['policyDocument']['Statement'][0]['Resource']
    assert resource == 'arn:aws:execute-api:eu-north-1:123456789012:abc123/dev/*/*'


def test_the_authorizer_reads_a_lower_case_header_name(handlers):
    """
    HTTP header names are case-insensitive and API Gateway does not normalise
    them - a client sending `authorization:` is not sending a bad request.
    """
    result = authorize(handlers, 'Bearer ' + token_for(ROLE_EMPLOYEE), 'authorization')
    assert result['context']['role'] == ROLE_EMPLOYEE


def test_the_authorizer_passes_its_context_through_as_strings(handlers):
    """
    API Gateway drops context values that are not scalars, so a role that
    arrived as anything else would arrive as nothing.
    """
    context = authorize(handlers, 'Bearer ' + token_for(ROLE_EMPLOYEE))['context']
    for value in context.values():
        assert isinstance(value, str)


@pytest.mark.parametrize('header', [
    None,                       # no Authorization header at all
    '',                         # present and empty
    'Bearer',                   # scheme with no token
    'Bearer ',                  # scheme with whitespace
    'Basic aGk6dGhlcmU=',       # the wrong scheme
    'not-a-scheme token',       # nonsense scheme
])
def test_a_request_without_a_usable_bearer_token_is_unauthorized(handlers, header):
    with pytest.raises(Exception, match='Unauthorized'):
        authorize(handlers, header)


def test_a_bare_token_without_the_bearer_prefix_is_unauthorized(handlers):
    with pytest.raises(Exception, match='Unauthorized'):
        authorize(handlers, token_for(ROLE_OFFICIAL))


def test_an_expired_token_is_unauthorized(handlers):
    expired = sign({'sub': 'hr.admin', 'role': ROLE_OFFICIAL},
                   os.environ['JWT_SECRET'], ttl_seconds=-1)
    with pytest.raises(Exception, match='Unauthorized'):
        authorize(handlers, 'Bearer ' + expired)


def test_a_token_signed_with_another_key_is_unauthorized(handlers):
    forged = sign({'sub': 'hr.admin', 'role': ROLE_OFFICIAL}, 'not-the-deployed-key')
    with pytest.raises(Exception, match='Unauthorized'):
        authorize(handlers, 'Bearer ' + forged)


def test_a_token_carrying_an_unknown_role_is_unauthorized(handlers):
    """
    Signed correctly, and still refused. A role this build does not recognise -
    left over from a rename, say - must not reach a handler and be defaulted into
    something by caller_role.
    """
    with pytest.raises(Exception, match='Unauthorized'):
        authorize(handlers, 'Bearer ' + token_for('superuser'))


def test_a_token_with_no_subject_is_unauthorized(handlers):
    naked = sign({'role': ROLE_OFFICIAL}, os.environ['JWT_SECRET'])
    with pytest.raises(Exception, match='Unauthorized'):
        authorize(handlers, 'Bearer ' + naked)

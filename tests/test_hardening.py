"""
The fixes that came out of the endpoint audit.

Each test here exists because a probe found something. They are grouped by the
finding rather than by the module, because what is being protected is a
property - "a stack with no authorizer serves nothing", "a token from another
deployment is refused" - and each one is spread across two or three files.
"""
import json
import os

import pytest

from common.accounts import ROLE_EMPLOYEE, ROLE_OFFICIAL
from common.handler import (
    Forbidden,
    caller_employee_id,
    caller_role,
    caller_username,
    require_role,
    require_self,
)
from common.tokens import ISSUER, InvalidToken, sign, verify
from test_handlers import EMPLOYEE, OFFICIAL, VALID, body, create, get, listing

SECRET = 'hardening-tests-key'


# ------------------------------------------------- finding: fail closed means
#                                                   read-only, not no-access

@pytest.mark.parametrize('event', [
    {},
    {'requestContext': {}},
    {'requestContext': {'authorizer': {}}},
    {'requestContext': {'authorizer': {'role': None}}},
    {'requestContext': {'authorizer': {'role': ''}}},
    {'requestContext': {'authorizer': {'role': 'superuser'}}},
    {'requestContext': {'authorizer': {'role': 'OFFICIAL'}}},
])
def test_a_request_with_no_identified_role_has_no_role(event):
    """
    None, not "employee".

    This is the finding in one assertion. Defaulting an unidentified caller to
    the least privileged *role* is not the same as denying them, because the
    employee role reads the entire staff directory - so a stack that lost its
    Auth block would have served every name, department, job title and start
    date to anyone who asked.
    """
    assert caller_role(event) is None


def test_require_role_refuses_an_unidentified_caller():
    with pytest.raises(Forbidden):
        require_role({})


def test_reads_are_refused_without_an_authorizer_context(handlers):
    """
    The half that used to be open. Both reading endpoints now go through
    require_role, so a missing authorizer denies reads as well as writes.
    """
    assert handlers['list_employees']({}, None)['statusCode'] == 403
    assert handlers['get_employee'](
        {'pathParameters': {'id': 'E1001'}}, None)['statusCode'] == 403


def test_no_employee_data_escapes_in_a_refused_read(handlers):
    """A 403 must not carry the thing it refused."""
    create(handlers, firstName='Priya', lastName='Sharma')
    raw = handlers['list_employees']({}, None)['body']

    for leak in ('Priya', 'Sharma', 'Engineering', 'employees'):
        assert leak not in raw


def test_the_directory_admits_officials_and_refuses_employees(handlers):
    """
    The gate must not close on the caller it exists to admit - and the employee
    role is no longer that caller.

    This test used to assert 200 for both. The directory is officials-only now: an
    employee has exactly one record they may read and they reach it by id, so
    there is no trimmed list left to serve them.
    """
    create(handlers)
    assert listing(handlers, OFFICIAL)['statusCode'] == 200
    assert listing(handlers, EMPLOYEE)['statusCode'] == 403


# ---------------------------------- finding: no iss/aud, so a token from one
#                                    deployment works against another

def test_a_token_carries_an_issuer_and_an_audience():
    claims = verify(sign({'sub': 'hr.admin', 'role': ROLE_OFFICIAL}, SECRET), SECRET)
    assert claims['iss'] == ISSUER
    assert claims['aud'] == os.environ['STAGE']


def test_a_token_minted_for_another_stage_is_refused(monkeypatch):
    """
    Correctly signed, correctly unexpired, and still refused.

    The scenario is mundane: someone copies the deploy command and prod ends up
    sharing dev's signing key. Without `aud`, a dev token is then a prod token.
    """
    monkeypatch.setenv('STAGE', 'dev')
    dev_token = sign({'sub': 'hr.admin', 'role': ROLE_OFFICIAL}, SECRET)

    monkeypatch.setenv('STAGE', 'prod')
    with pytest.raises(InvalidToken):
        verify(dev_token, SECRET)


def test_a_token_from_another_issuer_is_refused():
    """
    A token minted by some other system that happens to share the key.
    Hand-built, because sign() will not produce one.
    """
    from common.tokens import _HEADER, _encode_segment, _signature
    import time

    now = int(time.time())
    claims = {'sub': 'hr.admin', 'role': ROLE_OFFICIAL, 'iat': now,
              'exp': now + 3600, 'iss': 'some-other-system',
              'aud': os.environ['STAGE']}

    signing_input = _encode_segment(_HEADER) + b'.' + _encode_segment(claims)
    token = (signing_input + b'.' + _signature(signing_input, SECRET)).decode()

    with pytest.raises(InvalidToken):
        verify(token, SECRET)


def test_the_authorizer_refuses_a_token_from_another_stage(handlers, monkeypatch):
    monkeypatch.setenv('STAGE', 'dev')
    token = sign({'sub': 'hr.admin', 'role': ROLE_OFFICIAL}, os.environ['JWT_SECRET'])

    monkeypatch.setenv('STAGE', 'prod')
    with pytest.raises(Exception, match='Unauthorized'):
        handlers['authorizer']({
            'headers': {'Authorization': 'Bearer ' + token},
            'methodArn': 'arn:aws:execute-api:eu-north-1:1:api/prod/GET/employees',
        }, None)


# ------------------------------- finding: api_arn fell open to Resource '*'

@pytest.mark.parametrize('method_arn', [None, '', 'garbage', 123, {}, '/'])
def test_an_unparseable_method_arn_is_refused_not_widened(handlers, method_arn):
    """
    It used to return Resource '*' - "allow everything" - from the one function
    whose entire job is to say no. Unreachable via API Gateway, which always
    sends the ARN; the point is that the unreachable branch should not be the
    permissive one.
    """
    token = sign({'sub': 'hr.admin', 'role': ROLE_OFFICIAL}, os.environ['JWT_SECRET'])

    with pytest.raises(Exception, match='Unauthorized'):
        handlers['authorizer']({
            'headers': {'Authorization': 'Bearer ' + token},
            'methodArn': method_arn,
        }, None)


def test_a_valid_arn_still_yields_a_whole_api_policy(handlers):
    token = sign({'sub': 'employee', 'role': ROLE_EMPLOYEE}, os.environ['JWT_SECRET'])
    result = handlers['authorizer']({
        'headers': {'Authorization': 'Bearer ' + token},
        'methodArn': 'arn:aws:execute-api:eu-north-1:1:api7/dev/GET/employees/E1',
    }, None)

    assert result['policyDocument']['Statement'][0]['Resource'] == \
        'arn:aws:execute-api:eu-north-1:1:api7/dev/*/*'


# ---------------------------- finding: the signing key was a plaintext Lambda
#                              environment variable

def test_the_secret_is_read_from_the_environment_in_tests(monkeypatch):
    """
    JWT_SECRET still wins when it is set, which is what keeps the tests and
    `sam local` from needing AWS. The deployed path is JWT_SECRET_ARN.
    """
    import common.tokens as tokens
    monkeypatch.setattr(tokens, '_cached_secret', None)
    monkeypatch.setenv('JWT_SECRET', 'from-the-environment')
    assert tokens.secret() == 'from-the-environment'


def test_with_neither_source_configured_signing_fails_loudly(monkeypatch):
    """
    Never an empty-string default. A stack that signs with a key written in the
    source is a stack that is not signing at all, and a loud 500 is the only
    honest response.
    """
    import common.tokens as tokens
    monkeypatch.setattr(tokens, '_cached_secret', None)
    monkeypatch.delenv('JWT_SECRET', raising=False)
    monkeypatch.delenv('JWT_SECRET_ARN', raising=False)

    with pytest.raises(RuntimeError):
        tokens.secret()


def test_the_cached_secret_expires_so_rotation_takes_effect(monkeypatch):
    """
    The cache has to have a ceiling, or rotation is theatre.

    The first version of this cached the key for the life of the container. A
    warm Lambda that had already fetched it went on trusting it for minutes or
    hours, so rotating the secret invalidated nothing on any container already
    running - and "revoke every session now" quietly meant "eventually, on some
    containers, unpredictably".
    """
    import common.tokens as tokens

    assert tokens._SECRET_TTL_SECONDS > 0
    monkeypatch.setattr(tokens, '_cached_secret', 'stale-key')
    monkeypatch.setattr(tokens, '_cached_at', 0.0)   # fetched long ago

    monkeypatch.setenv('JWT_SECRET', 'rotated-key')
    assert tokens.secret() == 'rotated-key'


def test_a_fresh_cached_secret_is_not_refetched(monkeypatch):
    """The other half: inside the window it must not call out, or every request
    pays a Secrets Manager round trip."""
    import time

    import common.tokens as tokens

    monkeypatch.setattr(tokens, '_cached_secret', 'warm-key')
    monkeypatch.setattr(tokens, '_cached_at', time.monotonic())
    monkeypatch.setenv('JWT_SECRET', 'would-be-refetched')

    assert tokens.secret() == 'warm-key'


def test_a_login_never_returns_the_signing_key(handlers):
    response = handlers['login']({'body': json.dumps(
        {'username': 'hr.admin',
         'password': os.environ['TEST_OFFICIAL_PASSWORD']})}, None)
    assert os.environ['JWT_SECRET'] not in response['body']


# ------------------------------------------ finding: CORS was pinned to '*'

def test_the_allowed_origins_are_configurable(monkeypatch):
    """
    '*' let any page on the internet POST to /login and read the token back,
    which is a password-guessing proxy through other people's browsers. The
    origin is now deployment configuration rather than a constant.
    """
    import importlib

    monkeypatch.setenv(
        'ALLOWED_ORIGINS',
        'http://localhost:8000,http://localhost:8001',
    )
    responses = importlib.reload(importlib.import_module('common.responses'))
    try:
        responses.configure_request_origin({
            'headers': {'Origin': 'http://localhost:8001'},
        })
        assert responses.ok({})['headers']['Access-Control-Allow-Origin'] == \
            'http://localhost:8001'

        responses.configure_request_origin({
            'headers': {'origin': 'https://untrusted.example.com'},
        })
        assert 'Access-Control-Allow-Origin' not in responses.ok({})['headers']
    finally:
        # Restore the module for every test that runs after this one.
        monkeypatch.setenv('ALLOWED_ORIGINS', '*')
        importlib.reload(responses)


def test_authorization_stays_an_allowed_request_header(handlers):
    """Tightening the origin must not drop the header every request carries."""
    headers = handlers['login']({'body': '{}'}, None)['headers']
    assert 'Authorization' in headers['Access-Control-Allow-Headers']


# ------------------------------------------ finding: an own-record check must
#                                            fail closed on a missing identity

@pytest.mark.parametrize('event', [
    {},
    {'requestContext': {}},
    {'requestContext': {'authorizer': {}}},
    {'requestContext': {'authorizer': {'role': ROLE_EMPLOYEE}}},
    {'requestContext': {'authorizer': {'role': ROLE_EMPLOYEE, 'username': None}}},
    {'requestContext': {'authorizer': {'role': ROLE_EMPLOYEE, 'username': ''}}},
    {'requestContext': {'authorizer': {'role': ROLE_EMPLOYEE, 'username': 12}}},
])
def test_a_request_with_no_identified_caller_has_no_employee_id(event):
    """
    The same parametrisation as the role case above, asked of the identity. '' is
    the only safe answer to "whose record is this?" when nobody said.
    """
    assert caller_username(event) == ''
    assert caller_employee_id(event) == ''


@pytest.mark.parametrize('employee_id', ['E1001', '', None])
def test_require_self_refuses_a_caller_with_no_identity(employee_id):
    """
    Fails closed, including against itself.

    caller_employee_id is '' for an event with no username, and employee_id_param
    cannot return '' today because path_param raises on a falsy value. Without the
    emptiness check in require_self, that pairing would be one refactor away from
    letting an unauthenticated request match a record - so the check does not rely
    on a guarantee made in another file.
    """
    with pytest.raises(Forbidden):
        require_self({'requestContext': {'authorizer': {'role': ROLE_EMPLOYEE}}},
                     employee_id)


def test_require_self_reads_the_authorizer_context_and_nothing_else():
    """
    Not the body, not the headers, not a path parameter. The authorizer verified
    this value before the handler was invoked; anything the caller could set
    instead would make the whole check decorative.
    """
    forged = {
        'headers': {'X-Username': 'E1002'},
        'body': '{"username": "E1002"}',
        'pathParameters': {'id': 'E1002'},
        'requestContext': {'authorizer': {'role': ROLE_EMPLOYEE, 'username': 'E1001'}},
    }

    require_self(forged, 'E1001')            # the authorizer's value
    with pytest.raises(Forbidden):
        require_self(forged, 'E1002')        # the one the caller supplied


def test_an_officials_username_matches_no_employee_record():
    """
    Why require_self needs no officials special case: 'hr.admin' folds to
    'HR.ADMIN', which EMPLOYEE_ID_PATTERN would never have accepted as a number,
    so it cannot collide with a real id.
    """
    event = {'requestContext': {'authorizer': {'role': ROLE_OFFICIAL,
                                               'username': 'hr.admin'}}}
    assert caller_employee_id(event) == 'HR.ADMIN'
    with pytest.raises(Forbidden):
        require_self(event, 'E1001')

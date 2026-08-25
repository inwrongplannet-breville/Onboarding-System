"""
The token module, on its own.

These are the tests that matter most in the repo. Everything else protects
behaviour; this protects the one thing standing between an employee account and
the officials console. Each test below is a way of forging a token, and each one
has to come back InvalidToken.
"""
import base64
import json
import time

import pytest

from common.tokens import ALGORITHM, InvalidToken, sign, verify

SECRET = 'a-signing-key-long-enough-to-be-realistic'


def segments(token):
    return token.split('.')


def rebuild(header, claims, signature):
    """A token assembled by hand - how an attacker would make one."""
    def encode(payload):
        raw = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()
    return encode(header) + '.' + encode(claims) + '.' + signature


def test_a_signed_token_verifies_and_carries_its_claims():
    token = sign({'sub': 'hr.admin', 'role': 'official'}, SECRET)
    claims = verify(token, SECRET)

    assert claims['sub'] == 'hr.admin'
    assert claims['role'] == 'official'


def test_signing_adds_an_issued_at_and_an_expiry():
    before = int(time.time())
    claims = verify(sign({'sub': 'x'}, SECRET, ttl_seconds=60), SECRET)

    assert claims['iat'] >= before
    assert claims['exp'] == claims['iat'] + 60


def test_a_token_signed_with_another_secret_is_rejected():
    token = sign({'sub': 'hr.admin', 'role': 'official'}, 'someone-elses-key')
    with pytest.raises(InvalidToken):
        verify(token, SECRET)


def test_editing_the_role_invalidates_the_token():
    """
    The whole design, in one test.

    An employee has their own valid token. Nothing stops them decoding it,
    changing the role and re-encoding it - the payload is base64, not
    encryption. What stops them is that they cannot produce the signature over
    the payload they just wrote.
    """
    employee_token = sign({'sub': 'employee', 'role': 'employee'}, SECRET)
    header, _, signature = segments(employee_token)

    forged = rebuild({'alg': ALGORITHM, 'typ': 'JWT'},
                     {'sub': 'employee', 'role': 'official',
                      'iat': int(time.time()), 'exp': int(time.time()) + 3600},
                     signature)

    with pytest.raises(InvalidToken):
        verify(forged, SECRET)


def test_a_tampered_signature_is_rejected():
    token = sign({'sub': 'x'}, SECRET)
    with pytest.raises(InvalidToken):
        verify(token[:-2] + ('aa' if not token.endswith('aa') else 'bb'), SECRET)


def test_an_expired_token_is_rejected():
    # Negative TTL rather than sleeping: exp lands in the past, which is exactly
    # the state a token reaches eight hours after it was minted.
    token = sign({'sub': 'x', 'role': 'official'}, SECRET, ttl_seconds=-1)
    with pytest.raises(InvalidToken):
        verify(token, SECRET)


def test_a_token_with_no_expiry_is_rejected():
    """
    A hand-built token omitting exp must not read as "never expires".

    It is signed correctly here - the point is that a correct signature over
    claims with no expiry is still not a usable token, or a leaked token would
    be usable forever.
    """
    from common.tokens import _encode_segment, _signature, _HEADER

    signing_input = _encode_segment(_HEADER) + b'.' + _encode_segment({'sub': 'x', 'role': 'official'})
    token = (signing_input + b'.' + _signature(signing_input, SECRET)).decode()

    with pytest.raises(InvalidToken):
        verify(token, SECRET)


def test_the_none_algorithm_is_rejected():
    """
    The classic JWT hole: a verifier that trusts the header's `alg` lets the
    caller choose "unsigned".
    """
    claims = {'sub': 'employee', 'role': 'official',
              'iat': int(time.time()), 'exp': int(time.time()) + 3600}

    for signature in ('', 'anything'):
        with pytest.raises(InvalidToken):
            verify(rebuild({'alg': 'none', 'typ': 'JWT'}, claims, signature), SECRET)


def test_a_header_claiming_another_algorithm_is_rejected():
    token = sign({'sub': 'x', 'role': 'official'}, SECRET)
    _, payload, signature = segments(token)
    forged = rebuild({'alg': 'HS512', 'typ': 'JWT'}, {'sub': 'x'}, signature)

    with pytest.raises(InvalidToken):
        verify(forged, SECRET)


@pytest.mark.parametrize('token', [
    None, '', 'not-a-token', 'two.parts', 'a.b.c.d', 'x' * 40,
])
def test_malformed_input_is_rejected_rather_than_raising_something_else(token):
    """Every one of these must be InvalidToken, not ValueError or TypeError -
    the authorizer catches only InvalidToken, and anything else is a 500."""
    with pytest.raises(InvalidToken):
        verify(token, SECRET)

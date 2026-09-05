"""
Who can sign in, and which of the two roles they get.

One named account and one shared password. The officials login is an account in
the ordinary sense - a username, `hr.admin`, keyed in the `accounts` map loaded by
_load() below. The employee login is not: an employee signs in with **their
employee number**, and every number verifies against the same shared
`employeeCredential`. The number is the identity, the password is a door key, and
there is no per-employee record here to key it against.

That shape is doing real work and also has a real cost, both worth stating.

What it buys: an employee's token names a person, so the API can scope every read
and write to their own record without a user-management screen, an invite flow, or
a users table that would be machinery in service of a list HR already maintains in
DynamoDB.

What it costs: the password is shared, and employee numbers are sequential and
printed on payslips. So this scopes the *product* - each person sees their own
profile - and it does not scope confidentiality. One leaked password reads any
record, one number at a time. Before this holds real data it needs per-employee
credentials, which is the Cognito note below and not a small edit.

Passwords are stored as PBKDF2-SHA256 hashes rather than plaintext. The reasoning
is not that the hash is secret - it is that the *code* should never be the place
a password is written down. Operators choose passwords locally and seed only the
derived values into Secrets Manager. A plaintext comparison would have to be
found and rewritten first, which is exactly the edit that gets missed.

The salt+hash values themselves live in Secrets Manager, not in this file - see
_load() below. That is the same move common/tokens.py already made for the JWT
signing key, and for the same reason: a value sitting in source control, or even
just a Lambda environment variable, is readable by any principal with a fairly
ordinary permission (`lambda:GetFunctionConfiguration`, or just repo access) and
never expires until someone edits code and redeploys. A Secrets Manager entry is
readable only by the one IAM grant in template.yaml, and rotates with one API
call.

Where this would go next: drop this module entirely for a Cognito user pool -
real per-employee credentials rather than one shared password. Not blocked by
anything below; more than this phase needs.
"""
import hashlib
import hmac
import json
import os
import time

from common.models import EMPLOYEE_ID_PATTERN, clean_employee_id

ROLE_OFFICIAL = 'official'
ROLE_EMPLOYEE = 'employee'

# Every role the system knows. The authorizer and handler.caller_role check
# against this rather than against a string literal, so a token carrying a role
# that was renamed or removed fails closed instead of matching nothing and
# quietly behaving like the default.
ROLES = (ROLE_OFFICIAL, ROLE_EMPLOYEE)

# 200k rounds of SHA-256. High enough that a leaked hash is not a wordlist away
# from the password, low enough to disappear into a Lambda cold start - this runs
# once per login, not once per request.
_ITERATIONS = 200_000

# Used only to spend the same CPU on an unknown username as on a known one - see
# verify_credentials. Never matches: no account named '' can be looked up.
_DUMMY = {
    'salt': '00000000000000000000000000000000',
    'hash': '0' * 64,
}


def _derive(password, salt_hex):
    if not isinstance(password, str):
        password = ''
    return hashlib.pbkdf2_hmac(
        'sha256', password.encode('utf-8'), bytes.fromhex(salt_hex), _ITERATIONS
    ).hex()


# Sixty seconds, the same window common/tokens.py's secret() cache uses. Rotating
# a compromised employee credential should stop working on every warm container
# within about a minute, not whenever each one happens to cold-start next.
_ACCOUNTS_TTL_SECONDS = 60

_cached_data = None
_cached_at = 0.0


def _load():
    """
    {'accounts': {...}, 'employeeCredential': {...}}, from Secrets Manager.

    Two sources, in order - the same shape as common/tokens.py's secret():

      $ACCOUNTS_JSON        tests and `sam local` - set directly, no AWS call
      $ACCOUNTS_SECRET_ARN  deployed - fetched from Secrets Manager, then cached

    Missing is a hard failure, never an empty-account default: a default here
    would mean a misconfigured stack quietly accepting nobody, or worse, a
    hardcoded fallback that puts credential material back in this file - the
    exact thing this function exists to avoid.
    """
    global _cached_data, _cached_at

    if _cached_data and (time.monotonic() - _cached_at) < _ACCOUNTS_TTL_SECONDS:
        return _cached_data

    direct = os.environ.get('ACCOUNTS_JSON')
    if direct:
        _cached_data = json.loads(direct)
        _cached_at = time.monotonic()
        return _cached_data

    arn = os.environ.get('ACCOUNTS_SECRET_ARN')
    if not arn:
        raise RuntimeError(
            'Neither ACCOUNTS_JSON nor ACCOUNTS_SECRET_ARN is set. '
            'The stack cannot verify any login.')

    # Imported here rather than at module scope so that tests and `sam local`,
    # which take the ACCOUNTS_JSON path above, never build a client they will
    # not use - see common/tokens.py's secret() for the same reasoning.
    import boto3

    value = boto3.client('secretsmanager').get_secret_value(SecretId=arn)['SecretString']
    if not value:
        raise RuntimeError('The secret at ACCOUNTS_SECRET_ARN is empty.')

    _cached_data = json.loads(value)
    _cached_at = time.monotonic()
    return _cached_data


def verify_credentials(username, password):
    """
    {'username', 'role', 'displayName'}, or None. Never says which half was wrong.

    `username` comes back canonicalised - upper-cased for an employee number, so
    `e1001` and `E1001` are one identity and match the partition key fold in
    common/models.clean_employee_id. handlers/login signs *that* value as `sub`,
    never the string the caller typed.

    Two kinds of caller, resolved in this order, and the order is load-bearing: a
    named account wins over the employee-number shape. `hr.admin` is safe from the
    pattern today only because EMPLOYEE_ID_PATTERN rejects the dot - and a future
    officials username like `admin2` would be number-shaped and would otherwise
    sign in silently as an employee. Checking named accounts first makes that a
    non-question rather than a coincidence.

    Every path pays exactly one full PBKDF2 derivation - the named account's, the
    shared employee one, or the dummy. Without that, "no such user" returns in
    microseconds and "wrong password" takes 200k rounds, and the difference is
    measurable over the network - which turns this endpoint into a way to find out
    that `hr.admin` exists before guessing at it.

    Note what is *not* checked: whether the employee number names a record that
    exists. This module is imported by a Lambda with no DynamoDB permission at
    all, deliberately - see the LoginFunction policy in template.yaml - so a
    number with no employee behind it signs in and gets a 404 from its first read.
    """
    data = _load()

    typed = username if isinstance(username, str) else ''
    account = data['accounts'].get(typed)

    employee_id = None
    if account is None:
        candidate = clean_employee_id(typed)
        if EMPLOYEE_ID_PATTERN.match(candidate):
            employee_id = candidate

    if account is not None:
        reference = account
    elif employee_id is not None:
        reference = data['employeeCredential']
    else:
        reference = _DUMMY

    derived = _derive(password, reference['salt'])
    matches = hmac.compare_digest(derived, reference['hash'])

    # `matches` is checked after the branch, not inside it, so an unrecognised
    # username cannot pass by colliding with the dummy hash.
    if not matches:
        return None

    if account is not None:
        # A fresh dict rather than the loaded entry itself, so a caller cannot
        # mutate the cached data by editing what it was handed.
        return {
            'username': typed,
            'role': account['role'],
            'displayName': account['displayName'],
        }

    if employee_id is not None:
        # The number is the only display name available - see the docstring on why
        # this cannot look up a name. The frontend replaces it with the real one
        # once the profile loads.
        return {
            'username': employee_id,
            'role': ROLE_EMPLOYEE,
            'displayName': employee_id,
        }

    return None

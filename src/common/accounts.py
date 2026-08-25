"""
Who can sign in, and which of the two roles they get.

Two accounts, hard-coded. That is the honest shape of this feature: the brief is
one officials login and one employee login, and there is no user-management
screen, no invite flow and nothing that creates a third account. A DynamoDB users
table would be real machinery in service of a list that never changes.

Passwords are stored as PBKDF2-SHA256 hashes rather than plaintext. The reasoning
is not that the hash is secret - the demo passwords are in README.md, because
nobody can use this app without them - it is that the *code* should never be the
place a password is written down. The day these accounts become real ones, the
hashes get replaced and nothing else here changes. A plaintext comparison would
have to be found and rewritten first, which is exactly the edit that gets missed.

Where this would go next, in order: move ACCOUNTS to Secrets Manager, then drop
this module entirely for a Cognito user pool. Both are more than this phase
needs; neither is blocked by anything below.
"""
import hashlib
import hmac

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

ACCOUNTS = {
    'hr.admin': {
        'salt': '89cfb6356f147f197d5131fbc77a9e3f',
        'hash': 'd5de7d5e4a770324e5d7a48f2a257581e832072e3ff0cfe7a049baa18d57555b',
        'role': ROLE_OFFICIAL,
        'displayName': 'HR Admin',
    },
    'employee': {
        'salt': '821ffd37011d86d96e9551dbe76a3776',
        'hash': '33f8556d1f6946cd31f21f031ca333f0cfd5ed2c3b48b913760f148d2ec68374',
        'role': ROLE_EMPLOYEE,
        'displayName': 'Employee',
    },
}

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


def verify_credentials(username, password):
    """
    The account, or None. Never says which half was wrong.

    An unknown username still pays for a full PBKDF2 derivation. Without that,
    "no such user" returns in microseconds and "wrong password" takes 200k
    rounds, and the difference is measurable over the network - which turns this
    endpoint into a way to find out that `hr.admin` exists before guessing at it.
    """
    account = ACCOUNTS.get(username if isinstance(username, str) else '')
    reference = account or _DUMMY

    derived = _derive(password, reference['salt'])
    matches = hmac.compare_digest(derived, reference['hash'])

    # `account and matches`, in that order, so an unknown username cannot pass by
    # colliding with the dummy hash.
    return account if (account is not None and matches) else None

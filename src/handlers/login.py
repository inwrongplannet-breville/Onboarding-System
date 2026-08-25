"""
POST /login - the only route the authorizer does not sit in front of.

Verifies a username and password against common/accounts.py and hands back a
signed token carrying the caller's role. Everything else in the API is reached
with that token; see handlers/authorizer.py for the other end of it.

The role travels *inside* the signature, not beside it. The response repeats it
as a convenience so the UI knows which view to paint, but nothing trusts that
copy - each subsequent request is authorised from the token alone, so editing the
role in sessionStorage changes what the browser draws and not one thing about
what the API will do.

Not here, and deliberately: rate limiting. Two hard-coded accounts with a 200k
round PBKDF2 derivation each is not a realistic online guessing target, and the
right place for a real limit is an API Gateway usage plan or WAF rather than
Python holding attempt counts in a Lambda that scales to zero. Worth adding
before this stack holds anything real.
"""
from common import responses
from common.accounts import verify_credentials
from common.handler import api_handler, parse_body
from common.tokens import DEFAULT_TTL_SECONDS, secret, sign

# One message for every failure mode. "No such user" and "wrong password" told
# apart is a way to confirm which usernames exist, one guess at a time.
_REJECTED = 'Incorrect username or password.'


@api_handler
def lambda_handler(event, context):
    body = parse_body(event)

    username = body.get('username')
    password = body.get('password')

    # Checked before verify_credentials so a body with no fields at all is a 400
    # about the request rather than a 401 about credentials nobody sent.
    if not isinstance(username, str) or not isinstance(password, str) or not username or not password:
        return responses.bad_request('Username and password are required.', {
            'username': '' if username else 'Username is required.',
            'password': '' if password else 'Password is required.',
        })

    account = verify_credentials(username, password)
    if account is None:
        return responses.unauthorized(_REJECTED)

    token = sign({'sub': username, 'role': account['role']}, secret())

    return responses.ok({
        'token': token,
        'role': account['role'],
        'displayName': account['displayName'],
        # Seconds, so the client can decide to sign out early rather than
        # discovering the expiry as a failed request mid-edit.
        'expiresIn': DEFAULT_TTL_SECONDS,
    })

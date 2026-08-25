"""
Signed session tokens - a small HS256 JWT, written out here rather than imported.

This is the actual security boundary of the two-role split. The browser holds a
token and a role; nothing about either is trusted. Every request is authorised by
re-deriving the role from a signature only the backend can produce, which is what
stops an employee from editing `"role":"employee"` into `"official"` in devtools
and getting a working officials token out of it.

Why no PyJWT. src/requirements.txt is empty on purpose - boto3 ships in the Lambda
runtime, so today there is nothing to install, no build step and no lockfile to
keep current. Taking a dependency for one HMAC and two base64 calls would end
that for the whole project, and the standard library has both.

What that trades away is worth naming: PyJWT is audited and this is not. So the
verify path below is deliberately narrow - one algorithm, no `crit`, no `kid`, no
JWK fetching, no `none`. Every one of those is a feature this system does not
need and a way in if it is wrong.
"""
import base64
import hmac
import hashlib
import json
import os
import time

# The one algorithm this module speaks. Anything else in a header is a rejection,
# not a negotiation - see verify().
ALGORITHM = 'HS256'

# Eight hours: a working day, so a shift does not end with a surprise logout, and
# a token left in a closed laptop is stale by morning. sessionStorage in the
# browser is the other half of that (see js/auth.js) - closing the tab is a
# logout regardless of what the token still says.
DEFAULT_TTL_SECONDS = 8 * 60 * 60

# Who minted the token, and which deployment it is for. Both are checked on the
# way back in.
#
# Without `aud`, a token from the dev stack is accepted by a prod stack deployed
# with the same signing key - and "the same signing key" is exactly what happens
# when someone copies a deploy command. Stage-scoping the audience makes that
# mistake a 401 rather than a cross-environment authentication.
ISSUER = 'onboarding-system'

_HEADER = {'alg': ALGORITHM, 'typ': 'JWT'}


class InvalidToken(Exception):
    """
    Unusable token, for any reason: malformed, wrong algorithm, bad signature,
    expired.

    One exception for all four on purpose. A caller that can tell "expired" from
    "forged" will eventually report the difference to whoever sent it, and
    "expired" is a free confirmation that the rest of the token was genuine.
    Callers that legitimately need to know the difference do not exist here: the
    authorizer's only two answers are Allow and Unauthorized.
    """


def _b64encode(raw):
    """base64url without padding, as the JWT spec requires."""
    return base64.urlsafe_b64encode(raw).rstrip(b'=')


def _b64decode(segment):
    """
    base64url, padding restored.

    urlsafe_b64decode raises on unpadded input, and JWT segments are always
    unpadded - so a decoder that forgets this rejects every real token. The
    padding length is whatever brings the segment up to a multiple of four.
    """
    padding = b'=' * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _signature(signing_input, secret):
    return _b64encode(
        hmac.new(secret.encode('utf-8'), signing_input, hashlib.sha256).digest()
    )


def _encode_segment(payload):
    # Compact separators and sorted keys so the same claims always produce the
    # same bytes. Nothing depends on that today; a test comparing two tokens
    # would, and a signature over unstable bytes is a bad afternoon.
    raw = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')
    return _b64encode(raw)


def audience():
    """
    The deployment a token is minted for - the API Gateway stage name.

    Read from the environment rather than hard-coded, and with no default: a
    default is what makes two stages share an audience, which is the whole thing
    `aud` exists to prevent.
    """
    value = os.environ.get('STAGE')
    if not value:
        raise RuntimeError('STAGE is not set. Tokens cannot be scoped to a deployment.')
    return value


def sign(claims, secret, ttl_seconds=DEFAULT_TTL_SECONDS):
    """
    A signed token carrying `claims`, plus the four the caller should not have to
    remember: `iat`, `exp`, `iss` and `aud`.

    All four are set here rather than left to the caller because verify() refuses
    a token missing any of them. Making the signer responsible is what keeps the
    two ends consistent - a caller that forgets one produces a token nothing will
    accept, which is a confusing way to find out.
    """
    issued_at = int(time.time())
    payload = dict(claims,
                   iat=issued_at,
                   exp=issued_at + ttl_seconds,
                   iss=ISSUER,
                   aud=audience())

    signing_input = _encode_segment(_HEADER) + b'.' + _encode_segment(payload)
    token = signing_input + b'.' + _signature(signing_input, secret)
    return token.decode('ascii')


def verify(token, secret):
    """
    The claims in `token`, or InvalidToken.

    Order matters here. The signature is checked before anything in the payload
    is believed, because until it passes, the payload is just whatever the caller
    typed - including its `exp`.
    """
    if not isinstance(token, str) or not token:
        raise InvalidToken('No token.')

    parts = token.encode('ascii', 'ignore').split(b'.')
    if len(parts) != 3:
        raise InvalidToken('Malformed token.')

    header_segment, payload_segment, signature = parts
    signing_input = header_segment + b'.' + payload_segment

    # compare_digest, not ==. String equality returns as soon as two bytes
    # differ, and the time it took to do that is a measurement of how much of a
    # guessed signature was right - which is enough, over enough attempts, to
    # build a valid one a byte at a time.
    if not hmac.compare_digest(signature, _signature(signing_input, secret)):
        raise InvalidToken('Bad signature.')

    try:
        header = json.loads(_b64decode(header_segment))
        claims = json.loads(_b64decode(payload_segment))
    except (ValueError, TypeError, base64.binascii.Error):
        raise InvalidToken('Malformed token.')

    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise InvalidToken('Malformed token.')

    # The `alg: none` hole, closed. A verifier that reads the algorithm out of
    # the header and does what it says will happily accept a token that declares
    # it is not signed - the attacker picks the algorithm. This one has already
    # checked an HS256 signature above; the header is only allowed to agree.
    if header.get('alg') != ALGORITHM:
        raise InvalidToken('Unsupported algorithm.')

    expires_at = claims.get('exp')
    # A missing exp is invalid, not eternal. sign() always writes one, so the
    # only tokens without one are hand-built - and treating absent as "never
    # expires" is how a stolen token stays useful forever.
    if not isinstance(expires_at, int) or isinstance(expires_at, bool):
        raise InvalidToken('Token has no expiry.')
    if expires_at <= int(time.time()):
        raise InvalidToken('Token has expired.')

    # Checked after the signature, like everything else in the payload, and
    # checked at all so that a correctly signed token from another deployment or
    # another issuer is refused rather than honoured.
    if claims.get('iss') != ISSUER:
        raise InvalidToken('Token was not issued by this system.')
    if claims.get('aud') != audience():
        raise InvalidToken('Token was issued for another deployment.')

    return claims


# Held across invocations of a warm Lambda, the same way common/db.py holds its
# boto3 table. A Secrets Manager call per request would be a network round trip
# and a bill on the hot path of every single API call.
#
# But cached *with an expiry*, which the first version of this was missing and is
# the difference between revocation working and only appearing to. A warm
# container that had already fetched the key would have gone on trusting it for
# its whole life - minutes or hours - so rotating the secret would not have
# invalidated anything on any container that was already running. The advertised
# "revoke everything now" would have been "revoke everything, eventually,
# unpredictably".
#
# Sixty seconds, matching the authorizer's own result cache, which puts the
# worst-case lag between rotating the key and every token failing at about two
# minutes. The cost is one Secrets Manager call per minute per warm container.
_SECRET_TTL_SECONDS = 60

_cached_secret = None
_cached_at = 0.0


def secret():
    """
    The signing key.

    Two sources, in order:

      $JWT_SECRET       tests and `sam local` - set directly, no AWS call
      $JWT_SECRET_ARN   deployed - fetched from Secrets Manager, then cached

    It used to be a plain Lambda environment variable, and that was the finding
    that moved it: `lambda:GetFunctionConfiguration` returns environment
    variables in plaintext, so any principal holding that permission - commonly
    granted, and read-only-looking - could read the signing key and mint a valid
    officials token for any user. Secrets Manager makes reading it an explicit
    `secretsmanager:GetSecretValue` on one ARN, which in template.yaml is granted
    to exactly two functions.

    It also makes revocation possible. Rotating the secret invalidates every
    token in circulation, and that is now one API call rather than a redeploy.

    Missing is a hard failure, never an empty-string default: a default would
    mean a misconfigured stack signing tokens with a key written in this file,
    which is the same as not signing them at all.
    """
    global _cached_secret, _cached_at

    if _cached_secret and (time.monotonic() - _cached_at) < _SECRET_TTL_SECONDS:
        return _cached_secret

    direct = os.environ.get('JWT_SECRET')
    if direct:
        _cached_secret = direct
        _cached_at = time.monotonic()
        return _cached_secret

    arn = os.environ.get('JWT_SECRET_ARN')
    if not arn:
        raise RuntimeError(
            'Neither JWT_SECRET nor JWT_SECRET_ARN is set. '
            'The stack cannot sign or verify tokens.')

    # Imported here rather than at module scope so that tests and `sam local`,
    # which take the JWT_SECRET path above, never build a client they will not use.
    import boto3

    value = boto3.client('secretsmanager').get_secret_value(SecretId=arn)['SecretString']
    if not value:
        raise RuntimeError('The secret at JWT_SECRET_ARN is empty.')

    _cached_secret = value
    _cached_at = time.monotonic()
    return _cached_secret

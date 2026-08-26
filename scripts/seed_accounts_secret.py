"""
Populate AccountsSecret with real login credentials, after `sam deploy`.

template.yaml cannot generate a meaningful salted hash the way it generates the
JWT signing key - GenerateSecretString produces a random string, and a login
credential has to correspond to an actual chosen password. So AccountsSecret
deploys with an all-zero placeholder that matches nothing (see the resource
comment in template.yaml), and this script is the second, deliberately separate
step: it derives the salt+hash for the demo passwords the same way
common/accounts.py verifies them, and pushes the result into the secret with
`aws secretsmanager put-secret-value`.

Until this runs, POST /login 401s for every username - that is the intended
failure mode for a forgotten seeding step, not a bug to route around.

Usage:
    py scripts/seed_accounts_secret.py
    py scripts/seed_accounts_secret.py --secret-arn arn:aws:secretsmanager:...
    py scripts/seed_accounts_secret.py --hr-password ... --employee-password ...

ARN resolution, in order: --secret-arn, $ACCOUNTS_SECRET_ARN, then the
AccountsSecretArn output of the onboarding-system-dev CloudFormation stack via
the AWS CLI - the same lookup seed_employees.py uses for ApiBaseUrl/TableName.

Password resolution, in order: --hr-password/--employee-password, then
$ACCOUNTS_HR_PASSWORD/$ACCOUNTS_EMPLOYEE_PASSWORD, then the demo passwords in
README.md. Passing a real password on the command line leaves it in shell
history - fine for the demo passwords, not for anything real; use the
environment variables instead once these accounts stop being a demo.

Uses the AWS CLI rather than boto3, for the same reason seed_employees.py does:
`aws login`'s cached credentials are not boto3-readable without botocore[crt],
and the CLI is already a hard dependency here for reading the stack output.
"""
import argparse
import io
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile

STACK_NAME = 'onboarding-system-dev'
REGION = 'eu-north-1'

# The demo accounts, matching src/common/accounts.py and README.md. Only a
# default - --hr-password/--employee-password or the environment override it.
HR_USERNAME = 'hr.admin'
DEFAULT_HR_PASSWORD = 'onboard-2026'
DEFAULT_EMPLOYEE_PASSWORD = 'welcome-2026'

# src/ is not normally on sys.path outside a Lambda - added here so this script
# can import the one thing it must never reimplement: _derive. The PBKDF2
# algorithm and iteration count are security-critical and have to match
# common/accounts.py exactly, byte for byte - duplicating that call here is
# the kind of drift that fails silently at the login screen months later.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from common.accounts import _derive, _ITERATIONS, ROLE_OFFICIAL  # noqa: E402


def stack_output(key):
    """One output off the deployed stack, via the AWS CLI."""
    try:
        output = subprocess.run(
            ['aws', 'cloudformation', 'describe-stacks',
             '--stack-name', STACK_NAME, '--region', REGION,
             '--query', "Stacks[0].Outputs[?OutputKey=='" + key + "'].OutputValue",
             '--output', 'text'],
            capture_output=True, text=True, check=True, shell=(os.name == 'nt'),
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as error:
        raise SystemExit(
            'Could not read the {} stack output. Pass it explicitly instead.\n{}'.format(
                key, error))

    if not output:
        raise SystemExit('Stack {} has no {} output.'.format(STACK_NAME, key))
    return output


def resolve_secret_arn(explicit):
    if explicit:
        return explicit

    from_env = os.environ.get('ACCOUNTS_SECRET_ARN')
    if from_env:
        return from_env

    print('No --secret-arn or $ACCOUNTS_SECRET_ARN; reading it from the {} stack...'.format(
        STACK_NAME))
    return stack_output('AccountsSecretArn')


def resolve_passwords(hr_password, employee_password):
    return (
        hr_password or os.environ.get('ACCOUNTS_HR_PASSWORD') or DEFAULT_HR_PASSWORD,
        employee_password or os.environ.get('ACCOUNTS_EMPLOYEE_PASSWORD')
        or DEFAULT_EMPLOYEE_PASSWORD,
    )


def credential(password):
    """A fresh {'salt', 'hash'} pair for `password`, using accounts.py's own KDF."""
    salt = secrets.token_hex(16)
    return {'salt': salt, 'hash': _derive(password, salt)}


def put_secret(secret_arn, payload):
    """
    One `aws secretsmanager put-secret-value` call, the value passed as a temp
    file rather than inline - inline JSON on Windows is a quoting minefield
    (PowerShell strips the double quotes before the CLI sees them), and a plain
    UTF-8 file with no BOM sidesteps that entirely. Same trick as
    seed_employees.py's aws_json().
    """
    temp_dir = tempfile.mkdtemp(prefix='onboarding-accounts-')
    try:
        path = os.path.join(temp_dir, 'accounts.json')
        with io.open(path, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(payload))

        result = subprocess.run(
            ['aws', 'secretsmanager', 'put-secret-value',
             '--secret-id', secret_arn, '--region', REGION,
             '--secret-string', 'file://' + path],
            capture_output=True, text=True, shell=(os.name == 'nt'),
        )
        if result.returncode != 0:
            raise SystemExit('AWS CLI failed:\n{}'.format(result.stderr.strip()))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--secret-arn', help='ARN of the deployed AccountsSecret')
    parser.add_argument('--hr-password', help='password for the hr.admin account')
    parser.add_argument('--employee-password',
                        help='shared password for every employee number')
    args = parser.parse_args()

    secret_arn = resolve_secret_arn(args.secret_arn)
    hr_password, employee_password = resolve_passwords(args.hr_password, args.employee_password)

    payload = {
        'accounts': {
            HR_USERNAME: dict(credential(hr_password), role=ROLE_OFFICIAL,
                              displayName='HR Admin'),
        },
        'employeeCredential': credential(employee_password),
    }

    print('Secret: {}'.format(secret_arn))
    put_secret(secret_arn, payload)
    print('Done. {} rounds of PBKDF2-SHA256, salt and hash only - '
          'no password was written anywhere.'.format(_ITERATIONS))


if __name__ == '__main__':
    main()

"""
Reset the dev employee table: hard-wipe it, repopulate it through the public API.

These six people were the Phase 1 mock data in js/data.js. They moved here when the
UI was wired to the backend: the frontend now has no employee records in it at all,
so the fixtures live on the side of the wire that actually stores them.

Seeding deliberately drives the REST API rather than boto3 against DynamoDB, so it
exercises the same validation, the same transactional create and the same checklist
handling the browser will - a broken seed is then a broken API rather than a mystery.

Wiping cannot, and the reason is worth knowing. DELETE /employees/{id} archives: it
stamps the profile and leaves every row in place, including the email uniqueness
guard. Wiping through the API would therefore appear to work - the employees do
leave GET /employees - and then every POST in the seed that followed would fail with
a 409, because all six addresses are still reserved by records the API can no longer
show you. So --wipe goes straight at the table and really does delete.

That asymmetry is the design working as intended, not a hole in it: the API has no
hard delete because employee history should not be destroyable over HTTP. Resetting
a dev table is a deliberate act against the table, and this is it.

The table calls go through the AWS CLI rather than boto3, which is not the obvious
choice and is deliberate. `aws login` - the browser-based console login this project
uses - caches credentials that boto3 can only read if `botocore[crt]` is installed;
without it every boto3 call dies on MissingDependencyException while the CLI sitting
next to it works fine. The CLI is already a hard dependency here for reading the
stack outputs, so leaning on it costs nothing and removes a dependency that is easy
to not have.

Usage:
    py scripts/seed_employees.py --wipe --seed --yes
    py scripts/seed_employees.py --seed --base-url https://... /dev

Base URL resolution, in order: --base-url, $API_BASE_URL, then the ApiBaseUrl output
of the onboarding-system-dev CloudFormation stack via the AWS CLI. --wipe resolves
the table name the same way, from the stack's TableName output.
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

STACK_NAME = 'onboarding-system-dev'
REGION = 'eu-north-1'

# (employee, [checklist item ids to tick]) - the varied progress from Phase 1, which
# is what makes the list view's status filter and progress bars worth looking at.
FIXTURES = [
    (
        {
            'firstName': 'Priya', 'lastName': 'Sharma',
            'email': 'priya.sharma@breville.com', 'phone': '+61 412 883 016',
            'department': 'Engineering', 'jobTitle': 'Software Engineer',
            'manager': 'Santosh Kumar', 'startDate': '2026-07-06',
            'employmentType': 'Full-time',
        },
        ['offer-letter', 'id-proof', 'bank-details', 'laptop',
         'email-account', 'access-card', 'induction', 'policy-ack'],   # Onboarded
    ),
    (
        {
            'firstName': 'Daniel', 'lastName': 'Okafor',
            'email': 'daniel.okafor@breville.com', 'phone': '+61 431 507 224',
            'department': 'Engineering', 'jobTitle': 'QA Engineer',
            'manager': 'Santosh Kumar', 'startDate': '2026-08-10',
            'employmentType': 'Full-time',
        },
        ['offer-letter', 'id-proof', 'bank-details', 'laptop', 'email-account'],
    ),
    (
        {
            'firstName': 'Mei Lin', 'lastName': 'Tan',
            'email': 'meilin.tan@breville.com', 'phone': '+61 402 119 763',
            'department': 'Finance', 'jobTitle': 'Financial Analyst',
            'manager': 'Rachel Adams', 'startDate': '2026-08-24',
            'employmentType': 'Full-time',
        },
        ['offer-letter', 'id-proof'],
    ),
    (
        {
            'firstName': 'Arjun', 'lastName': 'Nair',
            'email': 'arjun.nair@breville.com', 'phone': '+61 448 620 195',
            'department': 'Operations', 'jobTitle': 'Supply Chain Coordinator',
            'manager': 'Grace Whitmore', 'startDate': '2026-09-01',
            'employmentType': 'Contract',
        },
        [],                                                            # Pending
    ),
    (
        {
            'firstName': 'Sofia', 'lastName': 'Marchetti',
            'email': 'sofia.marchetti@breville.com', 'phone': '+61 423 774 508',
            'department': 'HR', 'jobTitle': 'HR Coordinator',
            'manager': 'Grace Whitmore', 'startDate': '2026-08-17',
            'employmentType': 'Full-time',
        },
        ['offer-letter', 'id-proof', 'bank-details', 'laptop',
         'email-account', 'access-card'],
    ),
    (
        {
            'firstName': 'Liam', 'lastName': 'Byrne',
            'email': 'liam.byrne@breville.com', 'phone': '+61 437 285 941',
            'department': 'Engineering', 'jobTitle': 'Data Engineering Intern',
            'manager': 'Santosh Kumar', 'startDate': '2026-09-14',
            'employmentType': 'Intern',
        },
        [],
    ),
]


class ApiError(Exception):
    pass


def call(base_url, method, path, body=None):
    """One request. Raises ApiError on any non-2xx so a failed seed is loud."""
    data = json.dumps(body).encode('utf-8') if body is not None else None
    headers = {'Content-Type': 'application/json'} if data else {}
    request = urllib.request.Request(base_url + path, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        detail = error.read().decode('utf-8', 'replace')
        raise ApiError('{} {} -> {} {}'.format(method, path, error.code, detail)) from None
    except urllib.error.URLError as error:
        raise ApiError('Could not reach {}: {}'.format(base_url, error.reason)) from None


def resolve_base_url(explicit):
    if explicit:
        return explicit.rstrip('/')

    from_env = os.environ.get('API_BASE_URL')
    if from_env:
        return from_env.rstrip('/')

    print('No --base-url or $API_BASE_URL; reading it from the {} stack...'.format(STACK_NAME))
    return stack_output('ApiBaseUrl').rstrip('/')


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


def resolve_table_name(explicit):
    """Same resolution order as the base URL, off the stack's other output."""
    if explicit:
        return explicit

    from_env = os.environ.get('TABLE_NAME')
    if from_env:
        return from_env

    print('No --table or $TABLE_NAME; reading it from the {} stack...'.format(STACK_NAME))
    return stack_output('TableName')


def aws_json(args, payloads=None):
    """
    One AWS CLI call returning parsed JSON, with any dict arguments written to
    temp files and passed as file:// .

    The file:// detour is not decoration. Passing inline JSON to the CLI on
    Windows is a quoting minefield - PowerShell strips the double quotes before
    the exe sees them, and a BOM from the wrong text writer makes the CLI reject
    the file it just read. A plain UTF-8 file with no BOM sidesteps both.
    """
    temp_dir = tempfile.mkdtemp(prefix='onboarding-wipe-')
    try:
        argv = ['aws'] + args
        for flag, payload in (payloads or {}).items():
            path = os.path.join(temp_dir, flag.strip('-') + '.json')
            with io.open(path, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(json.dumps(payload))
            argv += [flag, 'file://' + path]

        result = subprocess.run(argv, capture_output=True, text=True,
                                shell=(os.name == 'nt'))
        if result.returncode != 0:
            raise SystemExit('AWS CLI failed:\n  {}\n{}'.format(
                ' '.join(args), result.stderr.strip()))

        return json.loads(result.stdout) if result.stdout.strip() else None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def scan_keys(table_name):
    """Every PK/SK in the table, following pagination."""
    keys = []
    start_key = None

    while True:
        args = ['dynamodb', 'scan', '--table-name', table_name,
                '--region', REGION, '--projection-expression', 'PK,SK',
                '--output', 'json']
        payloads = {}
        if start_key:
            payloads['--exclusive-start-key'] = start_key

        result = aws_json(args, payloads)
        keys.extend({'PK': i['PK'], 'SK': i['SK']} for i in result.get('Items', []))

        start_key = result.get('LastEvaluatedKey')
        if not start_key:
            return keys


def wipe(table_name, assume_yes):
    """
    Delete every row in the table - employees, checklist items and email guards.

    A Scan rather than GET /employees, because the API cannot see archived
    employees and their guards are exactly what would break the seed that
    follows. Scan reaches everything; the list endpoint reaches what is live.
    """
    keys = scan_keys(table_name)

    if not keys:
        print('Table {} is already empty. Nothing to wipe.'.format(table_name))
        return

    partitions = {k['PK']['S'] for k in keys}
    employees = {p for p in partitions if p.startswith('EMP#')}
    guards = {p for p in partitions if p.startswith('EMAIL#')}

    print('\nAbout to hard-delete {} row(s) from {}:'.format(len(keys), table_name))
    print('  {} employee partition(s), archived ones included'.format(len(employees)))
    print('  {} email uniqueness guard(s)'.format(len(guards)))

    if not assume_yes:
        # Irreversible, and unlike the API it really does destroy the history -
        # so make someone type the word.
        if input('\nType "delete" to confirm: ').strip().lower() != 'delete':
            raise SystemExit('Aborted.')

    # 25 is the BatchWriteItem ceiling. Unprocessed items are retried rather than
    # ignored - DynamoDB returns them on throttling, and a wipe that quietly left
    # rows behind would strand exactly the guards that break the next seed.
    deleted = 0
    for start in range(0, len(keys), 25):
        pending = [{'DeleteRequest': {'Key': key}} for key in keys[start:start + 25]]

        for attempt in range(5):
            result = aws_json(
                ['dynamodb', 'batch-write-item', '--region', REGION, '--output', 'json'],
                {'--request-items': {table_name: pending}},
            )
            unprocessed = (result or {}).get('UnprocessedItems', {}).get(table_name, [])
            deleted += len(pending) - len(unprocessed)
            if not unprocessed:
                break
            pending = unprocessed
            time.sleep(2 ** attempt)
        else:
            raise SystemExit('Gave up with {} row(s) still undeleted.'.format(len(pending)))

    print('Wiped {} row(s).'.format(deleted))


def seed(base_url):
    print('\nSeeding {} employees...'.format(len(FIXTURES)))

    for profile, done_items in FIXTURES:
        created = call(base_url, 'POST', '/employees', profile)
        employee_id = created['id']

        for item_id in done_items:
            created = call(
                base_url, 'PATCH',
                '/employees/{}/checklist/{}'.format(employee_id, item_id),
                {'done': True},
            )

        print('  {}  {:<18} {:<12} {}/{} {}'.format(
            employee_id,
            profile['firstName'] + ' ' + profile['lastName'],
            profile['department'],
            created['progress']['done'], created['progress']['total'],
            created['status'],
        ))

    total = call(base_url, 'GET', '/employees')['count']
    print('\nDone. Table now holds {} employee(s).'.format(total))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--wipe', action='store_true',
                        help='hard-delete every row in the table first, archived ones included')
    parser.add_argument('--seed', action='store_true', help='create the six fixture employees')
    parser.add_argument('--yes', action='store_true', help='skip the wipe confirmation prompt')
    parser.add_argument('--base-url', help='API base URL, e.g. https://xxxx.execute-api.../dev')
    parser.add_argument('--table', help='DynamoDB table name, for --wipe')
    args = parser.parse_args()

    if not args.wipe and not args.seed:
        parser.error('nothing to do - pass --wipe, --seed, or both')

    try:
        if args.wipe:
            table_name = resolve_table_name(args.table)
            print('Table: {}'.format(table_name))
            wipe(table_name, args.yes)

        if args.seed:
            base_url = resolve_base_url(args.base_url)
            print('API: {}'.format(base_url))
            seed(base_url)
    except ApiError as error:
        raise SystemExit('\nFAILED: {}'.format(error))


if __name__ == '__main__':
    main()

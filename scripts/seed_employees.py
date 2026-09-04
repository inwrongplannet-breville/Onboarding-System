"""
Reset the two dev tables: hard-wipe them, then repopulate them through the public
API with 30 deterministic people - 10 still onboarding, 10 promoted employees and
10 promoted interns. Every intern points at one of the promoted employees.

The fixtures live here because the frontend has no local employee data. E1001 is
kept as a promoted employee so the documented employee demo login remains useful;
the remaining details are synthetic and stable across reseeds.

Seeding deliberately drives the REST API rather than boto3 against DynamoDB, so it
exercises the same validation, the same create, the same checklist handling and the
same promote sequences the browser will - a broken seed is then a broken API rather
than a mystery. That matters especially here: promotion is a sequence of small
endpoints (see docs/database-design.md#promotion), and running that sequence
through the same client the UI uses is the cheapest end-to-end test of the ordering
it depends on.

Wiping cannot go through the API. DELETE /employees/{id} archives: it stamps the
record and leaves it in the table. Wiping through the API would appear to work -
the employees do leave GET /employees - and then every reseed would pile a fresh
set of records on top of the archived ones, growing the table on every cycle with
no way to ever clear it. So --wipe goes straight at both tables and really
does delete.

That asymmetry is the design working as intended, not a hole in it: the API has no
hard delete because employee history should not be destroyable over HTTP. Resetting
a dev table is a deliberate act against the table, and this is it.

(Seeding without a wipe now 409s on the first fixture rather than duplicating it.
The employee number is the partition key, so a second POST under E1001 is refused
by the conditional PutItem - which makes a forgotten --wipe a loud failure instead
of the quiet double-seed it was when the key was a UUID. That is an improvement,
and it is still not a reason to skip --wipe: the archived records stay.)

The table calls go through the AWS CLI rather than boto3, which is not the obvious
choice and is deliberate. `aws login` - the browser-based console login this project
uses - caches credentials that boto3 can only read if `botocore[crt]` is installed;
without it every boto3 call dies on MissingDependencyException while the CLI sitting
next to it works fine. The CLI is already a hard dependency here for reading the
stack outputs, so leaning on it costs nothing and removes a dependency that is easy
to not have.

Seeding now signs in first. Every write route is behind the authorizer, so a
script driving the public API needs a token like any other client - it posts the
officials credentials to /login and carries the bearer token from there on. That
follows from seeding through the API rather than around it, and it is worth
keeping: if the login route or the authorizer breaks, the seed fails loudly
instead of the browser being the first thing to find out.

Credentials resolution, in order: --username/--password, then $ONBOARDING_USERNAME
and $ONBOARDING_PASSWORD, then the demo officials account. --wipe needs no
credentials at all - it goes at the table, not the API.

Usage:
    py scripts/seed_employees.py --wipe --seed --yes
    py scripts/seed_employees.py --seed --base-url https://... /dev
    py scripts/seed_employees.py --seed --username hr.admin --password ...

Base URL resolution, in order: --base-url, $API_BASE_URL, then the ApiBaseUrl output
of the onboarding-system-dev CloudFormation stack via the AWS CLI. --wipe resolves
both table names the same way, from the stack's OnboardingTableName /
EmployeeTableName outputs.
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

# The demo officials account, matching src/common/accounts.py. Only a default -
# --username/--password or the environment override it, which is what a stack
# with real accounts would use.
DEFAULT_USERNAME = 'hr.admin'
DEFAULT_PASSWORD = 'onboard-2026'

# One dict per hire. `promote` says which sequence to run once the checklist is
# ticked: None leaves them in OnboardingTable with varied progress; 'employee'
# and 'intern' move completed records into the shared EmployeeTable. Interns are
# distinguished there by entityType and carry a reporting manager id.
#
# Order is load-bearing. Every 'intern' entry's `manager` must name an
# 'employee' entry that appears earlier in this list - promote_to_intern.py
# refuses a manager who is not already a live EmployeeTable record, and this
# script runs the sequences in list order (see seed() below), the same way an
# HR user would have to: promote the manager before promoting anyone who
# reports to them.
#
# `employeeId` is supplied rather than assigned: it is the partition key, so
# these numbers are the fixtures' identity. Keeping them stable and contiguous
# means a reseed lands the same people on the same ids every time, and a
# hand-written URL like #/onboarding/E1023 keeps working across resets.
CHECKLIST_IDS = [
    'offer-letter', 'id-proof', 'bank-details', 'laptop',
    'email-account', 'access-card', 'induction', 'policy-ack',
]


def fixture(employee_id, first_name, last_name, department, job_title,
            manager_name, start_date, employment_type='Full-time',
            promote=None, reporting_manager_id=None, done_count=8):
    """Build one readable fixture while keeping generated contact data stable."""
    sequence = int(employee_id[1:]) - 1000
    result = {
        'profile': {
            'employeeId': employee_id,
            'firstName': first_name,
            'lastName': last_name,
            'email': '{}.{}@breville.com'.format(first_name, last_name).lower(),
            'phone': '+61 400 {:03d} {:03d}'.format(sequence, (sequence * 37) % 1000),
            'department': department,
            'jobTitle': job_title,
            'manager': manager_name,
            'startDate': start_date,
            'employmentType': employment_type,
        },
        'done': CHECKLIST_IDS[:done_count],
        'promote': promote,
    }
    if reporting_manager_id:
        result['manager'] = reporting_manager_id
    return result


# Promoted employees come first because every intern promotion must name an
# employee who already exists in EmployeeTable. Each employee manages one intern.
FIXTURES = [
    fixture('E1001', 'Maya', 'Chen', 'Engineering', 'Engineering Manager',
            'Nina Foster', '2026-05-04', promote='employee'),
    fixture('E1002', 'Noah', 'Williams', 'Operations', 'Operations Manager',
            'Nina Foster', '2026-05-11', promote='employee'),
    fixture('E1003', 'Olivia', 'Patel', 'Finance', 'Finance Manager',
            'Marcus Reed', '2026-05-18', promote='employee'),
    fixture('E1004', 'Ethan', 'Brooks', 'HR', 'People Operations Manager',
            'Marcus Reed', '2026-05-25', promote='employee'),
    fixture('E1005', 'Amelia', 'Nguyen', 'Engineering', 'Product Manager',
            'Nina Foster', '2026-06-01', promote='employee'),
    fixture('E1006', 'Lucas', 'Martin', 'Operations', 'Logistics Manager',
            'Nina Foster', '2026-06-08', promote='employee'),
    fixture('E1007', 'Isla', 'Thompson', 'Finance', 'Payroll Manager',
            'Marcus Reed', '2026-06-15', promote='employee'),
    fixture('E1008', 'Henry', 'Wilson', 'Engineering', 'Platform Manager',
            'Nina Foster', '2026-06-22', promote='employee'),
    fixture('E1009', 'Ava', 'Robinson', 'HR', 'Talent Manager',
            'Marcus Reed', '2026-06-29', promote='employee'),
    fixture('E1010', 'Jack', 'Anderson', 'Operations', 'Facilities Manager',
            'Nina Foster', '2026-07-06', promote='employee'),

    fixture('E1011', 'Chloe', 'Davis', 'Engineering', 'Software Engineering Intern',
            'Maya Chen', '2026-07-13', 'Intern', 'intern', 'E1001'),
    fixture('E1012', 'Leo', 'Garcia', 'Operations', 'Supply Chain Intern',
            'Noah Williams', '2026-07-20', 'Intern', 'intern', 'E1002'),
    fixture('E1013', 'Mia', 'Brown', 'Finance', 'Finance Intern',
            'Olivia Patel', '2026-07-27', 'Intern', 'intern', 'E1003'),
    fixture('E1014', 'Oscar', 'Lee', 'HR', 'People and Culture Intern',
            'Ethan Brooks', '2026-08-03', 'Intern', 'intern', 'E1004'),
    fixture('E1015', 'Grace', 'Taylor', 'Engineering', 'Product Intern',
            'Amelia Nguyen', '2026-08-10', 'Intern', 'intern', 'E1005'),
    fixture('E1016', 'Arlo', 'Harris', 'Operations', 'Logistics Intern',
            'Lucas Martin', '2026-08-17', 'Intern', 'intern', 'E1006'),
    fixture('E1017', 'Zoe', 'Clark', 'Finance', 'Payroll Intern',
            'Isla Thompson', '2026-08-24', 'Intern', 'intern', 'E1007'),
    fixture('E1018', 'Finn', 'Walker', 'Engineering', 'Platform Intern',
            'Henry Wilson', '2026-08-31', 'Intern', 'intern', 'E1008'),
    fixture('E1019', 'Ruby', 'Hall', 'HR', 'Talent Acquisition Intern',
            'Ava Robinson', '2026-09-07', 'Intern', 'intern', 'E1009'),
    fixture('E1020', 'Max', 'Young', 'Operations', 'Facilities Intern',
            'Jack Anderson', '2026-09-14', 'Intern', 'intern', 'E1010'),

    fixture('E1021', 'Sophie', 'King', 'Engineering', 'Software Engineer',
            'Maya Chen', '2026-09-21', done_count=0),
    fixture('E1022', 'Liam', 'Scott', 'Operations', 'Procurement Analyst',
            'Noah Williams', '2026-09-22', 'Contract', done_count=1),
    fixture('E1023', 'Emma', 'Green', 'Finance', 'Accounts Analyst',
            'Olivia Patel', '2026-09-23', done_count=2),
    fixture('E1024', 'James', 'Baker', 'HR', 'HR Advisor',
            'Ethan Brooks', '2026-09-24', done_count=3),
    fixture('E1025', 'Charlotte', 'Adams', 'Engineering', 'Product Designer',
            'Amelia Nguyen', '2026-09-25', 'Contract', done_count=4),
    fixture('E1026', 'Benjamin', 'Nelson', 'Operations', 'Inventory Planner',
            'Lucas Martin', '2026-09-28', done_count=5),
    fixture('E1027', 'Harper', 'Carter', 'Finance', 'Commercial Analyst',
            'Isla Thompson', '2026-09-29', done_count=6),
    fixture('E1028', 'William', 'Mitchell', 'Engineering', 'DevOps Engineer',
            'Henry Wilson', '2026-09-30', done_count=7),
    fixture('E1029', 'Evelyn', 'Perez', 'HR', 'Recruiter',
            'Ava Robinson', '2026-10-01', 'Contract', done_count=0),
    fixture('E1030', 'Alexander', 'Roberts', 'Operations', 'Facilities Coordinator',
            'Jack Anderson', '2026-10-02', done_count=4),
]


def validate_fixtures():
    """Fail before touching AWS if the dataset stops matching its 10/10/10 contract."""
    ids = [entry['profile']['employeeId'] for entry in FIXTURES]
    expected_ids = ['E{:04d}'.format(number) for number in range(1001, 1031)]
    if ids != expected_ids or len(set(ids)) != 30:
        raise ValueError('Fixtures must use each employee id from E1001 through E1030 once.')

    by_kind = {
        kind: [entry for entry in FIXTURES if entry['promote'] == kind]
        for kind in (None, 'employee', 'intern')
    }
    if {kind: len(entries) for kind, entries in by_kind.items()} != {
            None: 10, 'employee': 10, 'intern': 10}:
        raise ValueError('Fixtures must contain exactly 10 onboarding, 10 employee and 10 intern records.')

    promoted_employee_ids = set()
    for entry in FIXTURES:
        kind = entry['promote']
        if kind is not None and entry['done'] != CHECKLIST_IDS:
            raise ValueError('Every promoted fixture must complete all checklist items.')
        if kind is None and len(entry['done']) == len(CHECKLIST_IDS):
            raise ValueError('An onboarding fixture must remain short of completion.')
        if kind == 'employee':
            promoted_employee_ids.add(entry['profile']['employeeId'])
        if kind == 'intern':
            if entry['profile']['employmentType'] != 'Intern':
                raise ValueError('Every promoted intern must use the Intern employment type.')
            if entry.get('manager') not in promoted_employee_ids:
                raise ValueError('Every intern manager must be a previously promoted employee.')


validate_fixtures()


class ApiError(Exception):
    pass


def call(base_url, method, path, body=None, token=None):
    """One request. Raises ApiError on any non-2xx so a failed seed is loud."""
    data = json.dumps(body).encode('utf-8') if body is not None else None
    headers = {'Content-Type': 'application/json'} if data else {}
    if token:
        headers['Authorization'] = 'Bearer ' + token
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


def log_in(base_url, username, password):
    """
    An officials token, or a clear failure.

    The 401 case is called out separately because it is the one a person is most
    likely to hit and the least likely to diagnose from a status code: the stack
    was deployed with different accounts, or the demo passwords were changed.
    """
    print('Signing in as {}...'.format(username))
    try:
        result = call(base_url, 'POST', '/login',
                      {'username': username, 'password': password})
    except ApiError as error:
        if ' 401 ' in str(error):
            raise ApiError(
                'Login was refused for {}. Pass --username/--password, or set '
                '$ONBOARDING_USERNAME and $ONBOARDING_PASSWORD.'.format(username)
            ) from None
        if ' 403 ' in str(error):
            raise ApiError(
                'POST /login was forbidden, which usually means the deployed stack '
                'predates the login route. Deploy first: sam deploy.'
            ) from None
        raise

    if result.get('role') != 'official':
        raise ApiError(
            'Signed in as {}, whose role is "{}". Seeding writes employees, which '
            'needs an officials account.'.format(username, result.get('role'))
        )

    return result['token']


def resolve_credentials(username, password):
    return (
        username or os.environ.get('ONBOARDING_USERNAME') or DEFAULT_USERNAME,
        password or os.environ.get('ONBOARDING_PASSWORD') or DEFAULT_PASSWORD,
    )


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


# Which table each source resolves through: (--flag value, env var, stack
# output key, key attribute, id prefix). Two sources, not three - employees
# and interns share EmployeeTable now, told apart by entityType, so a single
# --wipe pass over 'employee' already clears both.
_TABLE_SOURCES = {
    'onboarding': ('table_onboarding', 'ONBOARDING_TABLE_NAME', 'OnboardingTableName', 'employeeKey', 'EMP#'),
    'employee': ('table_employee', 'EMPLOYEE_TABLE_NAME', 'EmployeeTableName', 'employeeKey', 'EMP#'),
}


def resolve_table_name(source, explicit_by_source):
    """Same resolution order as the base URL, off the stack's other outputs."""
    flag_attr, env_var, output_key, _, _ = _TABLE_SOURCES[source]
    explicit = explicit_by_source.get(flag_attr)
    if explicit:
        return explicit

    from_env = os.environ.get(env_var)
    if from_env:
        return from_env

    print('No --{} or ${}; reading it from the {} stack...'.format(
        flag_attr.replace('_', '-'), env_var, STACK_NAME))
    return stack_output(output_key)


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


def scan_keys(table_name, key_attribute):
    """
    Every key in the table, following pagination.

    The partition key only - neither table has a sort key, and a
    DeleteRequest carrying an SK the table does not have would fail the whole
    batch.

    `key_attribute` is spelled out by the caller rather than imported from
    common.keys: this script drives the AWS CLI rather than the handler
    package, and is run from a checkout that need not have src/ importable.
    """
    keys = []
    start_key = None

    while True:
        args = ['dynamodb', 'scan', '--table-name', table_name,
                '--region', REGION, '--projection-expression', key_attribute,
                '--output', 'json']
        payloads = {}
        if start_key:
            payloads['--exclusive-start-key'] = start_key

        result = aws_json(args, payloads)
        keys.extend({key_attribute: i[key_attribute]} for i in result.get('Items', []))

        start_key = result.get('LastEvaluatedKey')
        if not start_key:
            return keys


def wipe_table(table_name, key_attribute, id_prefix, noun, assume_yes):
    """
    Delete every item in one table. One item per record, so one delete each.

    A Scan rather than a GET route, because the officials list endpoints hide
    archived onboarding records and interruption leftovers respectively, and
    both of those are exactly what a reseed would otherwise silently duplicate.
    Scan reaches everything.
    """
    keys = scan_keys(table_name, key_attribute)

    if not keys:
        print('Table {} is already empty. Nothing to wipe.'.format(table_name))
        return

    matching = {k[key_attribute]['S'] for k in keys
                if k[key_attribute]['S'].startswith(id_prefix)}
    others = len(keys) - len(matching)

    print('\nAbout to hard-delete {} item(s) from {}:'.format(len(keys), table_name))
    print('  {} {}(s)'.format(len(matching), noun))
    if others:
        # Nothing should ever land here - each table holds exactly one kind of
        # item. Worth saying out loud rather than deleting in silence.
        print('  {} item(s) that are not {}s'.format(others, noun))

    if not assume_yes:
        # Irreversible, and unlike the API's archive-in-place DELETE it really
        # does destroy the history - so make someone type the word.
        if input('\nType "delete" to confirm: ').strip().lower() != 'delete':
            raise SystemExit('Aborted.')

    # 25 is the BatchWriteItem ceiling. Unprocessed items are retried rather than
    # ignored - DynamoDB returns them on throttling, and a wipe that quietly left
    # records behind is a reseed that quietly duplicates them.
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
            raise SystemExit('Gave up with {} item(s) still undeleted.'.format(len(pending)))

    print('Wiped {} item(s) from {}.'.format(deleted, table_name))


def wipe(table_names, assume_yes):
    """
    Wipe both tables. Order does not matter - each is independent.

    One pass over 'employee' clears employees and interns together now - they
    share EmployeeTable, told apart only by entityType, which the AWS-CLI Scan
    this drives does not read at all.
    """
    wipe_table(table_names['onboarding'], 'employeeKey', 'EMP#', 'onboarding record', assume_yes)
    wipe_table(table_names['employee'], 'employeeKey', 'EMP#', 'employee/intern', assume_yes)


def _promote(base_url, token, fixture, employee_id):
    """
    Run the one or two-step promote sequence for one fixture, through the same
    endpoints the frontend calls - see docs/database-design.md#promotion. This
    is deliberately not a single call: it is the cheapest end-to-end proof that
    the sequence's ordering (destination written before the onboarding row is
    deleted) actually works against a real deployment.
    """
    kind = fixture['promote']
    if kind is None:
        return None

    if kind == 'employee':
        # /staff/employees is a collection route - the id travels in the body,
        # not the path. See handlers/promote_to_employee.py.
        call(base_url, 'POST', '/staff/employees', {'employeeId': employee_id}, token=token)
        return None

    manager_id = fixture['manager']
    call(base_url, 'POST', '/staff/interns',
         {'employeeId': employee_id, 'reportingManagerId': manager_id}, token=token)
    call(base_url, 'POST', '/staff/employees/{}/interns'.format(manager_id),
         {'internId': employee_id}, token=token)
    return manager_id


def seed(base_url, token):
    print('\nSeeding {} people...'.format(len(FIXTURES)))

    onboarding_count = 0
    promoted = {'employee': 0, 'intern': 0}

    for fixture in FIXTURES:
        profile = fixture['profile']
        created = call(base_url, 'POST', '/employees', profile, token=token)
        employee_id = created['id']

        for item_id in fixture['done']:
            created = call(
                base_url, 'PATCH',
                '/employees/{}/checklist/{}'.format(employee_id, item_id),
                {'done': True}, token=token,
            )

        kind = fixture['promote']
        if kind is None:
            onboarding_count += 1
            where = '{}/{} {} - still onboarding'.format(
                created['progress']['done'], created['progress']['total'], created['status'])
        else:
            manager_id = _promote(base_url, token, fixture, employee_id)
            # The destructive last step of the sequence - see
            # handlers/delete_onboarding_record.py. Run only after the copy
            # above has already succeeded.
            call(base_url, 'DELETE', '/onboarding/{}'.format(employee_id), token=token)
            promoted[kind] += 1
            where = ('moved to employee dashboard' if kind == 'employee'
                     else 'moved to intern dashboard, reports to {}'.format(manager_id))

        print('  {}  {:<18} {:<12} {}'.format(
            employee_id, profile['firstName'] + ' ' + profile['lastName'],
            profile['department'], where))

    onboarding_total = call(base_url, 'GET', '/employees', token=token)['count']
    employee_total = call(base_url, 'GET', '/staff/employees', token=token)['count']
    intern_total = call(base_url, 'GET', '/staff/interns', token=token)['count']

    expected_totals = {
        'onboarding': sum(entry['promote'] is None for entry in FIXTURES),
        'employee': sum(entry['promote'] == 'employee' for entry in FIXTURES),
        'intern': sum(entry['promote'] == 'intern' for entry in FIXTURES),
    }
    actual_totals = {
        'onboarding': onboarding_total,
        'employee': employee_total,
        'intern': intern_total,
    }
    if actual_totals != expected_totals:
        raise ApiError('Seed totals do not match: expected {}, received {}.'.format(
            expected_totals, actual_totals))

    print('\nDone.')
    print('  Onboarding table: {} record(s)'.format(onboarding_total))
    # Employee and intern here are two views of one table, not two tables -
    # employee_total + intern_total is every row in EmployeeTable.
    print('  Employee table:   {} employee(s), {} intern(s)'.format(employee_total, intern_total))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--wipe', action='store_true',
                        help='hard-delete every row in both tables first')
    parser.add_argument('--seed', action='store_true', help='create the fixture people')
    parser.add_argument('--yes', action='store_true', help='skip the wipe confirmation prompts')
    parser.add_argument('--base-url', help='API base URL, e.g. https://xxxx.execute-api.../dev')
    parser.add_argument('--table-onboarding', help='OnboardingTable name, for --wipe')
    parser.add_argument('--table-employee', help='EmployeeTable name (employees and interns both), for --wipe')
    parser.add_argument('--username', help='officials username for --seed')
    parser.add_argument('--password', help='password for --username')
    args = parser.parse_args()

    if not args.wipe and not args.seed:
        parser.error('nothing to do - pass --wipe, --seed, or both')

    try:
        if args.wipe:
            explicit = {
                'table_onboarding': args.table_onboarding,
                'table_employee': args.table_employee,
            }
            table_names = {source: resolve_table_name(source, explicit)
                           for source in _TABLE_SOURCES}
            print('Tables: onboarding={} employee={}'.format(
                table_names['onboarding'], table_names['employee']))
            wipe(table_names, args.yes)

        if args.seed:
            base_url = resolve_base_url(args.base_url)
            print('API: {}'.format(base_url))

            username, password = resolve_credentials(args.username, args.password)
            seed(base_url, log_in(base_url, username, password))
    except ApiError as error:
        raise SystemExit('\nFAILED: {}'.format(error))


if __name__ == '__main__':
    main()

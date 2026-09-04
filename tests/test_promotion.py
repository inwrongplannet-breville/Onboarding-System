"""
The promote / restore / reassign endpoints, driven end to end against an
in-memory DynamoDB - two tables, not one: OnboardingTable, and EmployeeTable
holding employees and interns side by side, told apart by entityType.

Each route is its own handler and its own test surface; the sequences (promote,
un-promote, reassign) are exercised by calling the handlers in the order the
frontend would, which is the same thing scripts/seed_employees.py does against
the real API.
"""
import json
from datetime import datetime, timezone

VALID = {
    'employeeId': 'E2001',
    'firstName': 'Nina',
    'lastName': 'Okoye',
    'email': 'nina.okoye@breville.com',
    'phone': '+61 400 000 001',
    'department': 'Engineering',
    'jobTitle': 'Software Engineer',
    'manager': 'Santosh Kumar',
    'startDate': '2026-07-06',
    'employmentType': 'Full-time',
}

INTERN = {
    'employeeId': 'E2002',
    'firstName': 'Theo',
    'lastName': 'Adebayo',
    'email': 'theo.adebayo@breville.com',
    'phone': '+61 400 000 002',
    'department': 'Engineering',
    'jobTitle': 'Engineering Intern',
    'manager': 'Nina Okoye',
    'startDate': '2026-08-01',
    'employmentType': 'Intern',
}

OFFICIAL = {'requestContext': {'authorizer': {'role': 'official', 'username': 'hr.admin'}}}


def as_employee(employee_id):
    return {'requestContext': {'authorizer': {'role': 'employee', 'username': employee_id}}}


def signed_in(event, context=None):
    return dict(event, **(context or OFFICIAL))


def body(response):
    return json.loads(response['body'])


def create(handlers, payload=None, context=None):
    payload = payload or VALID
    return handlers['create_employee'](
        signed_in({'body': json.dumps(payload)}, context), None)


def get_onboarding(handlers, employee_id, context=None):
    return handlers['get_employee'](
        signed_in({'pathParameters': {'id': employee_id}}, context), None)


def tick_everything(handlers, employee_id):
    for item in body(get_onboarding(handlers, employee_id))['checklist']:
        response = handlers['set_checklist_item'](signed_in({
            'pathParameters': {'id': employee_id, 'itemId': item['id']},
            'body': json.dumps({'done': True}),
        }), None)
        assert response['statusCode'] == 200, response['body']


def promote_to_employee(handlers, employee_id, context=None):
    # Collection route - the id travels in the body, not the path.
    return handlers['promote_to_employee'](signed_in({
        'body': json.dumps({'employeeId': employee_id}),
    }, context), None)


def promote_to_intern(handlers, employee_id, manager_id, context=None):
    return handlers['promote_to_intern'](signed_in({
        'body': json.dumps({'employeeId': employee_id, 'reportingManagerId': manager_id}),
    }, context), None)


def add_manager_intern(handlers, manager_id, intern_id, context=None):
    return handlers['add_manager_intern'](signed_in({
        'pathParameters': {'id': manager_id},
        'body': json.dumps({'internId': intern_id}),
    }, context), None)


def remove_manager_intern(handlers, manager_id, intern_id, context=None):
    return handlers['remove_manager_intern'](signed_in({
        'pathParameters': {'id': manager_id, 'internId': intern_id},
    }, context), None)


def set_intern_manager(handlers, intern_id, manager_id, context=None):
    return handlers['set_intern_manager'](signed_in({
        'pathParameters': {'id': intern_id},
        'body': json.dumps({'reportingManagerId': manager_id}),
    }, context), None)


def delete_onboarding_record(handlers, employee_id, context=None):
    return handlers['delete_onboarding_record'](
        signed_in({'pathParameters': {'id': employee_id}}, context), None)


def restore_onboarding(handlers, employee_id, context=None):
    return handlers['restore_onboarding'](signed_in({
        'body': json.dumps({'employeeId': employee_id}),
    }, context), None)


def delete_staff_employee(handlers, employee_id, context=None):
    return handlers['delete_staff_employee'](
        signed_in({'pathParameters': {'id': employee_id}}, context), None)


def delete_staff_intern(handlers, employee_id, context=None):
    return handlers['delete_staff_intern'](
        signed_in({'pathParameters': {'id': employee_id}}, context), None)


def list_staff_employees(handlers, context=None):
    return handlers['list_staff_employees'](signed_in({}, context), None)


def list_interns(handlers, manager_id=None, context=None):
    query = {'managerId': manager_id} if manager_id else None
    return handlers['list_interns'](
        signed_in({'queryStringParameters': query}, context), None)


def hire_and_finish(handlers, payload):
    """Create a hire and tick their whole checklist - the shared setup every
    promotion test needs before it can even attempt to promote."""
    response = create(handlers, payload)
    assert response['statusCode'] == 201, response['body']
    tick_everything(handlers, payload['employeeId'])


# ---------------------------------------------------------- completion gate

def test_promoting_an_incomplete_checklist_is_refused(handlers):
    create(handlers, VALID)
    response = promote_to_employee(handlers, VALID['employeeId'])
    assert response['statusCode'] == 409, response['body']
    # Still there, unmoved.
    assert get_onboarding(handlers, VALID['employeeId'])['statusCode'] == 200


def test_promoting_an_incomplete_intern_is_refused(handlers):
    hire_and_finish(handlers, VALID)  # the manager, so promote_to_intern's manager check has someone
    promote_to_employee(handlers, VALID['employeeId'])
    delete_onboarding_record(handlers, VALID['employeeId'])

    create(handlers, INTERN)
    response = promote_to_intern(handlers, INTERN['employeeId'], VALID['employeeId'])
    assert response['statusCode'] == 409, response['body']


# ---------------------------------------------------------------- promotion

def test_promoting_a_full_time_employee_lands_them_in_the_employee_table(handlers):
    hire_and_finish(handlers, VALID)

    response = promote_to_employee(handlers, VALID['employeeId'])
    assert response['statusCode'] == 201, response['body']
    promoted = body(response)
    assert promoted['onboardedAt']
    assert promoted['joinedOn'] == VALID['startDate']
    assert promoted['checklist']
    assert promoted['interns'] == []

    deleted = delete_onboarding_record(handlers, VALID['employeeId'])
    assert deleted['statusCode'] == 200, deleted['body']
    assert body(deleted)['movedTo'] == 'employee'

    # Gone from the onboarding list (GET /employees/{id} deliberately keeps
    # answering for a promoted id - find_record() spans both tables by
    # design), and present on the staff list.
    onboarding_ids = [e['id'] for e in body(handlers['list_employees'](
        signed_in({}), None))['employees']]
    assert VALID['employeeId'] not in onboarding_ids
    staff_ids = [e['id'] for e in body(list_staff_employees(handlers))['employees']]
    assert VALID['employeeId'] in staff_ids


def test_promoting_an_intern_links_the_manager_both_ways(handlers):
    hire_and_finish(handlers, VALID)
    promote_to_employee(handlers, VALID['employeeId'])
    delete_onboarding_record(handlers, VALID['employeeId'])

    hire_and_finish(handlers, INTERN)
    promoted = body(promote_to_intern(handlers, INTERN['employeeId'], VALID['employeeId']))
    assert promoted['reportingManagerId'] == VALID['employeeId']
    assert 'interns' not in promoted  # never on an intern record

    linked = body(add_manager_intern(handlers, VALID['employeeId'], INTERN['employeeId']))
    assert linked['interns'] == [INTERN['employeeId']]

    delete_onboarding_record(handlers, INTERN['employeeId'])

    intern_ids = [i['id'] for i in body(list_interns(handlers))['interns']]
    assert INTERN['employeeId'] in intern_ids
    by_manager = [i['id'] for i in body(list_interns(handlers, VALID['employeeId']))['interns']]
    assert by_manager == [INTERN['employeeId']]


def test_a_manager_that_does_not_exist_is_rejected(handlers):
    hire_and_finish(handlers, INTERN)
    response = promote_to_intern(handlers, INTERN['employeeId'], 'E9999')
    assert response['statusCode'] == 400, response['body']
    assert 'reportingManagerId' in body(response)['error']['fields']


def test_a_manager_still_in_onboarding_is_rejected(handlers):
    create(handlers, VALID)  # manager not yet promoted
    hire_and_finish(handlers, INTERN)
    response = promote_to_intern(handlers, INTERN['employeeId'], VALID['employeeId'])
    assert response['statusCode'] == 400, response['body']


def test_deleting_the_onboarding_row_before_the_copy_is_refused(handlers):
    hire_and_finish(handlers, VALID)
    response = delete_onboarding_record(handlers, VALID['employeeId'])
    assert response['statusCode'] == 409, response['body']
    assert get_onboarding(handlers, VALID['employeeId'])['statusCode'] == 200


def test_promoting_twice_is_idempotent_not_duplicated(handlers):
    hire_and_finish(handlers, VALID)
    first = promote_to_employee(handlers, VALID['employeeId'])
    assert first['statusCode'] == 201

    second = promote_to_employee(handlers, VALID['employeeId'])
    assert second['statusCode'] == 409

    staff = [e for e in body(list_staff_employees(handlers))['employees']
              if e['id'] == VALID['employeeId']]
    assert len(staff) == 1


def test_linking_the_same_intern_twice_does_not_duplicate(handlers):
    hire_and_finish(handlers, VALID)
    promote_to_employee(handlers, VALID['employeeId'])
    delete_onboarding_record(handlers, VALID['employeeId'])

    hire_and_finish(handlers, INTERN)
    promote_to_intern(handlers, INTERN['employeeId'], VALID['employeeId'])

    add_manager_intern(handlers, VALID['employeeId'], INTERN['employeeId'])
    twice = body(add_manager_intern(handlers, VALID['employeeId'], INTERN['employeeId']))
    assert twice['interns'] == [INTERN['employeeId']]


def test_unlinking_the_last_intern_removes_the_attribute_not_an_empty_list(handlers):
    hire_and_finish(handlers, VALID)
    promote_to_employee(handlers, VALID['employeeId'])
    delete_onboarding_record(handlers, VALID['employeeId'])

    hire_and_finish(handlers, INTERN)
    promote_to_intern(handlers, INTERN['employeeId'], VALID['employeeId'])
    add_manager_intern(handlers, VALID['employeeId'], INTERN['employeeId'])

    unlinked = body(remove_manager_intern(handlers, VALID['employeeId'], INTERN['employeeId']))
    assert unlinked['interns'] == []

    # Unlinking again is a no-op, not an error.
    again = remove_manager_intern(handlers, VALID['employeeId'], INTERN['employeeId'])
    assert again['statusCode'] == 200


# ------------------------------------------------------------------ restore

def test_undo_restores_the_checklist_and_its_comments(handlers):
    hire_and_finish(handlers, VALID)
    # Leave a comment before promoting - it must survive the round trip.
    comment_response = handlers['set_checklist_item'](signed_in({
        'pathParameters': {'id': VALID['employeeId'], 'itemId': 'offer-letter'},
        'body': json.dumps({'comment': 'chased payroll twice'}),
    }), None)
    assert comment_response['statusCode'] == 200, comment_response['body']

    promote_to_employee(handlers, VALID['employeeId'])
    delete_onboarding_record(handlers, VALID['employeeId'])

    restored = restore_onboarding(handlers, VALID['employeeId'])
    assert restored['statusCode'] == 201, restored['body']
    restored_employee = body(restored)
    item = [i for i in restored_employee['checklist'] if i['id'] == 'offer-letter'][0]
    assert item['comment'] == 'chased payroll twice'

    delete_staff = delete_staff_employee(handlers, VALID['employeeId'])
    assert delete_staff['statusCode'] == 200, delete_staff['body']


def test_undo_outside_the_window_is_refused(handlers, monkeypatch):
    hire_and_finish(handlers, VALID)
    promote_to_employee(handlers, VALID['employeeId'])
    delete_onboarding_record(handlers, VALID['employeeId'])

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2099, 1, 1, tzinfo=timezone.utc)

    import handlers.restore_onboarding as restore_module
    monkeypatch.setattr(restore_module, 'datetime', _Frozen)

    response = restore_onboarding(handlers, VALID['employeeId'])
    assert response['statusCode'] == 409, response['body']


def test_restoring_a_record_not_promoted_is_not_found(handlers):
    response = restore_onboarding(handlers, 'E9999')
    assert response['statusCode'] == 404, response['body']


def test_deleting_the_staff_row_before_restore_is_refused(handlers):
    hire_and_finish(handlers, VALID)
    promote_to_employee(handlers, VALID['employeeId'])
    delete_onboarding_record(handlers, VALID['employeeId'])

    response = delete_staff_employee(handlers, VALID['employeeId'])
    assert response['statusCode'] == 409, response['body']


# ---------------------------------------------------------------- reassign

def test_reassigning_moves_the_intern_between_managers(handlers):
    hire_and_finish(handlers, VALID)
    promote_to_employee(handlers, VALID['employeeId'])
    delete_onboarding_record(handlers, VALID['employeeId'])

    other_manager = dict(VALID, employeeId='E2003', email='other@breville.com')
    hire_and_finish(handlers, other_manager)
    promote_to_employee(handlers, other_manager['employeeId'])
    delete_onboarding_record(handlers, other_manager['employeeId'])

    hire_and_finish(handlers, INTERN)
    promote_to_intern(handlers, INTERN['employeeId'], VALID['employeeId'])
    add_manager_intern(handlers, VALID['employeeId'], INTERN['employeeId'])
    delete_onboarding_record(handlers, INTERN['employeeId'])

    reassigned = body(set_intern_manager(
        handlers, INTERN['employeeId'], other_manager['employeeId']))
    assert reassigned['previousReportingManagerId'] == VALID['employeeId']
    assert reassigned['reportingManagerId'] == other_manager['employeeId']

    add_manager_intern(handlers, other_manager['employeeId'], INTERN['employeeId'])
    remove_manager_intern(handlers, VALID['employeeId'], INTERN['employeeId'])

    old_manager = body(list_staff_employees(handlers))['employees']
    old = [e for e in old_manager if e['id'] == VALID['employeeId']][0]
    new = [e for e in old_manager if e['id'] == other_manager['employeeId']][0]
    assert old['interns'] == []
    assert new['interns'] == [INTERN['employeeId']]

    by_new_manager = [i['id'] for i in body(
        list_interns(handlers, other_manager['employeeId']))['interns']]
    assert by_new_manager == [INTERN['employeeId']]


def test_reassigning_to_the_same_manager_is_a_no_op(handlers):
    hire_and_finish(handlers, VALID)
    promote_to_employee(handlers, VALID['employeeId'])
    delete_onboarding_record(handlers, VALID['employeeId'])

    hire_and_finish(handlers, INTERN)
    promote_to_intern(handlers, INTERN['employeeId'], VALID['employeeId'])

    response = set_intern_manager(handlers, INTERN['employeeId'], VALID['employeeId'])
    assert response['statusCode'] == 200
    assert body(response)['previousReportingManagerId'] == VALID['employeeId']


# ------------------------------------------------------- cross-table reads

def test_a_promoted_employee_can_still_read_and_edit_their_own_record(handlers):
    hire_and_finish(handlers, VALID)
    promote_to_employee(handlers, VALID['employeeId'])
    delete_onboarding_record(handlers, VALID['employeeId'])

    own = handlers['get_employee'](signed_in(
        {'pathParameters': {'id': VALID['employeeId']}},
        as_employee(VALID['employeeId'])), None)
    assert own['statusCode'] == 200, own['body']

    patched = handlers['update_own_contact'](signed_in({
        'pathParameters': {'id': VALID['employeeId']},
        'body': json.dumps({'phone': '+61 499 999 999'}),
    }, as_employee(VALID['employeeId'])), None)
    assert patched['statusCode'] == 200, patched['body']
    assert body(patched)['phone'] == '+61 499 999 999'


def test_a_promoted_employee_cannot_be_edited_through_the_onboarding_routes(handlers):
    hire_and_finish(handlers, VALID)
    promote_to_employee(handlers, VALID['employeeId'])
    delete_onboarding_record(handlers, VALID['employeeId'])

    put_response = handlers['update_employee'](signed_in({
        'pathParameters': {'id': VALID['employeeId']},
        'body': json.dumps(VALID),
    }), None)
    assert put_response['statusCode'] == 404

    patch_response = handlers['set_checklist_item'](signed_in({
        'pathParameters': {'id': VALID['employeeId'], 'itemId': 'offer-letter'},
        'body': json.dumps({'done': False}),
    }), None)
    assert patch_response['statusCode'] == 404


# --------------------------------------------------------------------- role

def test_employee_role_is_refused_on_every_new_route(handlers):
    hire_and_finish(handlers, VALID)
    employee_ctx = as_employee(VALID['employeeId'])

    assert promote_to_employee(handlers, VALID['employeeId'], employee_ctx)['statusCode'] == 403
    assert list_staff_employees(handlers, employee_ctx)['statusCode'] == 403
    assert list_interns(handlers, context=employee_ctx)['statusCode'] == 403
    assert delete_onboarding_record(handlers, VALID['employeeId'], employee_ctx)['statusCode'] == 403
    assert restore_onboarding(handlers, VALID['employeeId'], employee_ctx)['statusCode'] == 403
    assert delete_staff_employee(handlers, VALID['employeeId'], employee_ctx)['statusCode'] == 403
    assert delete_staff_intern(handlers, VALID['employeeId'], employee_ctx)['statusCode'] == 403
    assert add_manager_intern(handlers, VALID['employeeId'], 'E9999', employee_ctx)['statusCode'] == 403
    assert remove_manager_intern(handlers, VALID['employeeId'], 'E9999', employee_ctx)['statusCode'] == 403
    assert set_intern_manager(handlers, 'E9999', VALID['employeeId'], employee_ctx)['statusCode'] == 403
    assert promote_to_intern(handlers, VALID['employeeId'], 'E9999', employee_ctx)['statusCode'] == 403


# ------------------------------------------- one table, two kinds of record

def test_an_intern_named_as_a_reporting_manager_is_rejected(handlers):
    """
    The regression this merge introduces if left unguarded: EmployeeTable
    holds both kinds of record now, so "this id resolves to a row in
    EmployeeTable" is no longer proof the row is an employee. Both places
    that validate a reporting manager must check entityType, not just
    existence.
    """
    hire_and_finish(handlers, VALID)
    promote_to_employee(handlers, VALID['employeeId'])
    delete_onboarding_record(handlers, VALID['employeeId'])

    hire_and_finish(handlers, INTERN)
    promote_to_intern(handlers, INTERN['employeeId'], VALID['employeeId'])
    delete_onboarding_record(handlers, INTERN['employeeId'])

    # A second intern, whose promote attempt names the FIRST intern as their
    # manager - must be refused by promote_to_intern.py's manager check.
    second_intern = dict(INTERN, employeeId='E2004', email='second@breville.com')
    hire_and_finish(handlers, second_intern)
    response = promote_to_intern(handlers, second_intern['employeeId'], INTERN['employeeId'])
    assert response['statusCode'] == 400, response['body']
    assert 'reportingManagerId' in body(response)['error']['fields']

    # And set_intern_manager.py's reassignment check must refuse the same
    # thing for an already-promoted intern.
    reassign = set_intern_manager(handlers, INTERN['employeeId'], second_intern['employeeId'])
    assert reassign['statusCode'] in (400, 404)


def test_delete_staff_intern_refuses_an_employee_record(handlers):
    hire_and_finish(handlers, VALID)
    promote_to_employee(handlers, VALID['employeeId'])
    delete_onboarding_record(handlers, VALID['employeeId'])

    response = delete_staff_intern(handlers, VALID['employeeId'])
    assert response['statusCode'] == 404, response['body']

    # The employee record must still be there and still be an employee.
    staff = [e for e in body(list_staff_employees(handlers))['employees']
             if e['id'] == VALID['employeeId']]
    assert len(staff) == 1


def test_delete_staff_employee_refuses_an_intern_record(handlers):
    hire_and_finish(handlers, VALID)
    promote_to_employee(handlers, VALID['employeeId'])
    delete_onboarding_record(handlers, VALID['employeeId'])

    hire_and_finish(handlers, INTERN)
    promote_to_intern(handlers, INTERN['employeeId'], VALID['employeeId'])
    delete_onboarding_record(handlers, INTERN['employeeId'])

    response = delete_staff_employee(handlers, INTERN['employeeId'])
    assert response['statusCode'] == 404, response['body']

    interns = [i for i in body(list_interns(handlers))['interns']
               if i['id'] == INTERN['employeeId']]
    assert len(interns) == 1


def test_linking_an_employee_as_someones_intern_is_rejected(handlers):
    hire_and_finish(handlers, VALID)
    promote_to_employee(handlers, VALID['employeeId'])
    delete_onboarding_record(handlers, VALID['employeeId'])

    other_employee = dict(VALID, employeeId='E2005', email='other2@breville.com')
    hire_and_finish(handlers, other_employee)
    promote_to_employee(handlers, other_employee['employeeId'])
    delete_onboarding_record(handlers, other_employee['employeeId'])

    # Try to link one employee as if they were another's intern.
    response = add_manager_intern(handlers, VALID['employeeId'], other_employee['employeeId'])
    assert response['statusCode'] == 400, response['body']

    manager = [e for e in body(list_staff_employees(handlers))['employees']
               if e['id'] == VALID['employeeId']][0]
    assert manager['interns'] == []


def test_cannot_be_promoted_as_both_employee_and_intern(handlers):
    """
    One shared table with attribute_not_exists(employeeKey) makes this
    structurally impossible now - two independent tables never guaranteed it.

    Exercised the way it could actually happen: an employee number is
    promoted, its onboarding row removed (freeing the number in
    OnboardingTable, which is the only place create_employee's own
    uniqueness check looks), then HR re-creates a hire under that same
    number - this time as an intern - and tries to promote them too. The
    PutItem's NOT_EXISTS on EmployeeTable is the only thing left to catch it.
    """
    other_employee = dict(VALID, employeeId='E2006', email='other3@breville.com')
    hire_and_finish(handlers, other_employee)
    promote_to_employee(handlers, other_employee['employeeId'])
    delete_onboarding_record(handlers, other_employee['employeeId'])

    hire_and_finish(handlers, VALID)
    promote_to_employee(handlers, VALID['employeeId'])
    delete_onboarding_record(handlers, VALID['employeeId'])

    # The number is free in OnboardingTable again, so this succeeds - but
    # EmployeeTable already has VALID['employeeId'] as an employee.
    reused = dict(INTERN, employeeId=VALID['employeeId'], email='reused@breville.com')
    hire_and_finish(handlers, reused)
    response = promote_to_intern(handlers, reused['employeeId'], other_employee['employeeId'])
    assert response['statusCode'] == 409, response['body']


def test_one_employee_and_one_intern_coexist_and_each_dashboard_shows_only_its_own(handlers):
    hire_and_finish(handlers, VALID)
    promote_to_employee(handlers, VALID['employeeId'])
    delete_onboarding_record(handlers, VALID['employeeId'])

    hire_and_finish(handlers, INTERN)
    promote_to_intern(handlers, INTERN['employeeId'], VALID['employeeId'])
    delete_onboarding_record(handlers, INTERN['employeeId'])

    employees = body(list_staff_employees(handlers))['employees']
    interns = body(list_interns(handlers))['interns']

    assert [e['id'] for e in employees] == [VALID['employeeId']]
    assert [i['id'] for i in interns] == [INTERN['employeeId']]
    # Every employee object carries `interns`; no intern object does.
    assert 'interns' in employees[0]
    assert 'interns' not in interns[0]
    assert 'reportingManagerId' in interns[0]
    assert 'reportingManagerId' not in employees[0]

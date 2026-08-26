"""
What the two roles can actually do, at the handler seam.

Separate from test_handlers.py, which asks whether the six routes are correct.
These ask a different question about the same routes - who is allowed to call
them - so they read better together than scattered through six hundred lines of
CRUD.

The reads used to be here too, as a set of tests about how much of a *colleague's*
record an employee was trimmed down to. There is no such thing any more: an
employee reads their own record and gets a 403 for anybody else's, so that half
moved to test_own_profile.py, where it sits beside the writes that share its trim.
What is left here is the boundary itself.

The helpers are imported rather than rewritten: `create`, `post` and the rest
already know how to build a valid request, and the only thing changing here is
the authorizer context they carry.

One thing these tests deliberately do not cover: whether the *token* was
genuine. That happens in the authorizer, before any of this runs, and is covered
in test_auth.py. Here the context is taken as given - which is exactly what a
handler does.
"""
from test_handlers import (
    EMPLOYEE,
    OFFICIAL,
    VALID,
    as_employee,
    body,
    create,
    delete,
    get,
    listing,
    patch,
    patch_contact,
    post,
    put,
)


# ------------------------------------------------------ writes are officials'

def test_an_employee_cannot_create(handlers):
    response = post(handlers, dict(VALID, employeeId='E7001'), EMPLOYEE)
    assert response['statusCode'] == 403

    # The record must not exist. A 403 that still wrote would be the worst of
    # both: refused to the caller, present in the table.
    assert get(handlers, 'E7001')['statusCode'] == 404


def test_an_employee_cannot_edit(handlers):
    employee = create(handlers)

    response = put(handlers, employee['id'], dict(VALID, jobTitle='Director'), EMPLOYEE)
    assert response['statusCode'] == 403

    assert body(get(handlers, employee['id']))['jobTitle'] == VALID['jobTitle']


def test_an_employee_cannot_archive(handlers):
    employee = create(handlers)

    assert delete(handlers, employee['id'], EMPLOYEE)['statusCode'] == 403

    assert body(get(handlers, employee['id']))['archived'] is False
    assert employee['id'] in [e['id'] for e in body(listing(handlers))['employees']]


def test_an_employee_cannot_tick_a_checklist_item(handlers):
    employee = create(handlers)

    response = patch(handlers, employee['id'], 'offer-letter', True, EMPLOYEE)
    assert response['statusCode'] == 403

    after = body(get(handlers, employee['id']))
    assert all(item['done'] is False for item in after['checklist'])
    assert after['status'] == 'Pending'


def test_a_refused_write_is_403_and_not_401(handlers):
    """
    The distinction the frontend acts on. 401 means the session is over and
    js/store.js signs the user out; 403 means the session is fine and this
    action is not theirs. Returning 401 here would log an employee out for
    clicking something they were never shown.
    """
    response = post(handlers, dict(VALID, employeeId='E7002'), EMPLOYEE)
    assert response['statusCode'] == 403
    assert body(response)['error']['code'] == 'Forbidden'


def test_a_request_with_no_authorizer_context_is_refused(handlers):
    """
    Fails closed. An event with no role on it - a direct invoke, a route wired up
    without an authorizer, a future refactor that drops the Auth block - must be
    treated as the least privileged caller, not the most.
    """
    assert handlers['create_employee']({'body': '{}'}, None)['statusCode'] == 403


def test_a_role_the_build_does_not_know_is_refused(handlers):
    """
    The authorizer already rejects unknown roles, so this should be unreachable.
    It is asserted anyway: caller_role is the last thing standing if that check
    is ever relaxed, and "unknown role" must not resolve to officials.
    """
    unknown = {'requestContext': {'authorizer': {'role': 'superuser'}}}
    assert post(handlers, dict(VALID, employeeId='E7003'), unknown)['statusCode'] == 403


def test_officials_are_unaffected_by_any_of_this(handlers):
    """The other half of every test above - the gate must not block the role it
    exists to admit."""
    employee = create(handlers)

    assert put(handlers, employee['id'], dict(VALID, jobTitle='Director'))['statusCode'] == 200
    assert patch(handlers, employee['id'], 'offer-letter', True)['statusCode'] == 200
    assert delete(handlers, employee['id'])['statusCode'] == 200


# ------------------------------------------------- reads are scoped by role

def test_the_directory_is_officials_only(handlers):
    """
    There is no trimmed list any more. An employee has exactly one record they may
    read and they reach it by id, so the endpoint has one audience - and refusing
    it outright is a smaller thing to get wrong than a whitelist applied per row.
    """
    create(handlers)

    response = listing(handlers, EMPLOYEE)
    assert response['statusCode'] == 403
    assert body(response)['error']['code'] == 'Forbidden'


def test_a_refused_directory_read_leaks_no_names(handlers):
    create(handlers, firstName='Meera', lastName='Nair')
    response = listing(handlers, EMPLOYEE)

    assert response['statusCode'] == 403
    assert 'Meera' not in response['body']
    assert 'Nair' not in response['body']


def test_an_employee_cannot_read_a_colleague(handlers):
    """
    The other half of the directory being closed: closing the list is worth
    nothing if the record is still readable one id at a time.
    """
    employee_id = create(handlers)['id']
    assert get(handlers, employee_id, as_employee('E7009'))['statusCode'] == 403


def test_an_employee_reads_and_writes_only_their_own_record(handlers):
    """
    The boundary in one test. What the own record actually contains, and what a
    contact patch may change, are test_own_profile.py's subject.
    """
    create(handlers, employeeId='E7010')

    assert get(handlers, 'E7010', as_employee('E7010'))['statusCode'] == 200
    assert patch_contact(handlers, 'E7010', {'phone': '+61 400 000 000'},
                         as_employee('E7010'))['statusCode'] == 200


def test_officials_still_get_the_whole_record(handlers):
    """The regression guard for the trim: it must apply to one role only."""
    employee_id = create(handlers)['id']
    full = body(get(handlers, employee_id, OFFICIAL))

    for field in ('email', 'phone', 'manager', 'employmentType', 'personalEmail',
                  'address', 'checklist', 'archived', 'archivedAs', 'archivedAt'):
        assert field in full
    assert len(full['checklist']) == 8
    assert all('comment' in item for item in full['checklist'])

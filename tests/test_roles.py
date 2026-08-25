"""
What the two roles can actually do, at the handler seam.

Separate from test_handlers.py, which asks whether the six routes are correct.
These ask a different question about the same routes - who is allowed to call
them, and how much of the answer they get back - so they read better together
than scattered through six hundred lines of CRUD.

The helpers are imported rather than rewritten: `create`, `post` and the rest
already know how to build a valid request, and the only thing changing here is
the authorizer context they carry.

One thing these tests deliberately do not cover: whether the *token* was
genuine. That happens in the authorizer, before any of this runs, and is covered
in test_auth.py. Here the context is taken as given - which is exactly what a
handler does.
"""
import pytest

from common.models import EMPLOYEE_VISIBLE_FIELDS
from test_handlers import (
    EMPLOYEE,
    OFFICIAL,
    VALID,
    archive,
    body,
    create,
    delete,
    get,
    listing,
    patch,
    post,
    put,
)

# Every field an officials response carries that an employee's must not. Spelled
# out rather than derived from EMPLOYEE_VISIBLE_FIELDS, so that adding a field to
# the model and forgetting it here is a failing test rather than a silent pass:
# a derived list would grow to exclude whatever was added.
HIDDEN_FROM_EMPLOYEES = (
    'email', 'phone', 'manager', 'employmentType',
    'checklist', 'archived', 'archivedAs', 'archivedAt',
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


# ------------------------------------------------- reads are trimmed by role

def test_the_list_an_employee_sees_carries_only_the_directory_fields(handlers):
    create(handlers)
    listed = body(listing(handlers, EMPLOYEE))['employees']

    assert listed
    for employee in listed:
        assert sorted(employee) == sorted(EMPLOYEE_VISIBLE_FIELDS)


@pytest.mark.parametrize('field', HIDDEN_FROM_EMPLOYEES)
def test_a_listed_employee_hides_each_restricted_field(handlers, field):
    """
    One test per field, so a failure names the field that leaked rather than
    reporting that a dict comparison did not match.
    """
    create(handlers)
    for employee in body(listing(handlers, EMPLOYEE))['employees']:
        assert field not in employee


@pytest.mark.parametrize('field', HIDDEN_FROM_EMPLOYEES)
def test_reading_one_employee_hides_each_restricted_field(handlers, field):
    employee_id = create(handlers)['id']
    assert field not in body(get(handlers, employee_id, EMPLOYEE))


def test_an_employee_still_sees_onboarding_progress(handlers):
    """
    The restriction is not "show almost nothing". Progress is the thing this app
    exists to report, and a directory that cannot say who has started is not
    worth signing in to.
    """
    employee_id = create(handlers)['id']
    patch(handlers, employee_id, 'offer-letter', True)

    seen = body(get(handlers, employee_id, EMPLOYEE))
    assert seen['status'] == 'In Progress'
    assert seen['progress'] == {'done': 1, 'total': 8, 'percent': 13}


def test_both_roles_are_shown_the_same_employees_in_the_same_order(handlers):
    """
    Restricting fields must not restrict rows or reorder them. The trim happens
    after the archive filter and the sort for exactly this reason.
    """
    for start in ('2026-03-01', '2026-01-05', '2026-02-11'):
        create(handlers, startDate=start)

    official_ids = [e['id'] for e in body(listing(handlers))['employees']]
    employee_ids = [e['id'] for e in body(listing(handlers, EMPLOYEE))['employees']]

    assert employee_ids == official_ids


def test_an_archived_employee_is_invisible_to_the_employee_role(handlers):
    """
    404, not a trimmed record. They are already off the list, and the only way
    an employee could reach one is by guessing the URL - at which point the
    honest answer is the same one the list gave.
    """
    employee_id = create(handlers)['id']
    archive(handlers, employee_id)

    assert get(handlers, employee_id, EMPLOYEE)['statusCode'] == 404
    # Still readable by an official, exactly as before.
    assert get(handlers, employee_id)['statusCode'] == 200


def test_the_count_an_employee_sees_matches_the_rows(handlers):
    create(handlers)
    create(handlers)
    result = body(listing(handlers, EMPLOYEE))

    assert result['count'] == len(result['employees']) == 2


def test_officials_still_get_the_whole_record(handlers):
    """The regression guard for the trim: it must apply to one role only."""
    employee_id = create(handlers)['id']
    full = body(get(handlers, employee_id, OFFICIAL))

    for field in HIDDEN_FROM_EMPLOYEES:
        assert field in full
    assert len(full['checklist']) == 8

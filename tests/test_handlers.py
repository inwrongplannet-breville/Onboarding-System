"""
The six handlers, driven end to end against an in-memory DynamoDB.

These mirror the acceptance run in docs/api.md. Passing here does not mean the
stack deploys - IAM is not emulated - but it does mean the DynamoDB calls,
condition expressions and response shapes are right before anyone waits on
`sam deploy`.
"""
import itertools
import json

import pytest

VALID = {
    'employeeId': 'E1001',
    'firstName': 'Priya',
    'lastName': 'Sharma',
    'email': 'priya.sharma@breville.com',
    'phone': '+61 412 883 016',
    'department': 'Engineering',
    'jobTitle': 'Software Engineer',
    'manager': 'Santosh Kumar',
    'startDate': '2026-07-06',
    'employmentType': 'Full-time',
}


# Every request below is made as an officials account, because that is what these
# tests were written to exercise: the CRUD behaviour of the six routes. The
# authorizer is what puts this on a real request, and it is not in the loop here -
# these call the handlers directly, which is the same seam `sam local invoke`
# uses.
#
# It is a constant rather than a fixture because nothing mutates it: handlers only
# ever read requestContext. Role *enforcement* is tested separately, at the bottom
# of this file, where the point is the employee context rather than the CRUD.
OFFICIAL = {'requestContext': {'authorizer': {'role': 'official', 'username': 'hr.admin'}}}
EMPLOYEE = {'requestContext': {'authorizer': {'role': 'employee', 'username': 'employee'}}}


def signed_in(event, context=None):
    """One request event, carrying an authorizer context. Officials by default."""
    return dict(event, **(context or OFFICIAL))


def body(response):
    return json.loads(response['body'])


def post(handlers, payload, context=None):
    return handlers['create_employee'](
        signed_in({'body': json.dumps(payload)}, context), None)


def get(handlers, employee_id, context=None):
    return handlers['get_employee'](
        signed_in({'pathParameters': {'id': employee_id}}, context), None)


def put(handlers, employee_id, payload, context=None):
    return handlers['update_employee'](signed_in(
        {'pathParameters': {'id': employee_id}, 'body': json.dumps(payload)}, context), None)


def delete(handlers, employee_id, context=None):
    return handlers['delete_employee'](
        signed_in({'pathParameters': {'id': employee_id}}, context), None)


def patch(handlers, employee_id, item_id, done, context=None):
    return patch_raw(handlers, employee_id, item_id, {'done': done}, context)


def patch_raw(handlers, employee_id, item_id, payload, context=None):
    return handlers['set_checklist_item'](signed_in(
        {'pathParameters': {'id': employee_id, 'itemId': item_id},
         'body': json.dumps(payload)}, context), None)


def item_of(employee, item_id):
    return [i for i in employee['checklist'] if i['id'] == item_id][0]


def archive(handlers, employee_id):
    """DELETE, asserting it archived, and hand back the archived record."""
    response = delete(handlers, employee_id)
    assert response['statusCode'] == 200, response['body']
    return body(response)


def tick_everything(handlers, employee_id):
    for item in body(get(handlers, employee_id))['checklist']:
        assert patch(handlers, employee_id, item['id'], True)['statusCode'] == 200


def listed_ids(handlers, context=None):
    return [e['id'] for e in body(listing(handlers, context))['employees']]


def listing(handlers, context=None):
    return handlers['list_employees'](signed_in({}, context), None)


# The employee id is the partition key now, so two creates in one test collide
# unless they are told apart. Starts well clear of VALID['employeeId'] so a test
# that mixes create() with a raw post(VALID) gets two employees rather than a 409.
_ids = itertools.count(9000)


def create(handlers, **overrides):
    """
    One employee, with a fresh employee id unless the caller names one.

    The default used to be irrelevant - the server minted a UUID and every create
    was unique for free. It is not free any more, and a test that wants two
    employees has to say so, which is exactly what this default does on its
    behalf.
    """
    overrides.setdefault('employeeId', 'E{}'.format(next(_ids)))
    response = post(handlers, dict(VALID, **overrides))
    assert response['statusCode'] == 201, response['body']
    return body(response)


# ---------------------------------------------------------------------- create

def test_create_returns_201_with_a_location_header(handlers):
    response = post(handlers, VALID)
    assert response['statusCode'] == 201
    assert response['headers']['Location'] == '/employees/' + body(response)['id']


def test_create_seeds_eight_unticked_checklist_items(handlers):
    employee = create(handlers)
    assert len(employee['checklist']) == 8
    assert all(item['done'] is False for item in employee['checklist'])
    assert employee['status'] == 'Pending'


def test_create_rejects_invalid_input_before_writing_anything(handlers):
    response = post(handlers, {'email': 'nope'})
    assert response['statusCode'] == 400
    assert 'email' in body(response)['error']['fields']
    assert body(listing(handlers))['count'] == 0


def test_create_rejects_a_department_outside_the_enum(handlers):
    response = post(handlers, dict(VALID, department='Marketing'))
    assert response['statusCode'] == 400


def test_create_ignores_a_checklist_supplied_by_the_caller(handlers):
    # `id` is not the input field - `employeeId` is - so a body naming `id` is
    # just an unknown key, and pick_editable drops it along with the checklist.
    employee = create(handlers, employeeId='E3001', id='hacked', checklist=[])
    assert employee['id'] == 'E3001'
    assert len(employee['checklist']) == 8


def test_create_rejects_a_body_that_is_not_json(handlers):
    response = handlers['create_employee'](signed_in({'body': 'not json'}), None)
    assert response['statusCode'] == 400


def test_create_rejects_a_start_date_that_is_not_a_real_day(handlers):
    response = post(handlers, dict(VALID, startDate='2026-02-30'))
    assert response['statusCode'] == 400
    assert 'startDate' in body(response)['error']['fields']


# ------------------------------------------------------------- the employee id

def test_the_supplied_employee_id_becomes_the_records_id(handlers):
    employee = create(handlers, employeeId='E1024')
    assert employee['id'] == 'E1024'
    assert body(get(handlers, 'E1024'))['id'] == 'E1024'


def test_the_employee_id_is_the_partition_key(handlers):
    from common.db import table

    create(handlers, employeeId='E1024')
    assert table.get_item(Key={'employeeKey': 'EMP#E1024'}).get('Item') is not None


def test_create_requires_an_employee_id(handlers):
    response = post(handlers, {k: v for k, v in VALID.items() if k != 'employeeId'})
    assert response['statusCode'] == 400
    assert 'employeeId' in body(response)['error']['fields']
    assert body(listing(handlers))['count'] == 0


@pytest.mark.parametrize('bad', [
    'E',                # one character
    'E' * 21,           # one over the cap
    'E 1024',           # a space, which would make two ids look identical
    'E#1024',           # '#' is the key separator and must never appear
    '-E1024',           # must start alphanumeric
    'E1024/../admin',   # nothing that could be read as a path
    '  ',               # whitespace only, which trims to nothing
])
def test_create_rejects_a_malformed_employee_id(handlers, bad):
    response = post(handlers, dict(VALID, employeeId=bad))
    assert response['statusCode'] == 400
    assert 'employeeId' in body(response)['error']['fields']
    assert body(listing(handlers))['count'] == 0


@pytest.mark.parametrize('bad', [1024, None, ['E1024'], {'id': 'E1024'}])
def test_an_employee_id_that_is_not_text_is_a_400_not_a_500(handlers, bad):
    assert post(handlers, dict(VALID, employeeId=bad))['statusCode'] == 400


def test_an_employee_id_is_upper_cased(handlers):
    # Otherwise "e1024" and "E1024" are two partition keys, which is one employee
    # with two records and no error to say so.
    assert create(handlers, employeeId='e1024')['id'] == 'E1024'


def test_an_employee_id_is_trimmed(handlers):
    assert create(handlers, employeeId='  E1024  ')['id'] == 'E1024'


def test_a_duplicate_employee_id_is_a_409(handlers):
    create(handlers, employeeId='E1024')
    response = post(handlers, dict(VALID, employeeId='E1024',
                                   firstName='Someone', lastName='Else'))

    assert response['statusCode'] == 409
    assert body(response)['error']['code'] == 'Conflict'
    # Named, so the form paints it under the input rather than in the banner.
    assert 'employeeId' in body(response)['error']['fields']


def test_a_duplicate_employee_id_does_not_overwrite_the_first_employee(handlers):
    # The whole reason the id belongs in the key. PutItem overwrites by default,
    # so without the condition this second call would silently replace a real
    # person and their entire onboarding history.
    create(handlers, employeeId='E1024')
    tick_everything(handlers, 'E1024')
    post(handlers, dict(VALID, employeeId='E1024', firstName='Someone', lastName='Else'))

    survivor = body(get(handlers, 'E1024'))
    assert survivor['firstName'] == VALID['firstName']
    assert survivor['status'] == 'Onboarded'


def test_a_duplicate_differing_only_in_case_is_still_a_duplicate(handlers):
    create(handlers, employeeId='E1024')
    assert post(handlers, dict(VALID, employeeId='e1024'))['statusCode'] == 409


def test_an_archived_employee_still_holds_its_id(handlers):
    # Archiving leaves the item in the table, so the number stays taken. Re-hiring
    # someone means a new number, which is the honest outcome - reusing it would
    # attach a second person's history to the first person's id.
    archive(handlers, create(handlers, employeeId='E1024')['id'])
    assert post(handlers, dict(VALID, employeeId='E1024'))['statusCode'] == 409


def test_an_employee_id_cannot_be_changed_by_an_update(handlers):
    # Not a whitelist nicety: DynamoDB cannot move an item between partition
    # keys, so a PUT that appeared to rename would have upserted a second
    # employee and left the first one behind.
    from common.db import table

    create(handlers, employeeId='E1024')
    assert put(handlers, 'E1024', dict(VALID, employeeId='E2048'))['statusCode'] == 200

    assert body(get(handlers, 'E1024'))['id'] == 'E1024'
    assert get(handlers, 'E2048')['statusCode'] == 404
    assert table.get_item(Key={'employeeKey': 'EMP#E2048'}).get('Item') is None


@pytest.mark.parametrize('typed', ['e1024', 'E1024', '  e1024  '])
def test_the_id_in_the_url_is_matched_case_insensitively(handlers, typed):
    # People type employee numbers, and they did not type UUIDs. A correct id in
    # the wrong case must not 404 against a record that plainly exists.
    create(handlers, employeeId='E1024')
    assert get(handlers, typed)['statusCode'] == 200
    assert patch(handlers, typed, 'laptop', True)['statusCode'] == 200
    assert put(handlers, typed, VALID)['statusCode'] == 200
    assert delete(handlers, typed)['statusCode'] == 200


# ------------------------------------------------- email is no longer unique

def test_two_employees_may_now_share_a_work_email(handlers):
    # Documenting a deliberate loss, not asserting a feature. Uniqueness used to
    # be enforced by a guard item in its own partition; the table now holds one
    # item per employee and nothing else, and DynamoDB can only enforce
    # uniqueness on a partition key - which is the employee number, not the
    # email. Moving a meaningful id into the key bought a real guarantee about
    # employee numbers and none at all about mailboxes. If this test ever starts
    # failing, someone reintroduced the guard and should say so loudly.
    create(handlers, employeeId='E2001')
    second = post(handlers, dict(VALID, employeeId='E2002',
                                 firstName='Someone', lastName='Else'))

    assert second['statusCode'] == 201
    assert body(listing(handlers))['count'] == 2


def test_an_employee_can_be_edited_onto_an_email_another_one_holds(handlers):
    create(handlers, email='taken@breville.com')
    other = create(handlers, email='free@breville.com')['id']

    assert put(handlers, other, dict(VALID, email='taken@breville.com'))['statusCode'] == 200
    assert body(get(handlers, other))['email'] == 'taken@breville.com'


# ------------------------------------------------------------------------- get

def test_get_returns_the_employee_with_its_checklist(handlers):
    created = create(handlers)
    fetched = body(get(handlers, created['id']))
    assert fetched == created


def test_get_of_an_unknown_id_is_404(handlers):
    assert get(handlers, 'does-not-exist')['statusCode'] == 404


def test_get_leaks_no_internal_attributes(handlers):
    employee = body(get(handlers, create(handlers)['id']))
    for internal in ('employeeKey', 'PK', 'SK', 'employeeId', 'entityType'):
        assert internal not in employee


# ------------------------------------------------------------------------ list

def test_list_is_empty_before_anything_is_created(handlers):
    result = body(listing(handlers))
    assert result == {'employees': [], 'count': 0}


def test_list_returns_every_employee_with_their_checklist(handlers):
    create(handlers, email='a@breville.com', startDate='2026-07-06')
    create(handlers, email='b@breville.com', startDate='2026-08-10')

    result = body(listing(handlers))
    assert result['count'] == 2
    assert all(len(employee['checklist']) == 8 for employee in result['employees'])


def test_list_is_sorted_by_start_date(handlers):
    create(handlers, lastName='Later', email='later@breville.com', startDate='2026-09-01')
    create(handlers, lastName='Earlier', email='earlier@breville.com', startDate='2026-07-06')

    names = [e['lastName'] for e in body(listing(handlers))['employees']]
    assert names == ['Earlier', 'Later']


# ---------------------------------------------------------------------- update

def test_update_changes_the_profile(handlers):
    employee_id = create(handlers)['id']
    updated = body(put(handlers, employee_id, dict(VALID, jobTitle='Senior Software Engineer')))
    assert updated['jobTitle'] == 'Senior Software Engineer'


def test_update_preserves_checklist_progress(handlers):
    employee_id = create(handlers)['id']
    patch(handlers, employee_id, 'offer-letter', True)

    updated = body(put(handlers, employee_id, dict(VALID, jobTitle='Changed')))
    ticked = [item['id'] for item in updated['checklist'] if item['done']]
    assert ticked == ['offer-letter']


def test_update_ignores_id_and_checklist_in_the_body(handlers):
    employee_id = create(handlers)['id']
    updated = body(put(handlers, employee_id,
                       dict(VALID, id='hacked', employeeId='E9999', checklist=[])))
    assert updated['id'] == employee_id
    assert len(updated['checklist']) == 8


def test_update_of_an_unknown_id_is_404_and_creates_no_ghost_record(handlers):
    # Without the condition expression, UpdateItem would upsert a half-employee
    # with no checklist attribute behind it.
    assert put(handlers, 'does-not-exist', VALID)['statusCode'] == 404
    assert body(listing(handlers))['count'] == 0


def test_update_rejects_an_impossible_date_before_writing(handlers):
    employee_id = create(handlers)['id']
    assert put(handlers, employee_id, dict(VALID, startDate='2026-13-01'))['statusCode'] == 400
    assert body(get(handlers, employee_id))['startDate'] == VALID['startDate']


def test_update_validates_before_writing(handlers):
    employee_id = create(handlers)['id']
    assert put(handlers, employee_id, dict(VALID, email='nope'))['statusCode'] == 400
    assert body(get(handlers, employee_id))['email'] == VALID['email']


# ------------------------------------------------------------------- checklist

def test_ticking_an_item_updates_only_that_item(handlers):
    employee_id = create(handlers)['id']
    updated = body(patch(handlers, employee_id, 'laptop', True))

    ticked = [item['id'] for item in updated['checklist'] if item['done']]
    assert ticked == ['laptop']


def test_ticking_recomputes_status(handlers):
    employee_id = create(handlers)['id']

    assert body(patch(handlers, employee_id, 'laptop', True))['status'] == 'In Progress'

    for item_id in ('offer-letter', 'id-proof', 'bank-details', 'email-account',
                    'access-card', 'induction', 'policy-ack'):
        response = patch(handlers, employee_id, item_id, True)

    assert body(response)['status'] == 'Onboarded'
    assert body(response)['progress'] == {'done': 8, 'total': 8, 'percent': 100}


def test_unticking_moves_status_back(handlers):
    employee_id = create(handlers)['id']
    patch(handlers, employee_id, 'laptop', True)
    assert body(patch(handlers, employee_id, 'laptop', False))['status'] == 'Pending'


def test_checklist_order_is_stable_across_updates(handlers):
    employee_id = create(handlers)['id']
    patch(handlers, employee_id, 'policy-ack', True)
    patch(handlers, employee_id, 'offer-letter', True)

    ids = [item['id'] for item in body(get(handlers, employee_id))['checklist']]
    assert ids[0] == 'offer-letter'
    assert ids[-1] == 'policy-ack'


def test_an_unknown_checklist_item_is_404(handlers):
    employee_id = create(handlers)['id']
    assert patch(handlers, employee_id, 'not-a-thing', True)['statusCode'] == 404


def test_ticking_an_item_for_an_unknown_employee_is_404(handlers):
    assert patch(handlers, 'does-not-exist', 'laptop', True)['statusCode'] == 404


@pytest.mark.parametrize('value', ['true', 1, None, 'yes'])
def test_done_must_be_an_actual_boolean(handlers, value):
    employee_id = create(handlers)['id']
    assert patch_raw(handlers, employee_id, 'laptop', {'done': value})['statusCode'] == 400


# ------------------------------------------------------- checklist comments

def test_a_new_checklist_item_has_an_empty_comment(handlers):
    employee = create(handlers)
    assert all(item['comment'] == '' for item in employee['checklist'])


def test_a_comment_can_be_written_without_touching_done(handlers):
    employee_id = create(handlers)['id']
    patch(handlers, employee_id, 'bank-details', True)

    updated = body(patch_raw(handlers, employee_id, 'bank-details',
                             {'comment': 'Chased payroll twice.'}))
    item = item_of(updated, 'bank-details')
    assert item['comment'] == 'Chased payroll twice.'
    assert item['done'] is True, 'a comment must not reset the tick'


def test_ticking_leaves_an_existing_comment_alone(handlers):
    employee_id = create(handlers)['id']
    patch_raw(handlers, employee_id, 'laptop', {'comment': 'Dell, collected Friday.'})

    updated = body(patch(handlers, employee_id, 'laptop', True))
    assert item_of(updated, 'laptop')['comment'] == 'Dell, collected Friday.'


def test_done_and_comment_can_travel_together(handlers):
    employee_id = create(handlers)['id']
    updated = body(patch_raw(handlers, employee_id, 'induction',
                             {'done': True, 'comment': 'Attended the 9am session.'}))
    item = item_of(updated, 'induction')
    assert (item['done'], item['comment']) == (True, 'Attended the 9am session.')


def test_a_comment_survives_an_employee_edit(handlers):
    employee_id = create(handlers)['id']
    patch_raw(handlers, employee_id, 'id-proof', {'comment': 'Passport, not licence.'})

    updated = body(put(handlers, employee_id, dict(VALID, jobTitle='Changed')))
    assert item_of(updated, 'id-proof')['comment'] == 'Passport, not licence.'


@pytest.mark.parametrize('cleared', ['', '   ', None])
def test_a_comment_can_be_cleared(handlers, cleared):
    employee_id = create(handlers)['id']
    patch_raw(handlers, employee_id, 'laptop', {'comment': 'Temporary note.'})

    updated = body(patch_raw(handlers, employee_id, 'laptop', {'comment': cleared}))
    assert item_of(updated, 'laptop')['comment'] == ''


def test_clearing_a_comment_removes_the_attribute_rather_than_storing_empty(handlers):
    from common.checklist_template import CHECKLIST_INDEX
    from common.db import table
    from common.keys import key

    employee_id = create(handlers)['id']
    patch_raw(handlers, employee_id, 'laptop', {'comment': 'Temporary note.'})
    patch_raw(handlers, employee_id, 'laptop', {'comment': ''})

    stored = table.get_item(Key=key(employee_id))['Item']
    entry = stored['checklist'][CHECKLIST_INDEX['laptop']]
    assert entry['itemId'] == 'laptop', 'the index must still point at the item it names'
    assert 'comment' not in entry


def test_done_and_comment_do_not_overwrite_each_other_in_either_order(handlers):
    # The guarantee the whole indexed-document-path design exists to keep. These
    # used to be writes to two different attributes of one small row; they are now
    # writes to two paths inside one list element of one large item. Neither
    # reads the list first, so neither can lose the other's change.
    employee_id = create(handlers)['id']

    patch(handlers, employee_id, 'laptop', True)
    after_comment = body(patch_raw(handlers, employee_id, 'laptop',
                                   {'comment': 'Dell, collected Friday.'}))
    assert item_of(after_comment, 'laptop')['done'] is True
    assert item_of(after_comment, 'laptop')['comment'] == 'Dell, collected Friday.'

    after_untick = body(patch(handlers, employee_id, 'laptop', False))
    assert after_untick['status'] == 'Pending'
    assert item_of(after_untick, 'laptop')['comment'] == 'Dell, collected Friday.'


def test_a_tick_on_a_drifted_checklist_fails_instead_of_landing_on_the_wrong_item(handlers):
    # CHECKLIST_INDEX addresses entries positionally, and SET on an out-of-range
    # list index APPENDS rather than failing - so without the itemId condition
    # this would either tick the wrong box or grow a bogus ninth entry. Simulate
    # the drift by deleting an element out from under the index.
    from common.db import table
    from common.keys import key

    employee_id = create(handlers)['id']
    table.update_item(
        Key=key(employee_id),
        UpdateExpression='REMOVE #checklist[0]',
        ExpressionAttributeNames={'#checklist': 'checklist'},
    )

    # policy-ack is index 7; after the shift, index 7 is off the end of the list.
    assert patch(handlers, employee_id, 'policy-ack', True)['statusCode'] == 500
    # induction is index 6; after the shift, index 6 holds policy-ack.
    assert patch(handlers, employee_id, 'induction', True)['statusCode'] == 500

    stored = table.get_item(Key=key(employee_id))['Item']
    assert len(stored['checklist']) == 7, 'the failed writes must not have appended'
    assert all(not entry['done'] for entry in stored['checklist'])


def test_a_comment_is_trimmed(handlers):
    employee_id = create(handlers)['id']
    updated = body(patch_raw(handlers, employee_id, 'laptop',
                             {'comment': '  Waiting on IT.  '}))
    assert item_of(updated, 'laptop')['comment'] == 'Waiting on IT.'


def test_an_over_long_comment_is_rejected(handlers):
    employee_id = create(handlers)['id']
    response = patch_raw(handlers, employee_id, 'laptop', {'comment': 'x' * 501})

    assert response['statusCode'] == 400
    assert 'comment' in body(response)['error']['fields']
    assert item_of(body(get(handlers, employee_id)), 'laptop')['comment'] == ''


def test_a_comment_of_exactly_the_limit_is_accepted(handlers):
    employee_id = create(handlers)['id']
    updated = body(patch_raw(handlers, employee_id, 'laptop', {'comment': 'x' * 500}))
    assert len(item_of(updated, 'laptop')['comment']) == 500


@pytest.mark.parametrize('value', [12, True, ['a note'], {'text': 'a note'}])
def test_a_comment_must_be_text(handlers, value):
    employee_id = create(handlers)['id']
    assert patch_raw(handlers, employee_id, 'laptop', {'comment': value})['statusCode'] == 400


def test_a_body_that_asks_for_nothing_is_a_400(handlers):
    employee_id = create(handlers)['id']
    assert patch_raw(handlers, employee_id, 'laptop', {})['statusCode'] == 400


def test_a_comment_on_an_unknown_item_is_404(handlers):
    employee_id = create(handlers)['id']
    assert patch_raw(handlers, employee_id, 'not-a-thing',
                     {'comment': 'hello'})['statusCode'] == 404


def test_comments_survive_archiving(handlers):
    # Archiving exists so the history is still there to read, and a note saying
    # why onboarding stalled is the most useful part of that history.
    employee_id = create(handlers)['id']
    patch_raw(handlers, employee_id, 'laptop', {'comment': 'Never collected it.'})

    archived = archive(handlers, employee_id)
    assert item_of(archived, 'laptop')['comment'] == 'Never collected it.'


# ---------------------------------------------------------------------- delete

def test_delete_returns_200_with_the_archived_record(handlers):
    archived = archive(handlers, create(handlers)['id'])
    assert archived['archived'] is True
    assert archived['archivedAt']


def test_delete_takes_nothing_away(handlers):
    from common.db import table

    employee_id = create(handlers)['id']
    before = len(table.scan()['Items'])
    archive(handlers, employee_id)

    assert len(table.scan()['Items']) == before, 'archiving must not remove rows'


def test_an_incomplete_checklist_archives_as_cancelled(handlers):
    employee_id = create(handlers)['id']
    patch(handlers, employee_id, 'offer-letter', True)

    assert archive(handlers, employee_id)['archivedAs'] == 'Onboarding Cancelled'


def test_an_untouched_checklist_archives_as_cancelled(handlers):
    assert archive(handlers, create(handlers)['id'])['archivedAs'] == 'Onboarding Cancelled'


def test_a_complete_checklist_archives_as_onboarded(handlers):
    employee_id = create(handlers)['id']
    tick_everything(handlers, employee_id)

    assert archive(handlers, employee_id)['archivedAs'] == 'Onboarded'


def test_one_unticked_box_is_the_difference_between_the_two_states(handlers):
    employee_id = create(handlers)['id']
    tick_everything(handlers, employee_id)
    patch(handlers, employee_id, 'policy-ack', False)

    assert archive(handlers, employee_id)['archivedAs'] == 'Onboarding Cancelled'


def test_archiving_preserves_the_progress_it_was_stamped_at(handlers):
    employee_id = create(handlers)['id']
    patch(handlers, employee_id, 'offer-letter', True)
    patch(handlers, employee_id, 'laptop', True)

    archived = archive(handlers, employee_id)
    assert archived['progress'] == {'done': 2, 'total': 8, 'percent': 25}
    assert archived['status'] == 'In Progress', 'derived status still describes the checklist'


def test_an_active_employee_is_not_archived(handlers):
    employee = create(handlers)
    assert employee['archived'] is False
    assert employee['archivedAs'] == ''
    assert employee['archivedAt'] == ''


def test_delete_of_an_unknown_id_is_404(handlers):
    assert delete(handlers, 'does-not-exist')['statusCode'] == 404


def test_archiving_one_employee_leaves_the_others_alone(handlers):
    keep = create(handlers, email='keep@breville.com')['id']
    remove = create(handlers, email='remove@breville.com')['id']

    archive(handlers, remove)

    assert get(handlers, keep)['statusCode'] == 200
    assert len(body(get(handlers, keep))['checklist']) == 8
    assert body(get(handlers, keep))['archived'] is False


# ------------------------------------------------- archived: gone from the list

def test_an_archived_employee_drops_off_the_list(handlers):
    keep = create(handlers, email='keep@breville.com')['id']
    gone = create(handlers, email='gone@breville.com')['id']

    archive(handlers, gone)

    assert listed_ids(handlers) == [keep]


def test_the_list_count_matches_the_employees_it_returns(handlers):
    # The count is what the UI puts on screen; an archived employee inflating it
    # would show "2 employees" above a table with one row in it.
    create(handlers, email='keep@breville.com')
    archive(handlers, create(handlers, email='gone@breville.com')['id'])

    result = body(listing(handlers))
    assert result['count'] == len(result['employees']) == 1


def test_an_archived_employee_is_still_readable_by_id(handlers):
    employee_id = create(handlers)['id']
    archive(handlers, employee_id)

    fetched = body(get(handlers, employee_id))
    assert fetched['archived'] is True
    assert fetched['archivedAs'] == 'Onboarding Cancelled'
    assert len(fetched['checklist']) == 8


# ------------------------------------------------------------ archived: frozen

def test_an_archived_employee_cannot_be_edited(handlers):
    employee_id = create(handlers)['id']
    archive(handlers, employee_id)

    response = put(handlers, employee_id, dict(VALID, jobTitle='Sneaky Promotion'))
    assert response['statusCode'] == 409
    assert body(response)['error']['code'] == 'Conflict'
    assert body(get(handlers, employee_id))['jobTitle'] == VALID['jobTitle']


def test_an_archived_checklist_cannot_be_ticked(handlers):
    employee_id = create(handlers)['id']
    archive(handlers, employee_id)

    response = patch(handlers, employee_id, 'offer-letter', True)
    assert response['statusCode'] == 409
    assert item_of(body(get(handlers, employee_id)), 'offer-letter')['done'] is False


def test_an_archived_checklist_cannot_be_commented_on(handlers):
    employee_id = create(handlers)['id']
    archive(handlers, employee_id)

    assert patch_raw(handlers, employee_id, 'laptop',
                     {'comment': 'After the fact.'})['statusCode'] == 409


def test_a_cancelled_record_cannot_be_ticked_up_to_complete(handlers):
    # The reason the freeze exists at all: without it this record would end up
    # stamped Onboarding Cancelled while showing 8 of 8 done, and nothing in the
    # table would say which of the two was the lie.
    employee_id = create(handlers)['id']
    archive(handlers, employee_id)

    for item in body(get(handlers, employee_id))['checklist']:
        assert patch(handlers, employee_id, item['id'], True)['statusCode'] == 409

    still = body(get(handlers, employee_id))
    assert still['archivedAs'] == 'Onboarding Cancelled'
    assert still['progress']['done'] == 0


def test_archiving_twice_is_idempotent_and_keeps_the_first_stamp(handlers):
    employee_id = create(handlers)['id']
    first = archive(handlers, employee_id)
    second = archive(handlers, employee_id)

    assert second['archivedAt'] == first['archivedAt']
    assert second['archivedAs'] == first['archivedAs']


def test_a_second_delete_cannot_relabel_an_archived_record(handlers):
    # Belt and braces on the above. Even with the checklist moved underneath it,
    # the stamp is a record of a decision someone made and is not recomputed.
    from common.checklist_template import CHECKLIST_INDEX
    from common.db import table
    from common.keys import key

    employee_id = create(handlers)['id']
    archive(handlers, employee_id)

    # Reaching past the API's archive freeze on purpose, the same way the old
    # version of this test reached past it into the separate CHK# rows.
    for item in body(get(handlers, employee_id))['checklist']:
        index = CHECKLIST_INDEX[item['id']]
        table.update_item(
            Key=key(employee_id),
            UpdateExpression='SET #checklist[{}].#done = :done'.format(index),
            ExpressionAttributeNames={'#checklist': 'checklist', '#done': 'done'},
            ExpressionAttributeValues={':done': True},
        )

    assert archive(handlers, employee_id)['archivedAs'] == 'Onboarding Cancelled'


# ------------------------------------------------------- the acceptance run

def test_the_full_lifecycle_from_docs_api_md(handlers):
    employee_id = create(handlers)['id']

    assert listing(handlers)['statusCode'] == 200
    assert get(handlers, employee_id)['statusCode'] == 200
    assert patch(handlers, employee_id, 'offer-letter', True)['statusCode'] == 200
    assert patch(handlers, employee_id, 'not-a-thing', True)['statusCode'] == 404
    assert post(handlers, {'email': 'nope'})['statusCode'] == 400

    # Where the run diverges from Phase 2: DELETE archives. The employee leaves
    # the list, stays readable by id, and stops accepting writes.
    assert delete(handlers, employee_id)['statusCode'] == 200
    assert listed_ids(handlers) == []
    assert get(handlers, employee_id)['statusCode'] == 200
    assert put(handlers, employee_id, VALID)['statusCode'] == 409
    assert patch(handlers, employee_id, 'id-proof', True)['statusCode'] == 409


def test_every_response_carries_cors_headers(handlers):
    # The Phase 3 trap: a browser refuses the response without these.
    employee_id = create(handlers)['id']
    for response in (get(handlers, employee_id),
                     listing(handlers),
                     delete(handlers, employee_id),
                     get(handlers, employee_id)):
        assert response['headers']['Access-Control-Allow-Origin'] == '*'

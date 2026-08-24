"""
The six handlers, driven end to end against an in-memory DynamoDB.

These mirror the acceptance run in docs/api.md. Passing here does not mean the
stack deploys - IAM is not emulated - but it does mean the DynamoDB calls,
condition expressions and response shapes are right before anyone waits on
`sam deploy`.
"""
import json

import pytest

VALID = {
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


def body(response):
    return json.loads(response['body'])


def post(handlers, payload):
    return handlers['create_employee']({'body': json.dumps(payload)}, None)


def get(handlers, employee_id):
    return handlers['get_employee']({'pathParameters': {'id': employee_id}}, None)


def put(handlers, employee_id, payload):
    return handlers['update_employee'](
        {'pathParameters': {'id': employee_id}, 'body': json.dumps(payload)}, None)


def delete(handlers, employee_id):
    return handlers['delete_employee']({'pathParameters': {'id': employee_id}}, None)


def patch(handlers, employee_id, item_id, done):
    return patch_raw(handlers, employee_id, item_id, {'done': done})


def patch_raw(handlers, employee_id, item_id, payload):
    return handlers['set_checklist_item'](
        {'pathParameters': {'id': employee_id, 'itemId': item_id},
         'body': json.dumps(payload)}, None)


def item_of(employee, item_id):
    return [i for i in employee['checklist'] if i['id'] == item_id][0]


def create(handlers, **overrides):
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
    assert body(handlers['list_employees']({}, None))['count'] == 0


def test_create_rejects_a_department_outside_the_enum(handlers):
    response = post(handlers, dict(VALID, department='Marketing'))
    assert response['statusCode'] == 400


def test_create_ignores_id_and_checklist_supplied_by_the_caller(handlers):
    employee = create(handlers, id='hacked', checklist=[])
    assert employee['id'] != 'hacked'
    assert len(employee['checklist']) == 8


def test_create_rejects_a_body_that_is_not_json(handlers):
    response = handlers['create_employee']({'body': 'not json'}, None)
    assert response['statusCode'] == 400


def test_create_rejects_a_start_date_that_is_not_a_real_day(handlers):
    response = post(handlers, dict(VALID, startDate='2026-02-30'))
    assert response['statusCode'] == 400
    assert 'startDate' in body(response)['error']['fields']


# ------------------------------------------------------- email uniqueness

def test_a_second_employee_cannot_take_a_used_work_email(handlers):
    create(handlers)

    response = post(handlers, dict(VALID, firstName='Someone', lastName='Else'))
    assert response['statusCode'] == 409
    assert body(response)['error']['fields']['email']

    # And the rejected create wrote nothing - not a stray profile, not a
    # half-seeded checklist.
    assert body(handlers['list_employees']({}, None))['count'] == 1


def test_uniqueness_ignores_case(handlers):
    create(handlers, email='priya.sharma@breville.com')
    assert post(handlers, dict(VALID, email='Priya.Sharma@Breville.com'))['statusCode'] == 409


def test_the_guard_is_not_mistaken_for_an_employee(handlers):
    create(handlers)
    result = body(handlers['list_employees']({}, None))
    assert result['count'] == 1
    assert result['employees'][0]['email'] == VALID['email']


def test_update_cannot_move_an_employee_onto_a_used_email(handlers):
    create(handlers, email='taken@breville.com')
    other = create(handlers, email='free@breville.com')['id']

    response = put(handlers, other, dict(VALID, email='taken@breville.com'))
    assert response['statusCode'] == 409
    assert body(get(handlers, other))['email'] == 'free@breville.com'


def test_update_that_leaves_the_email_alone_is_not_a_conflict_with_itself(handlers):
    employee_id = create(handlers)['id']
    updated = put(handlers, employee_id, dict(VALID, jobTitle='Senior Software Engineer'))
    assert updated['statusCode'] == 200


def test_changing_an_email_frees_the_old_one(handlers):
    employee_id = create(handlers, email='old@breville.com')['id']
    assert put(handlers, employee_id, dict(VALID, email='new@breville.com'))['statusCode'] == 200

    assert post(handlers, dict(VALID, email='old@breville.com'))['statusCode'] == 201
    assert post(handlers, dict(VALID, email='new@breville.com'))['statusCode'] == 409


def test_deleting_an_employee_frees_their_email(handlers):
    employee_id = create(handlers)['id']
    delete(handlers, employee_id)
    assert post(handlers, VALID)['statusCode'] == 201


def test_delete_leaves_no_guard_behind(handlers):
    from common.db import table

    delete(handlers, create(handlers)['id'])
    assert table.scan()['Items'] == [], 'an orphan guard would lock that email forever'


# ------------------------------------------------------------------------- get

def test_get_returns_the_employee_with_its_checklist(handlers):
    created = create(handlers)
    fetched = body(get(handlers, created['id']))
    assert fetched == created


def test_get_of_an_unknown_id_is_404(handlers):
    assert get(handlers, 'does-not-exist')['statusCode'] == 404


def test_get_leaks_no_internal_attributes(handlers):
    employee = body(get(handlers, create(handlers)['id']))
    for internal in ('PK', 'SK', 'employeeId', 'entityType'):
        assert internal not in employee


# ------------------------------------------------------------------------ list

def test_list_is_empty_before_anything_is_created(handlers):
    result = body(handlers['list_employees']({}, None))
    assert result == {'employees': [], 'count': 0}


def test_list_returns_every_employee_with_their_checklist(handlers):
    create(handlers, email='a@breville.com', startDate='2026-07-06')
    create(handlers, email='b@breville.com', startDate='2026-08-10')

    result = body(handlers['list_employees']({}, None))
    assert result['count'] == 2
    assert all(len(employee['checklist']) == 8 for employee in result['employees'])


def test_list_is_sorted_by_start_date(handlers):
    create(handlers, lastName='Later', email='later@breville.com', startDate='2026-09-01')
    create(handlers, lastName='Earlier', email='earlier@breville.com', startDate='2026-07-06')

    names = [e['lastName'] for e in body(handlers['list_employees']({}, None))['employees']]
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
    updated = body(put(handlers, employee_id, dict(VALID, id='hacked', checklist=[])))
    assert updated['id'] == employee_id
    assert len(updated['checklist']) == 8


def test_update_of_an_unknown_id_is_404_and_creates_no_ghost_record(handlers):
    # Without the condition expression, UpdateItem would upsert a profile with
    # no checklist rows behind it.
    assert put(handlers, 'does-not-exist', VALID)['statusCode'] == 404
    assert body(handlers['list_employees']({}, None))['count'] == 0


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
    from common.db import table
    from common.keys import chk_sk, pk

    employee_id = create(handlers)['id']
    patch_raw(handlers, employee_id, 'laptop', {'comment': 'Temporary note.'})
    patch_raw(handlers, employee_id, 'laptop', {'comment': ''})

    stored = table.get_item(Key={'PK': pk(employee_id), 'SK': chk_sk('laptop')})['Item']
    assert 'comment' not in stored


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


def test_comments_are_deleted_with_the_employee(handlers):
    from common.db import table

    employee_id = create(handlers)['id']
    patch_raw(handlers, employee_id, 'laptop', {'comment': 'Should not outlive them.'})
    delete(handlers, employee_id)

    assert table.scan()['Items'] == []


# ---------------------------------------------------------------------- delete

def test_delete_returns_204_with_no_body(handlers):
    response = delete(handlers, create(handlers)['id'])
    assert response['statusCode'] == 204
    assert 'body' not in response


def test_delete_removes_the_profile_and_every_checklist_row(handlers):
    from common.db import table

    employee_id = create(handlers)['id']
    delete(handlers, employee_id)

    leftovers = table.scan()['Items']
    assert leftovers == [], 'orphan checklist items were left behind'


def test_delete_of_an_unknown_id_is_404(handlers):
    assert delete(handlers, 'does-not-exist')['statusCode'] == 404


def test_deleting_one_employee_leaves_the_others_alone(handlers):
    keep = create(handlers, email='keep@breville.com')['id']
    remove = create(handlers, email='remove@breville.com')['id']

    delete(handlers, remove)

    assert get(handlers, keep)['statusCode'] == 200
    assert len(body(get(handlers, keep))['checklist']) == 8


# ------------------------------------------------------- the acceptance run

def test_the_full_lifecycle_from_docs_api_md(handlers):
    employee_id = create(handlers)['id']

    assert handlers['list_employees']({}, None)['statusCode'] == 200
    assert get(handlers, employee_id)['statusCode'] == 200
    assert patch(handlers, employee_id, 'offer-letter', True)['statusCode'] == 200
    assert patch(handlers, employee_id, 'not-a-thing', True)['statusCode'] == 404
    assert post(handlers, {'email': 'nope'})['statusCode'] == 400
    assert delete(handlers, employee_id)['statusCode'] == 204
    assert get(handlers, employee_id)['statusCode'] == 404
    assert put(handlers, employee_id, VALID)['statusCode'] == 404


def test_every_response_carries_cors_headers(handlers):
    # The Phase 3 trap: a browser refuses the response without these.
    employee_id = create(handlers)['id']
    for response in (get(handlers, employee_id),
                     handlers['list_employees']({}, None),
                     delete(handlers, employee_id),
                     get(handlers, employee_id)):
        assert response['headers']['Access-Control-Allow-Origin'] == '*'

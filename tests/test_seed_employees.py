"""The deterministic 30-person dataset used to reset the deployed dev stack."""
from collections import Counter

from common.models import validate_employee, validate_employee_id
from scripts import seed_employees as seed


def test_seed_contains_exactly_ten_records_in_each_dashboard():
    counts = Counter(entry['promote'] or 'onboarding' for entry in seed.FIXTURES)

    assert counts == {'onboarding': 10, 'employee': 10, 'intern': 10}


def test_seed_ids_and_contact_details_are_stable_and_unique():
    profiles = [entry['profile'] for entry in seed.FIXTURES]

    assert [profile['employeeId'] for profile in profiles] == [
        'E{:04d}'.format(number) for number in range(1001, 1031)
    ]
    assert len({profile['email'] for profile in profiles}) == 30
    assert len({profile['phone'] for profile in profiles}) == 30


def test_every_seed_profile_satisfies_the_public_api_validation_contract():
    for entry in seed.FIXTURES:
        profile = entry['profile']
        assert validate_employee_id(profile['employeeId']) is None
        assert validate_employee(profile) == {}


def test_every_promoted_record_has_a_complete_checklist():
    promoted = [entry for entry in seed.FIXTURES if entry['promote']]

    assert all(entry['done'] == seed.CHECKLIST_IDS for entry in promoted)


def test_onboarding_records_have_useful_varied_incomplete_progress():
    onboarding = [entry for entry in seed.FIXTURES if entry['promote'] is None]
    progress_counts = [len(entry['done']) for entry in onboarding]

    assert min(progress_counts) == 0
    assert max(progress_counts) == 7
    assert len(set(progress_counts)) >= 8


def test_every_intern_points_to_an_earlier_promoted_employee():
    promoted_employees = set()

    for entry in seed.FIXTURES:
        if entry['promote'] == 'employee':
            promoted_employees.add(entry['profile']['employeeId'])
        elif entry['promote'] == 'intern':
            assert entry['profile']['employmentType'] == 'Intern'
            assert entry['manager'] in promoted_employees

    assert {entry['manager'] for entry in seed.FIXTURES
            if entry['promote'] == 'intern'} == promoted_employees


def test_wipe_includes_attendance_with_its_complete_composite_key(monkeypatch):
    calls = []
    monkeypatch.setattr(seed, 'wipe_table', lambda *args: calls.append(args))

    seed.wipe({
        'onboarding': 'onboarding-table',
        'employee': 'employee-table',
        'attendance': 'attendance-table',
    }, True)

    assert calls[2][0] == 'attendance-table'
    assert calls[2][1] == ('employeeKey', 'attendanceDate')
    assert calls[2][2] == 'employeeKey'

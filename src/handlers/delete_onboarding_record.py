"""
DELETE /onboarding/{id} - the destructive last step of a promote sequence.

Not the same route as DELETE /employees/{id} (handlers/delete_employee.py),
which archives an abandoned onboarding in place and is unchanged. This one
actually removes the row from OnboardingTable, and it exists only to be called
after the record has already been copied to EmployeeTable - as an employee or
as an intern, either counts.

That "already copied" fact is a hard precondition, not a suggestion: this
handler refuses with 409 unless the id already exists in EmployeeTable. Called
out of order - before promote_to_employee.py or promote_to_intern.py has run -
it would destroy the only copy of someone's onboarding history, which is
exactly the failure mode the decomposed-sequence design (see
docs/database-design.md#promotion) has to rule out somewhere. This is where it
is ruled out.

Idempotent: deleting an id that is not in the onboarding table at all is 200
just the same, because the previous copy of this record having already been
promoted-and-removed is indistinguishable from this call having already run.
"""
from common import responses
from common.db import onboarding_table
from common.handler import api_handler, employee_id_param, require_official
from common.keys import key
from common.repository import load_staff_record

_NOT_YET_PROMOTED = ('This record has not been copied to the employee or intern '
                      'dashboard yet. Promote it first.')


@api_handler
def lambda_handler(event, context):
    require_official(event)

    employee_id = employee_id_param(event)

    staff_record, source = load_staff_record(employee_id, consistent=True)
    if staff_record is None:
        return responses.conflict(_NOT_YET_PROMOTED)

    # A plain DeleteItem, no ConditionExpression: whether the row is there or
    # already gone, the caller's intent - "this id should not be in onboarding
    # any more" - is satisfied either way, which is what makes a repeat of this
    # call after a partial failure harmless.
    onboarding_table.delete_item(Key=key(employee_id))

    return responses.ok({'id': employee_id, 'movedTo': source})

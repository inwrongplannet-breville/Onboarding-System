"""
The onboarding checklist every new hire starts with.

The only copy. The frontend used to carry a duplicate for its mock data; that went
away in Phase 3 when the UI started reading employees from this API, so this list
is now the single definition of what a new hire's checklist contains.
"""

CHECKLIST_TEMPLATE = [
    {'id': 'offer-letter',  'label': 'Offer letter signed',           'owner': 'HR',         'order': 1},
    {'id': 'id-proof',      'label': 'ID proof submitted',            'owner': 'Employee',   'order': 2},
    {'id': 'bank-details',  'label': 'Bank details collected',        'owner': 'Employee',   'order': 3},
    {'id': 'laptop',        'label': 'Laptop issued',                 'owner': 'IT',         'order': 4},
    {'id': 'email-account', 'label': 'Email / AD account created',    'owner': 'IT',         'order': 5},
    {'id': 'access-card',   'label': 'Building access card issued',   'owner': 'Operations', 'order': 6},
    {'id': 'induction',     'label': 'Induction session attended',    'owner': 'HR',         'order': 7},
    {'id': 'policy-ack',    'label': 'Policy acknowledgement signed', 'owner': 'Employee',   'order': 8},
]

VALID_ITEM_IDS = frozenset(item['id'] for item in CHECKLIST_TEMPLATE)

# Where each item sits in the embedded `checklist` list, so a PATCH can build the
# document path `checklist[i].done` without reading the list first. That is the
# whole reason ticking a box is still a single server-side UpdateItem and not a
# read-modify-write two people can lose a tick to.
#
# The index is POSITIONAL, which makes two things load-bearing:
#
#   1. No code may ever REMOVE or list_append a whole element. `REMOVE
#      checklist[3]` deletes the element and shifts every later index down by
#      one, and nothing here would notice - only `checklist[i].comment` is ever
#      removed, never `checklist[i]` itself.
#   2. Every write pairs its path with a `checklist[i].itemId = :itemId`
#      condition, because SET on an out-of-range index silently APPENDS instead
#      of failing. The condition turns a desynced list into a failed write.
CHECKLIST_INDEX = {item['id']: index for index, item in enumerate(CHECKLIST_TEMPLATE)}

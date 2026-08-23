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

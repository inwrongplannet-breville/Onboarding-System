/**
 * Mock data and shared model helpers.
 *
 * Phase 1 only: everything here is hardcoded. In a later phase the seed data
 * goes away entirely and employees come from DynamoDB via API Gateway - the
 * helpers below (progress, computeStatus, formatDate) stay as-is.
 */
window.App = window.App || {};

(function (App) {
  'use strict';

  App.DEPARTMENTS = ['Engineering', 'HR', 'Finance', 'Operations'];
  App.EMPLOYMENT_TYPES = ['Full-time', 'Contract', 'Intern'];

  // The onboarding checklist every new hire starts with. Owner is displayed
  // only - Phase 1 has no per-role permissions.
  //
  // Phase 2 note: src/common/checklist_template.py is now the canonical copy.
  // This one is only still here because the UI has not been pointed at the API
  // yet; it goes away in Phase 3. Keep the two in sync until then.
  var CHECKLIST_TEMPLATE = [
    { id: 'offer-letter',  label: 'Offer letter signed',           owner: 'HR' },
    { id: 'id-proof',      label: 'ID proof submitted',            owner: 'Employee' },
    { id: 'bank-details',  label: 'Bank details collected',        owner: 'Employee' },
    { id: 'laptop',        label: 'Laptop issued',                 owner: 'IT' },
    { id: 'email-account', label: 'Email / AD account created',    owner: 'IT' },
    { id: 'access-card',   label: 'Building access card issued',   owner: 'Operations' },
    { id: 'induction',     label: 'Induction session attended',    owner: 'HR' },
    { id: 'policy-ack',    label: 'Policy acknowledgement signed', owner: 'Employee' }
  ];

  /** A fresh, all-unchecked checklist for a new employee. */
  App.checklistTemplate = function () {
    return CHECKLIST_TEMPLATE.map(function (item) {
      return { id: item.id, label: item.label, owner: item.owner, done: false };
    });
  };

  /** Checklist with the given item ids pre-ticked - used to seed varied mock states. */
  function checklistWith(doneIds) {
    return App.checklistTemplate().map(function (item) {
      item.done = doneIds.indexOf(item.id) !== -1;
      return item;
    });
  }

  var SEED_EMPLOYEES = [
    {
      id: 'emp-001',
      firstName: 'Priya', lastName: 'Sharma',
      email: 'priya.sharma@breville.com', phone: '+61 412 883 016',
      department: 'Engineering', jobTitle: 'Software Engineer',
      manager: 'Santosh Kumar', startDate: '2026-07-06',
      employmentType: 'Full-time',
      checklist: checklistWith([
        'offer-letter', 'id-proof', 'bank-details', 'laptop',
        'email-account', 'access-card', 'induction', 'policy-ack'
      ])
    },
    {
      id: 'emp-002',
      firstName: 'Daniel', lastName: 'Okafor',
      email: 'daniel.okafor@breville.com', phone: '+61 431 507 224',
      department: 'Engineering', jobTitle: 'QA Engineer',
      manager: 'Santosh Kumar', startDate: '2026-08-10',
      employmentType: 'Full-time',
      checklist: checklistWith(['offer-letter', 'id-proof', 'bank-details', 'laptop', 'email-account'])
    },
    {
      id: 'emp-003',
      firstName: 'Mei Lin', lastName: 'Tan',
      email: 'meilin.tan@breville.com', phone: '+61 402 119 763',
      department: 'Finance', jobTitle: 'Financial Analyst',
      manager: 'Rachel Adams', startDate: '2026-08-24',
      employmentType: 'Full-time',
      checklist: checklistWith(['offer-letter', 'id-proof'])
    },
    {
      id: 'emp-004',
      firstName: 'Arjun', lastName: 'Nair',
      email: 'arjun.nair@breville.com', phone: '+61 448 620 195',
      department: 'Operations', jobTitle: 'Supply Chain Coordinator',
      manager: 'Grace Whitmore', startDate: '2026-09-01',
      employmentType: 'Contract',
      checklist: checklistWith([])
    },
    {
      id: 'emp-005',
      firstName: 'Sofia', lastName: 'Marchetti',
      email: 'sofia.marchetti@breville.com', phone: '+61 423 774 508',
      department: 'HR', jobTitle: 'HR Coordinator',
      manager: 'Grace Whitmore', startDate: '2026-08-17',
      employmentType: 'Full-time',
      checklist: checklistWith([
        'offer-letter', 'id-proof', 'bank-details', 'laptop', 'email-account', 'access-card'
      ])
    },
    {
      id: 'emp-006',
      firstName: 'Liam', lastName: 'Byrne',
      email: 'liam.byrne@breville.com', phone: '+61 437 285 941',
      department: 'Engineering', jobTitle: 'Data Engineering Intern',
      manager: 'Santosh Kumar', startDate: '2026-09-14',
      employmentType: 'Intern',
      checklist: checklistWith([])
    }
  ];

  App.seedEmployees = function () {
    return JSON.parse(JSON.stringify(SEED_EMPLOYEES));
  };

  App.fullName = function (employee) {
    return (employee.firstName + ' ' + employee.lastName).trim();
  };

  App.progress = function (employee) {
    var items = employee.checklist || [];
    var done = items.filter(function (item) { return item.done; }).length;
    return {
      done: done,
      total: items.length,
      percent: items.length ? Math.round((done / items.length) * 100) : 0
    };
  };

  /**
   * Status is derived, never stored - so it can't drift out of sync with the
   * checklist it describes.
   */
  App.computeStatus = function (employee) {
    var p = App.progress(employee);
    if (p.total === 0 || p.done === 0) return 'Pending';
    if (p.done === p.total) return 'Onboarded';
    return 'In Progress';
  };

  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  /** "2026-09-01" -> "1 Sep 2026". Parsed by hand to dodge timezone shifts. */
  App.formatDate = function (isoDate) {
    if (!isoDate) return '-';
    var parts = String(isoDate).split('-');
    if (parts.length !== 3) return isoDate;
    var month = MONTHS[Number(parts[1]) - 1];
    if (!month) return isoDate;
    return Number(parts[2]) + ' ' + month + ' ' + parts[0];
  };
})(window.App);

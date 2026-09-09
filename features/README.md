# Feature workspace

This directory decomposes the large [system design](../reports/automated-reup-platform-system-design.md)
into bounded, trackable implementation units.

## Structure

```text
features/
├── README.md
├── todo.md
├── _shared/
│   ├── decisions.md
│   └── delivery_contract.md
└── <task_name>/
    ├── spec.md
    └── todo.md
```

## File responsibilities

- Root `todo.md`: priority, dependency order, and whole-project status.
- `_shared/decisions.md`: confirmed choices. Change a decision here first.
- `_shared/delivery_contract.md`: authoritative source-to-platform mapping.
- Feature `spec.md`: short behavioral contract. It explains what and why, not
  line-by-line implementation.
- Feature `todo.md`: executable checklist and verification work.

## Working rules

1. Pick only tasks whose dependencies are complete.
2. Change a feature's local `todo.md` while working.
3. Mark the root checkbox complete only after the feature's `Done when` section
   is verified.
4. Record evidence next to the relevant checkbox: test name, execution ID,
   external sandbox ID, screenshot, or report path.
5. If a product decision changes, update `_shared` and every affected spec before
   changing code.
6. Do not put secrets, access tokens, account names, or customer data in specs or
   evidence.

## Status legend

- `[ ]` not started
- `[-]` in progress
- `[x]` complete and verified
- `[!]` blocked; write the blocking condition below the checkbox


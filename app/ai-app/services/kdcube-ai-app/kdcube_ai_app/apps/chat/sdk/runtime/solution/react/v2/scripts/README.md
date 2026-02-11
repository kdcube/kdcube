# React patch checks

These scripts perform lightweight syntax/format checks after `react.patch` modifies a file.
The runner is **best-effort**: if a checker is missing, it skips without failing the patch.

## Scripts

- `check_python.sh`  
  Uses: `python -m py_compile <file>`

- `check_json.sh`  
  Uses: `python` to parse JSON via `json.loads`

- `check_js.sh`  
  Uses: `node --check <file>`

- `check_tsx.sh`  
  Uses: `tsc --noEmit --jsx react --allowJs <file>`

- `check_html.sh`  
  Uses: `tidy -errors -quiet <file>`

## Runtime requirements

Install these binaries in the runtime container for full coverage:

- `python` (required for `.py` and `.json` checks)
- `node` (for `.js`/`.jsx`)
- `tsc` (TypeScript compiler, for `.ts`/`.tsx`)
- `tidy` (HTML Tidy, for `.html`/`.htm`)

Missing tools are tolerated (the check is skipped with a note).

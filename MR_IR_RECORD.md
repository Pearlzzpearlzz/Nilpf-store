
## Certification Dashboard Link Completed
- Date: 2026-05-30
- Area: TSH Program Tools / Employee Certifications
- Completed:
  - Repaired employee_certifications.html template structure.
  - Removed broken duplicate CSS cursor block.
  - Matched template variables to app.py route variables.
  - Confirmed /employee-certifications redirects to login when not logged in.
  - Confirmed /employee-certifications renders STATUS 200 when logged in.
  - Connected TSH Tools Employee Certifications card to /employee-certifications.
  - Confirmed /tsh-program-tools renders STATUS 200.
  - Confirmed certification dashboard link is present.
  - Confirmed Audit Packet Builder remains Coming Soon.
- Backups saved:
  - templates/tsh_program_tools_CERT_LINK_WORKING.html
  - templates/employee_certifications_DASH_WORKING.html
- Status: Working checkpoint saved.

## Audit Packet Builder Starter Page Completed
- Date: 2026-05-30
- Area: TSH Program Tools / Audit Packet Builder
- Completed:
  - Confirmed existing audit log system already exists in app.py.
  - Confirmed existing participant packet download route already exists.
  - Created starter template: templates/audit_packet_builder.html.
  - Added route: /audit-packet-builder.
  - Connected TSH Tools Audit Packet Builder card to /audit-packet-builder.
  - Confirmed /audit-packet-builder renders STATUS 200 when logged in.
  - Confirmed /tsh-program-tools renders STATUS 200.
  - Confirmed Audit Packet Builder link is present.
  - Confirmed no Coming Soon cards remain on TSH Tools page.
- Backups saved:
  - templates/audit_packet_builder_STARTER_WORKING.html
  - templates/tsh_program_tools_AUDIT_LINK_WORKING.html
  - app_AUDIT_PACKET_BUILDER_ROUTE_WORKING.py
- Status: Starter doorway page working and ready for future filter/export build.

## APB / HMIS Readiness Pull Logic Completed
- Date: 2026-05-30
- Area: Audit Packet Builder / HMIS-CoC Readiness Summary
- Starting checkpoint:
  - 2323106 Add HMIS CoC readiness summary and employee certification entry
- Final checkpoint:
  - 4f6d383 Build real APB HMIS readiness pull logic
- Completed:
  - Added backend scanner helper: build_apb_hmis_readiness_summary().
  - Connected /audit-packet-builder-summary to real readiness data.
  - Pulled total participant count from data/participants.json.
  - Pulled program counts by program_type.
  - Counted completed forms and final locked forms.
  - Counted intake / entry records.
  - Counted service coordination records from service_activity_record and individual_service_plan.
  - Counted exit / discharge records when present.
  - Counted check-in/check-out records from checkin_checkout_logs.
  - Counted uploads/proof records from participant uploads.
  - Counted audit log activity from available audit/Mr. IR/change log files.
  - Added missing HMIS-style readiness scan per participant.
  - Added participant readiness detail table to printable summary.
  - Added print-friendly table styling.
  - Updated old starter summary language so the page states it pulls live saved records.
- Tested:
  - Ran python3 -m py_compile app.py successfully.
  - Opened local Audit Packet Builder in browser.
  - Confirmed printable summary showed real pulled counts.
  - Confirmed visible pulled counts included participants, completed/locked forms, intake/entry, service coordination, check-in/check-out, uploads/proof, audit activity, and missing items.
  - Render auto-deployed final commit 4f6d383 live on 2026-05-30 at approximately 8:11 AM.
- Important repair note:
  - data/audit_log.json is ignored by Git because .gitignore includes data/*.json.
  - Runtime audit notes placed in data/audit_log.json do not travel with GitHub/Render deploys.
  - Permanent Mr. IR build and repair notes must be kept in tracked MR_IR_RECORD.md unless a future tracked Mr. IR record system is added.
- Files changed:
  - app.py
  - templates/audit_packet_builder_summary.html
  - MR_IR_RECORD.md
- Status: Real APB/HMIS readiness pull logic completed, tested, deployed, and now documented in tracked Mr. IR record.

## 2026-06-12 — Render Redirect Loop / ProxyFix Repair

- Area: Render Docker live app / login-session redirect behavior.
- Issue observed:
  - Browser showed `ERR_TOO_MANY_REDIRECTS`.
  - Incognito `/entry-screening/4` opened the Entry Screening page.
  - After saving Entry Screening, app redirected back to login.
- Interpretation:
  - Entry Screening PID reload appeared improved because the ES page opened.
  - The remaining issue looked like a Render HTTPS proxy/session redirect problem, not a participant/PID file problem.
- Repair applied:
  - Added `ProxyFix` import from `werkzeug.middleware.proxy_fix`.
  - Wrapped Flask app with:
    `app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_for=1)`
- Reason:
  - Render runs Flask behind a reverse proxy.
  - ProxyFix tells Flask to trust forwarded HTTPS/proxy headers from Render.
  - This may prevent HTTPS/session/login redirect loops.
- Tested:
  - Ran `python3 -m py_compile app.py` successfully.
- Pending:
  - Commit and push.
  - Confirm Render deploys the new commit.
  - Verify login and Packet Builder first.
  - Do not run another full Entry Screening stress test tonight.
- Status: ProxyFix repair documented; live Render verification pending.


## 2026-06-25 — ILH Form Cleanup

Repaired Property Belongings true-to-sight review and date handling. Removed the obsolete duplicate Privacy Acknowledgment from all active flow, packet, export, and route wiring. Retained the approved Privacy Noncommercial form, added date auto-fill, verified progression to Vehicle Parking, and deleted the obsolete privacy templates after successful testing.

Status: Working on Render.

## 2026-06-25 — ILH Form Cleanup

Property Belongings was repaired with automatic date entry and a full true-to-sight review page. The obsolete duplicate Privacy Acknowledgment was detached from all active flow, packet, export, and continuation wiring. The approved Privacy Noncommercial acknowledgment was retained, its date now auto-fills, and progression to Vehicle Parking was verified on Render.

Security history also confirms that restricted Sensitivity Vault fields were encrypted in commit `fb1a5aa`, with admin entry and unlock controls added in commits `1d00d18` and `497b8e4`.

## 2026-06-25 — Sensitivity Vault Fernet Encryption Record

Security implementation confirmed from commit `fb1a5aa`.

Environment:
- Encryption key variable: `FERNET_KEY`
- The key is stored in the Render environment.
- The secret value is not stored in Git, source code, logs, or Mr. IR.
- The Security Engine raises an error if `FERNET_KEY` is missing.

Encrypted Sensitivity Vault fields:
- date_of_birth
- dob_data_quality
- ssn
- ssn_data_quality
- hopwa_eligibility_status
- psh_disability_status
- chronic_homelessness_status
- verification_source
- staff_verification_notes
- operator_notes

Readable association fields:
- participant_name
- participant_pid

Behavior:
- Selected Vault fields are encrypted before participant data is saved.
- Existing Fernet ciphertext is not encrypted twice.
- Legacy plaintext values remain readable during migration.
- Encrypted values are decrypted only for the authorized Vault form and review views.
- Decryption failures return an empty value rather than exposing invalid data.
- Final Lock marks the Vault record completed and returns to Admin Forms.
- Unlock control was added in commit `497b8e4`.
- Admin-record access was added in commit `1d00d18`.

Security engine:
- File: `storage/security_engine.py`
- Encryption method: Fernet symmetric encryption
- Audit integrity hashing: SHA-256

## 2026-06-25 — Security Camera Form Cleanup

The Common Area Security Camera Disclosure was repaired by removing the obsolete second Date field, adding automatic local-date entry, and preserving participant name and signature fallbacks. The review page now displays one Date only, no longer shows “End of Form,” and no longer contains an outdated hardcoded continuation link.

Validation completed:
- Jinja form and print templates passed.
- `app.py` compilation passed.
- Git diff check passed.

Live Render verification remains pending.

## 2026-06-25 — Voluntary Participation Form Cleanup

The Voluntary Participation Acknowledgement was repaired by moving Save / Review into the visible form page, adding automatic local-date entry, and preserving participant name and signature fallbacks. The obsolete “End of Acknowledgement” page and old Incident Report continuation link were removed.

Validation completed:
- Jinja form and print templates passed.
- `app.py` compilation passed.
- Git diff check passed.

Live Render verification remains pending.

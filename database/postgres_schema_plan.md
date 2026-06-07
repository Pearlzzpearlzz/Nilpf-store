# NILPF Housing OS PostgreSQL Migration Plan

Goal:
Move from JSON files to PostgreSQL without breaking current routes, forms, packet builder, final lock, property papers, payments, or Mr. IR.

Core rule:
Do not change public routes during migration.

Storage design:
Use regular SQL columns for IDs, status, links, timestamps, and search fields.
Use JSONB for full form/document data.

Main participant tables:

participants
- id
- legacy_pid
- name
- program_type
- screening_status
- current_check_status
- screening JSONB
- form_data JSONB
- data JSONB
- created_at
- updated_at

participant_forms
- id
- participant_id
- form_key
- data JSONB
- locked
- completed
- created_at
- updated_at
- final_locked_at

Rule:
participant_forms should have one row per participant form.

Service / operational tables:

service_records
- id
- participant_id
- record_type
- service_date
- data JSONB
- locked
- completed
- created_at
- updated_at
- final_locked_at

participant_checkin_logs
- id
- participant_id
- action
- destination
- expected_return
- notes
- created_at
- data JSONB

participant_uploads
- id
- participant_id
- category
- original_name
- stored_path
- note
- uploaded_at
- data JSONB

Rule:
Repeatable dated records should not stay buried inside one participant JSON blob forever.

Property / operator tables:

property_papers
- id
- paper_type
- organization_name
- property_address
- route
- doc_type
- data JSONB
- locked
- completed
- created_at
- updated_at
- final_locked_at

operator_forms
- id
- form_key
- data JSONB
- locked
- completed
- created_at
- updated_at
- final_locked_at

Rule:
Property papers are organization/property records, not participant PID records.

License / payment / contact tables:

license_requests
- id
- license_number
- status
- full_name
- business_name
- email
- site_address
- city
- state
- zip_code
- phone
- notes
- created_at
- updated_at
- data JSONB

paypal_webhook_events
- id
- received_at
- event_type
- payer_email
- subscription_id
- raw_event JSONB

rolodex_contacts
- id
- organization_name
- contact_person
- role_title
- phone
- email
- organization_type
- notes
- created_at
- updated_at
- data JSONB

Staff / audit / repair tables:

employee_certifications
- id
- employee_name
- role
- certification_name
- completed_date
- expiration_date
- status
- alert
- created_at
- updated_at
- data JSONB

audit_logs
- id
- timestamp
- owner_operator_email
- business_or_property_name
- program_type
- action
- participant_id
- form_key
- route
- method
- details
- data JSONB

mr_ir_records
- id
- date
- title
- category
- status
- summary
- tested JSONB
- data JSONB
- created_at

System table:

system_activation
- id
- property_name
- license_number
- email
- password_hash
- program_type
- license_status
- business_name
- licensed_site_address
- licensed_site_city
- licensed_site_state
- licensed_site_zip
- created_at
- updated_at
- data JSONB

Migration phases:
1. Create schema only
2. Build Python storage adapter
3. Import JSON into PostgreSQL
4. Dual-write JSON + PostgreSQL
5. Switch reads to PostgreSQL
6. Keep JSON export fallback

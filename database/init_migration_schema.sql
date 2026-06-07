-- NILPF Housing OS - PostgreSQL Migration Schema
-- Hybrid Relational + JSONB Layout

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE system_activation (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    property_name VARCHAR(255),
    license_number VARCHAR(100),
    email VARCHAR(255),
    password_hash VARCHAR(255),
    program_type VARCHAR(100),
    license_status VARCHAR(50),
    business_name VARCHAR(255),
    licensed_site_address TEXT,
    licensed_site_city VARCHAR(100),
    licensed_site_state VARCHAR(50),
    licensed_site_zip VARCHAR(20),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    data JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE participants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    legacy_pid VARCHAR(100) UNIQUE,
    name VARCHAR(255),
    program_type VARCHAR(100),
    screening_status VARCHAR(100),
    current_check_status VARCHAR(100),
    screening JSONB DEFAULT '{}'::jsonb,
    form_data JSONB DEFAULT '{}'::jsonb,
    data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_participants_legacy_pid ON participants(legacy_pid);
CREATE INDEX idx_participants_program ON participants(program_type);

CREATE TABLE participant_forms (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    participant_id UUID REFERENCES participants(id) ON DELETE CASCADE,
    form_key VARCHAR(100),
    data JSONB DEFAULT '{}'::jsonb,
    locked BOOLEAN DEFAULT FALSE,
    completed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    final_locked_at TIMESTAMP WITH TIME ZONE,
    UNIQUE(participant_id, form_key)
);

CREATE INDEX idx_p_forms_lookup ON participant_forms(participant_id, form_key);

CREATE TABLE service_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    participant_id UUID REFERENCES participants(id) ON DELETE CASCADE,
    record_type VARCHAR(100),
    service_date DATE,
    data JSONB DEFAULT '{}'::jsonb,
    locked BOOLEAN DEFAULT FALSE,
    completed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    final_locked_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_service_records_p_id ON service_records(participant_id);

CREATE TABLE participant_checkin_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    participant_id UUID REFERENCES participants(id) ON DELETE CASCADE,
    action VARCHAR(100),
    destination VARCHAR(255),
    expected_return VARCHAR(100),
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    data JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE participant_uploads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    participant_id UUID REFERENCES participants(id) ON DELETE CASCADE,
    category VARCHAR(100),
    original_name VARCHAR(255),
    stored_path TEXT,
    note TEXT,
    uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    data JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE property_papers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    paper_type VARCHAR(100),
    organization_name VARCHAR(255),
    property_address TEXT,
    route VARCHAR(255),
    doc_type VARCHAR(100),
    data JSONB DEFAULT '{}'::jsonb,
    locked BOOLEAN DEFAULT FALSE,
    completed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    final_locked_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE operator_forms (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    form_key VARCHAR(100),
    data JSONB DEFAULT '{}'::jsonb,
    locked BOOLEAN DEFAULT FALSE,
    completed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    final_locked_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE license_requests (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    license_number VARCHAR(100),
    status VARCHAR(50),
    full_name VARCHAR(255),
    business_name VARCHAR(255),
    email VARCHAR(255),
    site_address TEXT,
    city VARCHAR(100),
    state VARCHAR(50),
    zip_code VARCHAR(20),
    phone VARCHAR(50),
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    data JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE paypal_webhook_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    received_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    event_type VARCHAR(100),
    payer_email VARCHAR(255),
    subscription_id VARCHAR(100),
    raw_event JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE rolodex_contacts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_name VARCHAR(255),
    contact_person VARCHAR(255),
    role_title VARCHAR(100),
    phone VARCHAR(50),
    email VARCHAR(255),
    organization_type VARCHAR(100),
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    data JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE employee_certifications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_name VARCHAR(255),
    role VARCHAR(100),
    certification_name VARCHAR(255),
    completed_date DATE,
    expiration_date DATE,
    status VARCHAR(50),
    alert BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    data JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    owner_operator_email VARCHAR(255),
    business_or_property_name VARCHAR(255),
    program_type VARCHAR(100),
    action VARCHAR(100),
    participant_id VARCHAR(100),
    form_key VARCHAR(100),
    route VARCHAR(255),
    method VARCHAR(10),
    details TEXT,
    data JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE mr_ir_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    date DATE,
    title VARCHAR(255),
    category VARCHAR(100),
    status VARCHAR(50),
    summary TEXT,
    tested JSONB DEFAULT '{}'::jsonb,
    data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

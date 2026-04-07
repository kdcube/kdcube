-- =========================================
-- deploy-kdcube-control-plane.sql
-- Control Plane Schema
-- =========================================

CREATE SCHEMA IF NOT EXISTS kdcube_control_plane;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS kdcube_control_plane.registered_projects (
    tenant      TEXT        NOT NULL,
    project     TEXT        NOT NULL,
    schema_name TEXT        NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant, project)
);

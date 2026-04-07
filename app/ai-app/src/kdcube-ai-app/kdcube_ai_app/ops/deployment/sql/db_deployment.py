# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# ops/deployment/sql/db_deployment.py

import argparse
import os, sys, re

# Add the directory to sys.path if it's not already there
from kdcube_ai_app.infra.relational.psql.psql_base import PostgreSqlDbMgr

SYSTEM_COMPONENT = "kdcube-system-schema"
PROJECT_COMPONENT = "kdcube-proj-schema"
CONTROL_PLANE_COMPONENT = "kdcube-control-plane"

PROJECT_DEFAULT_SCHEMA = "kdcube_default"
SYSTEM_SCHEMA = "kdcubesystem"
CONTROL_PLANE_SCHEMA = "kdcube_control_plane"  # NEW: Fixed schema name

SUPPORTED_COMPONENTS = [
    "experts-and-registries",
    "rbac",
    "knowledge_base",
    "event-log",
    SYSTEM_COMPONENT,
    CONTROL_PLANE_COMPONENT,
]

sql_location = os.path.dirname(__file__)



def safe_schema_name(name: str) -> str:
    """
    Turn an arbitrary string into a safe PostgreSQL schema name:
      - lowercase
      - only letters, digits, and underscores
      - starts with a letter or underscore
      - maximum length of 63 characters
      - fallback to '_schema' if name is empty after sanitization
    """
    # 1) Replace any character not a letter, digit, or underscore with '_'
    sanitized = re.sub(r'[^A-Za-z0-9_]', '_', name)
    # 2) Collapse multiple underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    # 3) Strip leading/trailing underscores
    sanitized = sanitized.strip('_')
    # 4) Lowercase
    sanitized = sanitized.lower()
    # 5) Ensure it starts with a letter or underscore
    if not re.match(r'^[a-z_]', sanitized):
        sanitized = '_' + sanitized
    # 6) Truncate to PostgreSQL's max identifier length (63 chars)
    max_len = 63
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len]
    # 7) Fallback if empty
    return sanitized or '_schema'


def project_schema(tenant: str, project: str) -> str:
    """
    Returns the PostgreSQL schema name for a given tenant/project.
    All per-tenant/project tables (conversations, economics, policies, etc.) live here.
    """
    def _sanitize(name: str) -> str:
        return re.sub(r"[^A-Za-z0-9_]", "_", name.replace("-", "_").replace(" ", "_"))
    schema_name = f"{_sanitize(tenant)}_{_sanitize(project)}"
    if not schema_name.startswith("kdcube_"):
        schema_name = f"kdcube_{schema_name}"
    return schema_name


def run(op, component, tenant=None, project=None, app=None):
    """
    Execute SQL deployment/deletion for a given component.

    Args:
        op: "deploy" or "delete"
        component: Component name (e.g., "control-plane", "kdcube-proj-schema")
        tenant: Tenant name (not used for control-plane)
        project: Project name (not used for control-plane)
        app: App name (optional subdirectory)
    """
    mgr = PostgreSqlDbMgr()

    if not component:
        error = "Please specify a component."
        print(error)
        raise Exception(error)

    # Handle control plane (global, no tenant/project)
    if component == CONTROL_PLANE_COMPONENT:
        substitutions = {"SCHEMA": CONTROL_PLANE_SCHEMA}

        if op == "deploy":
            schema_file = os.path.join(sql_location, app, f"deploy-{component}.sql")
            try:
                mgr.execute_sql_file(schema_file, substitutions=substitutions)
                print(f"Control plane schema deployed successfully to '{CONTROL_PLANE_SCHEMA}'.")
            except Exception as e:
                error = f"Error deploying control plane schema: {e}"
                print(error)
                raise e

        elif op == "delete":
            delete_file = os.path.join(sql_location, app, f"drop-{component}.sql")
            try:
                mgr.execute_sql_file(delete_file, substitutions=substitutions)
                print(f"Control plane schema components removed successfully.")
            except Exception as e:
                error = f"Error deleting control plane schema: {e}"
                print(error)
                raise e
        else:
            print("Please specify --deploy or --delete.")

        return

    # Handle tenant/project-specific components (existing logic)
    schema = project_schema(tenant or "default", project or "default-project")
    substitutions = {
        "SCHEMA": schema,
        "SYSTEM_SCHEMA": SYSTEM_SCHEMA
    }

    if op == "deploy":
        if app:
            schema_file = os.path.join(sql_location, app, f"deploy-{component}.sql")
        else:
            schema_file = os.path.join(sql_location, f"deploy-{component}.sql")
        try:
            mgr.execute_sql_file(schema_file, substitutions=substitutions)
            print(f"Schema deployed successfully: {schema}")
            if tenant and project:
                try:
                    mgr.execute_sql(
                        f"""
                        INSERT INTO {CONTROL_PLANE_SCHEMA}.registered_projects (tenant, project, schema_name)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (tenant, project) DO UPDATE SET schema_name = EXCLUDED.schema_name
                        """,
                        data=(tenant, project, schema)
                    )
                    print(f"Registered project '{tenant}/{project}' in control plane.")
                except Exception as reg_err:
                    print(f"Warning: could not register project in control plane (deploy control plane first): {reg_err}")
        except Exception as e:
            error = f"Error deploying schema: {e}"
            print(error)
            raise e

    elif op == "delete":
        if app:
            delete_file = os.path.join(sql_location, app, f"drop-{component}.sql")
        else:
            delete_file = os.path.join(sql_location, f"drop-{component}.sql")
        try:
            mgr.execute_sql_file(delete_file, substitutions=substitutions)
            print(f"Schema deleted successfully: {schema}")
            if tenant and project:
                try:
                    mgr.execute_sql(
                        f"DELETE FROM {CONTROL_PLANE_SCHEMA}.registered_projects WHERE tenant = %s AND project = %s",
                        data=(tenant, project)
                    )
                    print(f"Deregistered project '{tenant}/{project}' from control plane.")
                except Exception as reg_err:
                    print(f"Warning: could not deregister project from control plane: {reg_err}")
        except Exception as e:
            error = f"Error deleting schema: {e}"
            print(error)
            raise e
    else:
        print("Please specify --deploy or --delete.")


def main(args, parser):
    """
    Main function to handle CLI arguments and execute scripts.
    """
    if args.deploy:
        op = "deploy"
    elif args.delete:
        op = "delete"
    else:
        print("Please specify --deploy or --delete.")
        parser and parser.print_help()
        return False

    component = args.component

    if not component:
        print("Please specify a component.")
        parser and parser.print_help()
        sys.exit(1)

    # Control plane doesn't need tenant/project
    if component == CONTROL_PLANE_COMPONENT:
        run(op, component)
        return True

    # Other components need tenant/project
    project = args.project
    if not project and component != SYSTEM_COMPONENT:
        print("Please specify a project name.")
        parser and parser.print_help()
        sys.exit(1)

    tenant = args.tenant
    if not tenant and component != SYSTEM_COMPONENT:
        print("Please specify a tenant name.")
        parser and parser.print_help()
        sys.exit(1)
    app = args.app

    run(op, component, tenant, project, app)

if __name__ == "__main__":
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv())

    parser = None

    # debug = os.environ["DEBUG"] == "true"
    debug = True
    # debug = True
    # deploy = True
    # deploy = False
    # delete = True

    delete = False
    deploy = True
    # deploy = True
    # delete = False

    component = CONTROL_PLANE_COMPONENT  # Test control plane deployment
    # component = PROJECT_COMPONENT
    tenant = "home"

    # component = SYSTEM_COMPONENT

    sync_pricing = True
    class MArgs:
        def __init__(self):
            self.delete = delete
            self.deploy = deploy
            self.sync_pricing = sync_pricing
            self.component = component
            self.project = "running_shoes"
            self.app = "knowledge_base"
            self.tenant = "home"

    if debug:
        args = MArgs()
    else:
        parser = argparse.ArgumentParser(description="Database Tool for KDCube schema deployments.")
        parser.add_argument(
            "--component", action="store", choices=SUPPORTED_COMPONENTS,
            help=f"Name of the component to deploy {SUPPORTED_COMPONENTS}."
        )
        parser.add_argument(
            "--deploy", action="store_true", help="Deploy the database schema and indices."
        )
        parser.add_argument(
            "--delete", action="store_true", help="Delete the database schema and indices."
        )
        parser.add_argument(
            "--project", help="Name of the project (name of the schema)"
        )
        parser.add_argument(
            "--tenant", help="Name of the tenant"
        )
        parser.add_argument(
            "--app", help="Name of the app (namespace of the deployment scripts)"
        )
        args = parser.parse_args()
    main(args, parser)

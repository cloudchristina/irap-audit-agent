"""
One-shot seeder Lambda — invoke manually to populate mysql.general_log with
demo users and realistic IRAP-relevant activity.

Invoke with:
    aws lambda invoke --function-name irap-db-seeder \
      --payload '{}' --region ap-southeast-2 out.json && cat out.json
"""
import json
import logging
import os
import secrets
import string

import boto3
import pymysql

logger = logging.getLogger()
logger.setLevel(logging.INFO)

secretsmanager = boto3.client("secretsmanager")


def _generate_password(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def get_credentials(secret_arn):
    resp = secretsmanager.get_secret_value(SecretId=secret_arn)
    return json.loads(resp["SecretString"])


def connect(host, port, user, password):
    return pymysql.connect(
        host=host, port=port, user=user, password=password,
        connect_timeout=15,
        ssl={"ca": "/etc/ssl/certs/ca-bundle.crt"},
    )


def setup(conn, demo_passwords):
    with conn.cursor() as cur:
        cur.execute("CREATE DATABASE IF NOT EXISTS irap_audit")
        cur.execute("USE irap_audit")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_accounts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(100) NOT NULL,
                email VARCHAR(255) NOT NULL,
                role VARCHAR(50) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(100),
                action VARCHAR(100) NOT NULL,
                resource VARCHAR(255),
                occurred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sensitive_data (
                id INT AUTO_INCREMENT PRIMARY KEY,
                data_classification VARCHAR(50) DEFAULT 'PROTECTED',
                content TEXT,
                owner VARCHAR(100)
            )
        """)
        cur.execute("DELETE FROM user_accounts")
        cur.execute("""
            INSERT INTO user_accounts (username, email, role) VALUES
            ('alice.smith',  'alice@agency.gov.au',  'analyst'),
            ('bob.jones',    'bob@agency.gov.au',    'administrator'),
            ('carol.white',  'carol@agency.gov.au',  'readonly'),
            ('dave.brown',   'dave@agency.gov.au',   'service_account')
        """)
        cur.execute("DELETE FROM sensitive_data")
        cur.execute("""
            INSERT INTO sensitive_data (data_classification, content, owner) VALUES
            ('PROTECTED', 'Strategic assessment Q1 2026',    'alice.smith'),
            ('PROTECTED', 'Personnel records batch 2026-Q1', 'bob.jones')
        """)
        conn.commit()

        for username, pwd in demo_passwords.items():
            # pymysql does not support parameterised DDL; values are controlled by
            # this function's caller (generated in handler) and never user-supplied.
            cur.execute(f"DROP USER IF EXISTS '{username}'@'%'")
            cur.execute(f"CREATE USER '{username}'@'%' IDENTIFIED BY '{pwd}'")

        cur.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON irap_audit.* TO 'app_user'@'%'")
        cur.execute("GRANT SELECT ON irap_audit.* TO 'reporting_user'@'%'")
        cur.execute("GRANT ALL PRIVILEGES ON irap_audit.* TO 'admin_user'@'%'")
        cur.execute("GRANT SELECT, LOCK TABLES, SHOW VIEW, EVENT, TRIGGER ON *.* TO 'svc_backup'@'%'")
        cur.execute("FLUSH PRIVILEGES")
        conn.commit()


def activity_app(host, port, demo_passwords):
    conn = connect(host, port, "app_user", demo_passwords["app_user"])
    with conn.cursor() as cur:
        cur.execute("USE irap_audit")
        cur.execute("SELECT * FROM user_accounts WHERE role = 'analyst'")
        cur.execute("INSERT INTO audit_events (username, action, resource) VALUES ('alice.smith', 'LOGIN', 'web_portal')")
        cur.execute("SELECT COUNT(*) FROM audit_events")
        cur.execute("UPDATE user_accounts SET email = 'alice.smith@agency.gov.au' WHERE username = 'alice.smith'")
        cur.execute("INSERT INTO audit_events (username, action, resource) VALUES ('alice.smith', 'READ', 'sensitive_data/1')")
        conn.commit()
    conn.close()


def activity_reporting(host, port, demo_passwords):
    conn = connect(host, port, "reporting_user", demo_passwords["reporting_user"])
    with conn.cursor() as cur:
        cur.execute("USE irap_audit")
        cur.execute("SELECT username, role FROM user_accounts")
        cur.execute("SELECT action, COUNT(*) AS cnt FROM audit_events GROUP BY action")
        cur.execute("SELECT * FROM sensitive_data")  # out-of-scope read — flags ISM-1405
        cur.execute("SELECT * FROM user_accounts WHERE role = 'administrator'")
    conn.close()


def activity_admin(host, port, demo_passwords):
    conn = connect(host, port, "admin_user", demo_passwords["admin_user"])
    with conn.cursor() as cur:
        cur.execute("SHOW DATABASES")
        cur.execute("USE irap_audit")
        cur.execute("SELECT * FROM user_accounts")
        cur.execute("SELECT * FROM sensitive_data")
        cur.execute("SELECT * FROM audit_events")
        try:
            cur.execute("SELECT User, Host, account_locked FROM mysql.user")
        except pymysql.err.OperationalError:
            pass
        try:
            cur.execute("SHOW GRANTS FOR 'reporting_user'@'%'")
            cur.execute("SHOW GRANTS FOR 'app_user'@'%'")
        except pymysql.err.OperationalError:
            pass
        try:
            cur.execute("SET GLOBAL general_log = OFF")  # attempt to disable auditing — flags ISM-0585
        except pymysql.err.OperationalError:
            pass
    conn.close()


def activity_backup(host, port, demo_passwords):
    conn = connect(host, port, "svc_backup", demo_passwords["svc_backup"])
    with conn.cursor() as cur:
        cur.execute("SHOW DATABASES")
        cur.execute("USE irap_audit")
        cur.execute("LOCK TABLES user_accounts READ")
        cur.execute("SELECT * FROM user_accounts")
        cur.execute("UNLOCK TABLES")
        cur.execute("LOCK TABLES sensitive_data READ")
        cur.execute("SELECT * FROM sensitive_data")
        cur.execute("UNLOCK TABLES")
    conn.close()


def handler(event, context):
    secret_arn = os.environ["RDS_SECRET_ARN"]
    host = os.environ["RDS_ENDPOINT"]
    port = int(os.environ.get("RDS_PORT", "3306"))

    demo_passwords = {user: _generate_password() for user in ("app_user", "reporting_user", "admin_user", "svc_backup")}

    creds = get_credentials(secret_arn)
    master = connect(host, port, creds["username"], creds["password"])

    logger.info("Setting up schema and demo users...")
    setup(master, demo_passwords)
    master.close()

    logger.info("Generating demo activity...")
    activity_app(host, port, demo_passwords)
    activity_reporting(host, port, demo_passwords)
    activity_admin(host, port, demo_passwords)
    activity_backup(host, port, demo_passwords)

    logger.info("Seeding complete.")
    return {"statusCode": 200, "message": "Seeding complete — mysql.general_log populated"}

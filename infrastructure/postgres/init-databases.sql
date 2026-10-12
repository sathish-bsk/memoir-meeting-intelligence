-- VideoClip — Initialize all databases in shared PostgreSQL instance (dev convenience)
-- Used by root docker-compose.yml on first start.

CREATE DATABASE auth_db;
CREATE DATABASE videoclip_db;
CREATE DATABASE billing_db;
CREATE DATABASE notification_db;
CREATE DATABASE crm_db;

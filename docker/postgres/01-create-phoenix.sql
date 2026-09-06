-- Phoenix has a separate database and role: it cannot read Assistant tables.
CREATE ROLE phoenix LOGIN PASSWORD 'phoenix';
CREATE DATABASE phoenix OWNER phoenix;

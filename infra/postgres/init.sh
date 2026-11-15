#!/usr/bin/env bash
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"  --set=orders_password="$ORDERS_DB_PASSWORD" --set=agent_password="$AGENT_DB_PASSWORD"  --set=benefits_password="$BENEFITS_DB_PASSWORD" --set=control_password="$CONTROL_DB_PASSWORD"  --set=payments_password="$PAYMENTS_DB_PASSWORD" --set=keycloak_password="$KEYCLOAK_DB_PASSWORD" --set=fep_password="$FEP_DB_PASSWORD" --set=card_password="$INST_CARD_DB_PASSWORD" --set=bank_password="$INST_BANK_DB_PASSWORD" --set=kftc_password="$INST_KFTC_DB_PASSWORD" --set=bok_password="$INST_BOK_DB_PASSWORD" <<'SQL'
REVOKE ALL ON DATABASE food FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
CREATE ROLE orders_app LOGIN PASSWORD :'orders_password';
CREATE ROLE agent_app LOGIN PASSWORD :'agent_password';
CREATE ROLE benefits_app LOGIN PASSWORD :'benefits_password';
CREATE ROLE control_app LOGIN PASSWORD :'control_password';
CREATE ROLE payments_app LOGIN PASSWORD :'payments_password';
CREATE ROLE keycloak_app LOGIN PASSWORD :'keycloak_password';
-- FEP망과 가상 금융기관(Mock망): 기관마다 계정·스키마를 나누고 다른 스키마 권한을 주지 않는다.
CREATE ROLE fep_app LOGIN PASSWORD :'fep_password';
CREATE ROLE inst_card_app LOGIN PASSWORD :'card_password';
CREATE ROLE inst_bank_app LOGIN PASSWORD :'bank_password';
CREATE ROLE inst_kftc_app LOGIN PASSWORD :'kftc_password';
CREATE ROLE inst_bok_app LOGIN PASSWORD :'bok_password';
GRANT CONNECT ON DATABASE food TO orders_app,agent_app,benefits_app,control_app,payments_app,fep_app,inst_card_app,inst_bank_app,inst_kftc_app,inst_bok_app;
CREATE SCHEMA orders AUTHORIZATION orders_app;
CREATE SCHEMA agent AUTHORIZATION agent_app;
CREATE SCHEMA benefits AUTHORIZATION benefits_app;
CREATE SCHEMA control AUTHORIZATION control_app;
CREATE SCHEMA payments AUTHORIZATION payments_app;
CREATE SCHEMA fep AUTHORIZATION fep_app;
CREATE SCHEMA inst_card AUTHORIZATION inst_card_app;
CREATE SCHEMA inst_bank AUTHORIZATION inst_bank_app;
CREATE SCHEMA inst_kftc AUTHORIZATION inst_kftc_app;
CREATE SCHEMA inst_bok AUTHORIZATION inst_bok_app;
CREATE DATABASE keycloak OWNER keycloak_app;
REVOKE CONNECT ON DATABASE keycloak FROM PUBLIC;
GRANT CONNECT ON DATABASE keycloak TO keycloak_app;
SQL

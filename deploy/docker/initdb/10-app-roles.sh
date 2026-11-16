#!/bin/sh
# 빈 DB 볼륨을 처음 초기화할 때 한 번만 실행된다 (postgres 이미지 docker-entrypoint-initdb.d).
# 런타임 API는 career_app 멤버(career_api)로 접속하고, migration은 소유자 계정(POSTGRES_USER)으로.
# 비밀번호를 나중에 바꾸려면: docker compose exec db psql -U career -d career_brain \
#   -c "ALTER ROLE career_api PASSWORD '<새 비밀번호>'"  그리고 .env 를 같이 고친다.
set -eu
: "${CAREER_API_DB_PASSWORD:?CAREER_API_DB_PASSWORD is required}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v api_password="$CAREER_API_DB_PASSWORD" <<'SQL'
CREATE ROLE career_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE career_api LOGIN PASSWORD :'api_password' NOSUPERUSER NOBYPASSRLS IN ROLE career_app;
SQL

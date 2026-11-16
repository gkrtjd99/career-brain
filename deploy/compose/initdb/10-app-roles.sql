-- 로컬 개발 전용. 런타임 API는 career_app 멤버(career_api)로 접속하고,
-- migration은 소유자 계정(career)으로 실행한다. 운영에서는 비밀번호를 별도 관리한다.
CREATE ROLE career_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE career_api LOGIN PASSWORD 'career-api-dev-only' NOSUPERUSER NOBYPASSRLS IN ROLE career_app;

-- 005-salesforce-case-thread.sql
-- this table's rows represent the link created between Slack threads
-- that relate to a Salesforce Case. When this link is created, the agent
-- will sync Slack messages with Salesforce comments
create table agent.salesforce_case_thread
(channel_id text not null
, thread_ts text
, case_id text not null
);

create unique index on agent.salesforce_case_thread (channel_id, thread_ts desc);
create index on agent.salesforce_case_thread (case_id);
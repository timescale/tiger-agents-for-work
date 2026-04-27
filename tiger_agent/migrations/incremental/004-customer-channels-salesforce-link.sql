--004-customer-channels-salesforce-link.sql

-----------------------------------------------------------------------
-- to facilitate the creation of salesforce tickets from customer (external)
-- slack channels, we need to link channels to salesforce account ids
create table if not exists agent.customer_channel_salesforce_link
( channel_id text primary key
, salesforce_account_id text
);


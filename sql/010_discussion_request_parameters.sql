ALTER TABLE discussion_notifications
    ADD COLUMN IF NOT EXISTS request_parameters JSON NULL;

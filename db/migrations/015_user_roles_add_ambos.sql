PRAGMA foreign_keys = OFF;

CREATE TABLE users_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  department TEXT,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'dp' CHECK (role IN ('dp', 'company', 'beta', 'alfa', 'ambos')),
  is_active INTEGER NOT NULL DEFAULT 1,
  activated_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO users_new (
  id, name, email, department, password_hash, role, is_active, activated_at, created_at, updated_at
)
SELECT
  id, name, email, department, password_hash, role, is_active, activated_at, created_at, updated_at
FROM users;

DROP TABLE users;
ALTER TABLE users_new RENAME TO users;

CREATE INDEX IF NOT EXISTS idx_users_email_active ON users(email, is_active);

CREATE TABLE user_invites_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  email TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('dp', 'company', 'beta', 'alfa', 'ambos')),
  invite_token TEXT NOT NULL UNIQUE,
  invited_by_user_id INTEGER,
  expires_at TEXT NOT NULL,
  accepted_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (invited_by_user_id) REFERENCES users(id)
);

INSERT INTO user_invites_new (
  id, user_id, email, role, invite_token, invited_by_user_id, expires_at, accepted_at, created_at
)
SELECT
  id, user_id, email, role, invite_token, invited_by_user_id, expires_at, accepted_at, created_at
FROM user_invites;

DROP TABLE user_invites;
ALTER TABLE user_invites_new RENAME TO user_invites;

CREATE UNIQUE INDEX IF NOT EXISTS idx_user_invites_token ON user_invites(invite_token);

PRAGMA foreign_keys = ON;

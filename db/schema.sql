PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('etudiant','professeur','assistant','universite','section')),
  prenom TEXT,
  nom TEXT,
  telephone TEXT,
  universite TEXT,
  filiere TEXT,
  niveau TEXT,
  matricule TEXT,
  date_naissance TEXT,
  departement TEXT,
  grade TEXT,
  service TEXT,
  fonction TEXT,
  num_employe TEXT,
  num_assist TEXT,
  nom_universite TEXT,
  sigle TEXT,
  ville TEXT,
  adresse TEXT,
  nb_etudiants TEXT,
  site_web TEXT,
  responsable TEXT,
  code_uni TEXT,
  cours_classes TEXT DEFAULT '[]',
  payment TEXT,
  inscription_fee TEXT,
  campus_tariffs TEXT,
  failed_login_attempts INTEGER DEFAULT 0,
  locked_until TEXT,
          section_id TEXT,
          nomination TEXT,
          created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_matricule ON users(matricule);
CREATE INDEX IF NOT EXISTS idx_users_telephone ON users(telephone);
CREATE INDEX IF NOT EXISTS idx_users_section ON users(section_id);

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT DEFAULT '',
  source TEXT NOT NULL CHECK (source IN ('professeur','assistant','administration')),
  author TEXT NOT NULL,
  author_id TEXT NOT NULL,
  date TEXT NOT NULL,
  media_category TEXT DEFAULT 'document',
  type TEXT DEFAULT 'PDF',
  size TEXT DEFAULT '—',
  media_url TEXT DEFAULT '',
  media_path TEXT,
  attachments TEXT DEFAULT '[]',
  audience_type TEXT NOT NULL DEFAULT 'ma_classe',
  section_id TEXT,
  section_name TEXT,
  universite TEXT,
  filiere TEXT,
  niveau TEXT,
  course_code TEXT,
  course_name TEXT,
  classe TEXT,
  allow_reactions INTEGER DEFAULT 0,
  reactions TEXT DEFAULT '{"useful":[],"question":[],"thanks":[]}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (author_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_docs_author ON documents(author_id);
CREATE INDEX IF NOT EXISTS idx_docs_audience ON documents(universite, niveau, audience_type);

CREATE TABLE IF NOT EXISTS refresh_tokens (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_refresh_user ON refresh_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_refresh_hash ON refresh_tokens(token_hash);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  code_hash TEXT,
  expires_at TEXT NOT NULL,
  used_at TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reset_user ON password_reset_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_reset_hash ON password_reset_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_reset_code ON password_reset_tokens(code_hash);

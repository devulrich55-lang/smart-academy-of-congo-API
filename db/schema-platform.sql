-- Extension plateforme unifiée Smart Academy of Congo

CREATE TABLE IF NOT EXISTS grades (
  id TEXT PRIMARY KEY,
  student_email TEXT NOT NULL,
  student_matricule TEXT,
  professor_email TEXT NOT NULL,
  universite TEXT NOT NULL,
  filiere TEXT,
  niveau TEXT,
  semester TEXT NOT NULL,
  course_code TEXT NOT NULL,
  course_name TEXT NOT NULL,
  classe TEXT,
  credits INTEGER DEFAULT 3,
  cc REAL NOT NULL,
  exam REAL NOT NULL,
  avg REAL NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('Validé','Rattrapage')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_grades_student ON grades(student_email);
CREATE INDEX IF NOT EXISTS idx_grades_uni_sem ON grades(universite, semester);

CREATE TABLE IF NOT EXISTS library_items (
  id TEXT PRIMARY KEY,
  universite TEXT NOT NULL,
  title TEXT NOT NULL,
  author TEXT,
  category TEXT NOT NULL DEFAULT 'ouvrage',
  description TEXT DEFAULT '',
  file_url TEXT DEFAULT '',
  cover_url TEXT DEFAULT '',
  year INTEGER,
  language TEXT DEFAULT 'fr',
  access_roles TEXT DEFAULT '["etudiant","professeur","assistant"]',
  published INTEGER DEFAULT 1,
  created_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_library_uni ON library_items(universite);

CREATE TABLE IF NOT EXISTS career_posts (
  id TEXT PRIMARY KEY,
  universite TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'campus' CHECK (scope IN ('campus','national')),
  type TEXT NOT NULL CHECK (type IN ('stage','emploi','alternance')),
  title TEXT NOT NULL,
  organization TEXT NOT NULL,
  location TEXT,
  description TEXT NOT NULL,
  requirements TEXT,
  deadline TEXT,
  contact_email TEXT,
  published INTEGER DEFAULT 1,
  created_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_careers_uni ON career_posts(universite, type);

CREATE TABLE IF NOT EXISTS online_courses (
  id TEXT PRIMARY KEY,
  universite TEXT NOT NULL,
  professor_email TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT,
  filiere TEXT,
  niveau TEXT,
  modules TEXT DEFAULT '[]',
  published INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS course_enrollments (
  id TEXT PRIMARY KEY,
  course_id TEXT NOT NULL,
  student_email TEXT NOT NULL,
  progress INTEGER DEFAULT 0,
  enrolled_at TEXT NOT NULL,
  FOREIGN KEY (course_id) REFERENCES online_courses(id) ON DELETE CASCADE,
  UNIQUE(course_id, student_email)
);

CREATE TABLE IF NOT EXISTS social_posts (
  id TEXT PRIMARY KEY,
  universite TEXT NOT NULL,
  author_email TEXT NOT NULL,
  author_name TEXT NOT NULL,
  author_role TEXT NOT NULL,
  content TEXT NOT NULL,
  media_url TEXT,
  audience TEXT DEFAULT 'campus' CHECK (audience IN ('campus','filiere')),
  filiere TEXT,
  likes TEXT DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_social_uni ON social_posts(universite, created_at);

CREATE TABLE IF NOT EXISTS diplomas (
  id TEXT PRIMARY KEY,
  universite TEXT NOT NULL,
  student_email TEXT NOT NULL,
  student_name TEXT NOT NULL,
  matricule TEXT NOT NULL,
  filiere TEXT NOT NULL,
  niveau TEXT NOT NULL,
  diploma_type TEXT NOT NULL DEFAULT 'Licence',
  graduation_year INTEGER NOT NULL,
  diploma_number TEXT NOT NULL UNIQUE,
  verification_code TEXT NOT NULL UNIQUE,
  hash_signature TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'actif' CHECK (status IN ('actif','revoque','suspendu')),
  issued_by TEXT,
  issued_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_diplomas_verify ON diplomas(verification_code);
CREATE INDEX IF NOT EXISTS idx_diplomas_number ON diplomas(diploma_number);

CREATE TABLE IF NOT EXISTS audit_log (
  id TEXT PRIMARY KEY,
  actor_email TEXT,
  actor_role TEXT,
  action TEXT NOT NULL,
  resource TEXT NOT NULL,
  resource_id TEXT,
  universite TEXT,
  ip_hash TEXT,
  meta TEXT DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);

-- Cours en direct (vidéo live)
CREATE TABLE IF NOT EXISTS live_sessions (
  id TEXT PRIMARY KEY,
  universite TEXT NOT NULL,
  professor_email TEXT NOT NULL,
  professor_name TEXT,
  course_code TEXT,
  title TEXT NOT NULL,
  description TEXT,
  room_name TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'scheduled'
    CHECK (status IN ('scheduled','live','ended')),
  filiere TEXT,
  niveau TEXT,
  scheduled_at TEXT,
  started_at TEXT,
  ended_at TEXT,
  recording_url TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS live_attendees (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  student_email TEXT NOT NULL,
  student_name TEXT,
  joined_at TEXT NOT NULL,
  FOREIGN KEY (session_id) REFERENCES live_sessions(id) ON DELETE CASCADE,
  UNIQUE(session_id, student_email)
);

CREATE INDEX IF NOT EXISTS idx_live_uni ON live_sessions(universite, status);
CREATE INDEX IF NOT EXISTS idx_live_prof ON live_sessions(professor_email);

-- Réunions institutionnelles (Chef section ↔ Prof, Doyen ↔ Chefs)
CREATE TABLE IF NOT EXISTS meetings (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL CHECK (type IN ('section_prof','dean_sections')),
  universite TEXT NOT NULL,
  section_id TEXT,
  section_name TEXT,
  section_filiere TEXT,
  title TEXT NOT NULL,
  description TEXT,
  agenda TEXT,
  room_name TEXT NOT NULL UNIQUE,
  host_email TEXT NOT NULL,
  host_name TEXT,
  allowed_emails TEXT DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'scheduled'
    CHECK (status IN ('scheduled','live','ended')),
  scheduled_at TEXT,
  started_at TEXT,
  ended_at TEXT,
  documents TEXT DEFAULT '[]',
  votes TEXT DEFAULT '[]',
  transcript TEXT,
  ai_summary TEXT,
  ai_key_points TEXT DEFAULT '[]',
  ai_translations TEXT DEFAULT '{}',
  stats_snapshot TEXT DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meeting_attendees (
  id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL,
  attendee_email TEXT NOT NULL,
  attendee_name TEXT,
  attendee_role TEXT,
  joined_at TEXT NOT NULL,
  FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE,
  UNIQUE(meeting_id, attendee_email)
);

CREATE INDEX IF NOT EXISTS idx_meetings_uni ON meetings(universite, type, status);
CREATE INDEX IF NOT EXISTS idx_meetings_section ON meetings(section_id);

-- Présence en ligne (heartbeat)
CREATE TABLE IF NOT EXISTS online_presence (
  id TEXT PRIMARY KEY,
  user_email TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL,
  universite TEXT NOT NULL,
  filiere TEXT,
  section_id TEXT,
  classe TEXT,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_presence_uni_updated ON online_presence(universite, updated_at);
CREATE INDEX IF NOT EXISTS idx_presence_section ON online_presence(section_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_presence_classe ON online_presence(classe, updated_at);

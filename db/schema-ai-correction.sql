-- Agent IA — correction automatisée des travaux (SAC)

CREATE TABLE IF NOT EXISTS work_submissions (
  id TEXT PRIMARY KEY,
  student_email TEXT NOT NULL,
  student_name TEXT,
  student_matricule TEXT,
  professor_email TEXT,
  universite TEXT NOT NULL,
  filiere TEXT,
  niveau TEXT,
  course_code TEXT NOT NULL,
  course_name TEXT NOT NULL,
  classe TEXT,
  semester TEXT NOT NULL DEFAULT 's1-2025',
  assignment_title TEXT NOT NULL,
  file_url TEXT,
  file_path TEXT,
  file_type TEXT,
  text_content TEXT,
  status TEXT NOT NULL DEFAULT 'depose'
    CHECK (status IN ('depose','correction_ia','note_provisoire','valide','rejete')),
  provisional_grade REAL,
  final_grade REAL,
  originality_score REAL,
  ai_comments TEXT DEFAULT '[]',
  ai_strengths TEXT DEFAULT '[]',
  ai_weaknesses TEXT DEFAULT '[]',
  rubric_scores TEXT DEFAULT '{}',
  professor_comment TEXT,
  validated_by TEXT,
  validated_at TEXT,
  ai_progress INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_work_student ON work_submissions(student_email);
CREATE INDEX IF NOT EXISTS idx_work_prof ON work_submissions(professor_email);
CREATE INDEX IF NOT EXISTS idx_work_course ON work_submissions(universite, course_code, semester);
CREATE INDEX IF NOT EXISTS idx_work_status ON work_submissions(status);

CREATE TABLE IF NOT EXISTS correction_notifications (
  id TEXT PRIMARY KEY,
  recipient_email TEXT NOT NULL,
  recipient_role TEXT NOT NULL,
  type TEXT NOT NULL,
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  submission_id TEXT,
  universite TEXT,
  read INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notif_recipient ON correction_notifications(recipient_email, read);

-- Copies de correction modèle déposées par le professeur (référence pour l'IA)
CREATE TABLE IF NOT EXISTS correction_references (
  id TEXT PRIMARY KEY,
  professor_email TEXT NOT NULL,
  universite TEXT NOT NULL,
  course_code TEXT NOT NULL,
  course_name TEXT NOT NULL,
  assignment_title TEXT NOT NULL,
  semester TEXT NOT NULL DEFAULT 's1-2025',
  reference_text TEXT,
  criteria_notes TEXT,
  file_url TEXT,
  file_path TEXT,
  file_name TEXT,
  file_type TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ref_prof ON correction_references(professor_email, universite);
CREATE INDEX IF NOT EXISTS idx_ref_course ON correction_references(universite, course_code, assignment_title);

-- Sections faculté & réclamations étudiants

CREATE TABLE IF NOT EXISTS faculty_sections (
  id TEXT PRIMARY KEY,
  university_id TEXT NOT NULL,
  universite TEXT NOT NULL,
  name TEXT NOT NULL,
  filiere TEXT NOT NULL,
  responsable_nom TEXT DEFAULT '',
  email TEXT DEFAULT '',
  telephone TEXT DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_faculty_sections_uni ON faculty_sections(universite);
CREATE INDEX IF NOT EXISTS idx_faculty_sections_univ_id ON faculty_sections(university_id);

CREATE TABLE IF NOT EXISTS reclamations (
  id TEXT PRIMARY KEY,
  section_id TEXT NOT NULL,
  section_name TEXT DEFAULT '',
  student_id TEXT NOT NULL,
  student_email TEXT NOT NULL COLLATE NOCASE,
  student_nom TEXT DEFAULT '',
  matricule TEXT DEFAULT '',
  universite TEXT NOT NULL,
  filiere TEXT DEFAULT '',
  niveau TEXT DEFAULT '',
  sujet TEXT NOT NULL,
  message TEXT NOT NULL,
  categorie TEXT NOT NULL DEFAULT 'autre',
  categorie_detail TEXT DEFAULT '',
  statut TEXT NOT NULL DEFAULT 'ouverte'
    CHECK (statut IN ('ouverte','en_cours','resolue','fermee')),
  reponse TEXT DEFAULT '',
  traite_par TEXT DEFAULT '',
  attachments TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reclamations_section ON reclamations(section_id);
CREATE INDEX IF NOT EXISTS idx_reclamations_student ON reclamations(student_email);
CREATE INDEX IF NOT EXISTS idx_reclamations_uni ON reclamations(universite);
CREATE INDEX IF NOT EXISTS idx_reclamations_statut ON reclamations(statut);

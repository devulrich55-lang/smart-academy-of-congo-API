-- Smart Academy of Congo — schéma MySQL (utf8mb4)
-- Exécuté automatiquement au démarrage de l'API si les tables n'existent pas.

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE IF NOT EXISTS users (
  id VARCHAR(36) PRIMARY KEY,
  email VARCHAR(255) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  role VARCHAR(20) NOT NULL,
  prenom VARCHAR(120) NULL,
  nom VARCHAR(120) NULL,
  telephone VARCHAR(40) NULL,
  universite VARCHAR(80) NULL,
  filiere VARCHAR(200) NULL,
  niveau VARCHAR(40) NULL,
  classe VARCHAR(150) NULL,
  matricule VARCHAR(80) NULL,
  date_naissance VARCHAR(20) NULL,
  departement VARCHAR(120) NULL,
  grade VARCHAR(80) NULL,
  service VARCHAR(120) NULL,
  fonction VARCHAR(120) NULL,
  num_employe VARCHAR(80) NULL,
  num_assist VARCHAR(80) NULL,
  nom_universite VARCHAR(200) NULL,
  sigle VARCHAR(40) NULL,
  ville VARCHAR(120) NULL,
  adresse VARCHAR(300) NULL,
  nb_etudiants VARCHAR(40) NULL,
  site_web VARCHAR(300) NULL,
  responsable VARCHAR(200) NULL,
  code_uni VARCHAR(40) NULL,
  cours_classes JSON NULL,
  payment JSON NULL,
  inscription_fee JSON NULL,
  campus_tariffs JSON NULL,
  failed_login_attempts INT DEFAULT 0,
  locked_until VARCHAR(40) NULL,
  section_id VARCHAR(80) NULL,
  nomination VARCHAR(200) NULL,
  logo_url MEDIUMTEXT NULL,
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL,
  UNIQUE KEY uq_users_email (email),
  CONSTRAINT chk_users_role CHECK (role IN ('etudiant','professeur','assistant','universite','section'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_users_role ON users(role);
CREATE INDEX idx_users_matricule ON users(matricule);
CREATE INDEX idx_users_telephone ON users(telephone);
CREATE INDEX idx_users_section ON users(section_id);
CREATE INDEX idx_users_universite ON users(universite, role);

CREATE TABLE IF NOT EXISTS documents (
  id VARCHAR(36) PRIMARY KEY,
  title VARCHAR(300) NOT NULL,
  description TEXT,
  source VARCHAR(20) NOT NULL,
  author VARCHAR(150) NOT NULL,
  author_id VARCHAR(36) NOT NULL,
  date VARCHAR(20) NOT NULL,
  media_category VARCHAR(40) DEFAULT 'document',
  type VARCHAR(20) DEFAULT 'PDF',
  size VARCHAR(30) DEFAULT '—',
  media_url VARCHAR(2000) DEFAULT '',
  media_path VARCHAR(500) NULL,
  attachments JSON NULL,
  audience_type VARCHAR(20) NOT NULL DEFAULT 'ma_classe',
  section_id VARCHAR(80) NULL,
  section_name VARCHAR(200) NULL,
  universite VARCHAR(80) NULL,
  filiere VARCHAR(200) NULL,
  niveau VARCHAR(40) NULL,
  course_code VARCHAR(30) NULL,
  course_name VARCHAR(200) NULL,
  classe VARCHAR(150) NULL,
  allow_reactions TINYINT(1) DEFAULT 0,
  reactions JSON NULL,
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL,
  CONSTRAINT fk_docs_author FOREIGN KEY (author_id) REFERENCES users(id) ON DELETE CASCADE,
  CONSTRAINT chk_docs_source CHECK (source IN ('professeur','assistant','administration'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_docs_author ON documents(author_id);
CREATE INDEX idx_docs_author_source ON documents(author_id, source);
CREATE INDEX idx_docs_audience ON documents(universite, niveau, audience_type);
CREATE INDEX idx_docs_universite ON documents(universite, source);
CREATE INDEX idx_docs_created ON documents(created_at);

CREATE TABLE IF NOT EXISTS refresh_tokens (
  id VARCHAR(36) PRIMARY KEY,
  user_id VARCHAR(36) NOT NULL,
  token_hash VARCHAR(255) NOT NULL,
  expires_at VARCHAR(40) NOT NULL,
  created_at VARCHAR(40) NOT NULL,
  UNIQUE KEY uq_refresh_hash (token_hash),
  CONSTRAINT fk_refresh_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_refresh_user ON refresh_tokens(user_id);
CREATE INDEX idx_refresh_expires ON refresh_tokens(expires_at);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
  id VARCHAR(36) PRIMARY KEY,
  user_id VARCHAR(36) NOT NULL,
  token_hash VARCHAR(255) NOT NULL,
  code_hash VARCHAR(255) NULL,
  expires_at VARCHAR(40) NOT NULL,
  used_at VARCHAR(40) NULL,
  created_at VARCHAR(40) NOT NULL,
  UNIQUE KEY uq_reset_hash (token_hash),
  CONSTRAINT fk_reset_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_reset_user ON password_reset_tokens(user_id);
CREATE INDEX idx_reset_code ON password_reset_tokens(code_hash);

CREATE TABLE IF NOT EXISTS grades (
  id VARCHAR(36) PRIMARY KEY,
  student_email VARCHAR(255) NOT NULL,
  student_matricule VARCHAR(80) NULL,
  professor_email VARCHAR(255) NOT NULL,
  universite VARCHAR(80) NOT NULL,
  filiere VARCHAR(200) NULL,
  niveau VARCHAR(40) NULL,
  semester VARCHAR(40) NOT NULL,
  course_code VARCHAR(30) NOT NULL,
  course_name VARCHAR(200) NOT NULL,
  classe VARCHAR(150) NULL,
  credits INT DEFAULT 3,
  cc DOUBLE NOT NULL,
  exam DOUBLE NOT NULL,
  avg DOUBLE NOT NULL,
  status VARCHAR(20) NOT NULL,
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL,
  CONSTRAINT chk_grade_status CHECK (status IN ('Validé','Rattrapage'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_grades_student ON grades(student_email);
CREATE INDEX idx_grades_student_uni ON grades(student_email, universite);
CREATE INDEX idx_grades_prof ON grades(professor_email, universite);
CREATE INDEX idx_grades_uni_sem ON grades(universite, semester);
CREATE INDEX idx_grades_campus ON grades(universite, semester);

CREATE TABLE IF NOT EXISTS library_items (
  id VARCHAR(36) PRIMARY KEY,
  universite VARCHAR(80) NOT NULL,
  title VARCHAR(300) NOT NULL,
  author VARCHAR(200) NULL,
  category VARCHAR(40) NOT NULL DEFAULT 'ouvrage',
  description TEXT,
  file_url VARCHAR(2000) DEFAULT '',
  cover_url VARCHAR(2000) DEFAULT '',
  year INT NULL,
  language VARCHAR(10) DEFAULT 'fr',
  access_roles JSON NULL,
  published TINYINT(1) DEFAULT 1,
  created_by VARCHAR(255) NULL,
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_library_uni ON library_items(universite);

CREATE TABLE IF NOT EXISTS career_posts (
  id VARCHAR(36) PRIMARY KEY,
  universite VARCHAR(80) NOT NULL,
  scope VARCHAR(20) NOT NULL DEFAULT 'campus',
  type VARCHAR(20) NOT NULL,
  title VARCHAR(300) NOT NULL,
  organization VARCHAR(200) NOT NULL,
  location VARCHAR(200) NULL,
  description TEXT NOT NULL,
  requirements TEXT NULL,
  deadline VARCHAR(40) NULL,
  contact_email VARCHAR(255) NULL,
  published TINYINT(1) DEFAULT 1,
  created_by VARCHAR(255) NULL,
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL,
  CONSTRAINT chk_career_scope CHECK (scope IN ('campus','national')),
  CONSTRAINT chk_career_type CHECK (type IN ('stage','emploi','alternance'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_careers_uni ON career_posts(universite, type);

CREATE TABLE IF NOT EXISTS online_courses (
  id VARCHAR(36) PRIMARY KEY,
  universite VARCHAR(80) NOT NULL,
  professor_email VARCHAR(255) NOT NULL,
  title VARCHAR(300) NOT NULL,
  description TEXT NULL,
  filiere VARCHAR(200) NULL,
  niveau VARCHAR(40) NULL,
  modules JSON NULL,
  published TINYINT(1) DEFAULT 0,
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS course_enrollments (
  id VARCHAR(36) PRIMARY KEY,
  course_id VARCHAR(36) NOT NULL,
  student_email VARCHAR(255) NOT NULL,
  progress INT DEFAULT 0,
  enrolled_at VARCHAR(40) NOT NULL,
  UNIQUE KEY uq_enrollment (course_id, student_email),
  CONSTRAINT fk_enroll_course FOREIGN KEY (course_id) REFERENCES online_courses(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS social_posts (
  id VARCHAR(36) PRIMARY KEY,
  universite VARCHAR(80) NOT NULL,
  author_email VARCHAR(255) NOT NULL,
  author_name VARCHAR(200) NOT NULL,
  author_role VARCHAR(20) NOT NULL,
  content TEXT NOT NULL,
  media_url VARCHAR(2000) NULL,
  audience VARCHAR(20) DEFAULT 'campus',
  filiere VARCHAR(200) NULL,
  likes JSON NULL,
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL,
  CONSTRAINT chk_social_audience CHECK (audience IN ('campus','filiere'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_social_uni ON social_posts(universite, created_at);

CREATE TABLE IF NOT EXISTS diplomas (
  id VARCHAR(36) PRIMARY KEY,
  universite VARCHAR(80) NOT NULL,
  student_email VARCHAR(255) NOT NULL,
  student_name VARCHAR(200) NOT NULL,
  matricule VARCHAR(80) NOT NULL,
  filiere VARCHAR(200) NOT NULL,
  niveau VARCHAR(40) NOT NULL,
  diploma_type VARCHAR(40) NOT NULL DEFAULT 'Licence',
  graduation_year INT NOT NULL,
  diploma_number VARCHAR(80) NOT NULL,
  verification_code VARCHAR(80) NOT NULL,
  hash_signature VARCHAR(255) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'actif',
  issued_by VARCHAR(255) NULL,
  issued_at VARCHAR(40) NOT NULL,
  created_at VARCHAR(40) NOT NULL,
  UNIQUE KEY uq_diploma_number (diploma_number),
  UNIQUE KEY uq_diploma_verify (verification_code),
  CONSTRAINT chk_diploma_status CHECK (status IN ('actif','revoque','suspendu'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_diplomas_verify ON diplomas(verification_code);
CREATE INDEX idx_diplomas_number ON diplomas(diploma_number);

CREATE TABLE IF NOT EXISTS audit_log (
  id VARCHAR(36) PRIMARY KEY,
  actor_email VARCHAR(255) NULL,
  actor_role VARCHAR(20) NULL,
  action VARCHAR(80) NOT NULL,
  resource VARCHAR(80) NOT NULL,
  resource_id VARCHAR(36) NULL,
  universite VARCHAR(80) NULL,
  ip_hash VARCHAR(128) NULL,
  meta JSON NULL,
  created_at VARCHAR(40) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_audit_created ON audit_log(created_at);

CREATE TABLE IF NOT EXISTS live_sessions (
  id VARCHAR(36) PRIMARY KEY,
  universite VARCHAR(80) NOT NULL,
  professor_email VARCHAR(255) NOT NULL,
  professor_name VARCHAR(200) NULL,
  course_code VARCHAR(30) NULL,
  title VARCHAR(300) NOT NULL,
  description TEXT NULL,
  room_name VARCHAR(120) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'scheduled',
  filiere VARCHAR(200) NULL,
  niveau VARCHAR(40) NULL,
  scheduled_at VARCHAR(40) NULL,
  started_at VARCHAR(40) NULL,
  ended_at VARCHAR(40) NULL,
  recording_url VARCHAR(2000) NULL,
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL,
  UNIQUE KEY uq_live_room (room_name),
  CONSTRAINT chk_live_status CHECK (status IN ('scheduled','live','ended'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS live_attendees (
  id VARCHAR(36) PRIMARY KEY,
  session_id VARCHAR(36) NOT NULL,
  student_email VARCHAR(255) NOT NULL,
  student_name VARCHAR(200) NULL,
  joined_at VARCHAR(40) NOT NULL,
  UNIQUE KEY uq_live_attendee (session_id, student_email),
  CONSTRAINT fk_live_attendee FOREIGN KEY (session_id) REFERENCES live_sessions(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_live_uni ON live_sessions(universite, status);
CREATE INDEX idx_live_prof ON live_sessions(professor_email);

CREATE TABLE IF NOT EXISTS meetings (
  id VARCHAR(36) PRIMARY KEY,
  type VARCHAR(20) NOT NULL,
  universite VARCHAR(80) NOT NULL,
  section_id VARCHAR(80) NULL,
  section_name VARCHAR(200) NULL,
  section_filiere VARCHAR(200) NULL,
  title VARCHAR(300) NOT NULL,
  description TEXT NULL,
  agenda TEXT NULL,
  room_name VARCHAR(120) NOT NULL,
  host_email VARCHAR(255) NOT NULL,
  host_name VARCHAR(200) NULL,
  allowed_emails JSON NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'scheduled',
  scheduled_at VARCHAR(40) NULL,
  started_at VARCHAR(40) NULL,
  ended_at VARCHAR(40) NULL,
  documents JSON NULL,
  votes JSON NULL,
  transcript LONGTEXT NULL,
  ai_summary LONGTEXT NULL,
  ai_key_points JSON NULL,
  ai_translations JSON NULL,
  stats_snapshot JSON NULL,
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL,
  UNIQUE KEY uq_meeting_room (room_name),
  CONSTRAINT chk_meeting_type CHECK (type IN ('section_prof','dean_sections')),
  CONSTRAINT chk_meeting_status CHECK (status IN ('scheduled','live','ended'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS meeting_attendees (
  id VARCHAR(36) PRIMARY KEY,
  meeting_id VARCHAR(36) NOT NULL,
  attendee_email VARCHAR(255) NOT NULL,
  attendee_name VARCHAR(200) NULL,
  attendee_role VARCHAR(20) NULL,
  joined_at VARCHAR(40) NOT NULL,
  UNIQUE KEY uq_meeting_attendee (meeting_id, attendee_email),
  CONSTRAINT fk_meeting_attendee FOREIGN KEY (meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_meetings_uni ON meetings(universite, type, status);
CREATE INDEX idx_meetings_section ON meetings(section_id);

CREATE TABLE IF NOT EXISTS online_presence (
  id VARCHAR(36) PRIMARY KEY,
  user_email VARCHAR(255) NOT NULL,
  role VARCHAR(20) NOT NULL,
  universite VARCHAR(80) NOT NULL,
  filiere VARCHAR(200) NULL,
  section_id VARCHAR(80) NULL,
  classe VARCHAR(150) NULL,
  updated_at VARCHAR(40) NOT NULL,
  UNIQUE KEY uq_presence_email (user_email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_presence_uni_updated ON online_presence(universite, updated_at);
CREATE INDEX idx_presence_section ON online_presence(section_id, updated_at);
CREATE INDEX idx_presence_classe ON online_presence(classe, updated_at);

CREATE TABLE IF NOT EXISTS work_submissions (
  id VARCHAR(36) PRIMARY KEY,
  student_email VARCHAR(255) NOT NULL,
  student_name VARCHAR(200) NULL,
  student_matricule VARCHAR(80) NULL,
  professor_email VARCHAR(255) NULL,
  universite VARCHAR(80) NOT NULL,
  filiere VARCHAR(200) NULL,
  niveau VARCHAR(40) NULL,
  course_code VARCHAR(30) NOT NULL,
  course_name VARCHAR(200) NOT NULL,
  classe VARCHAR(150) NULL,
  semester VARCHAR(40) NOT NULL DEFAULT 's1-2025',
  assignment_title VARCHAR(300) NOT NULL,
  file_url VARCHAR(2000) NULL,
  file_path VARCHAR(500) NULL,
  file_type VARCHAR(40) NULL,
  text_content LONGTEXT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'depose',
  provisional_grade DOUBLE NULL,
  final_grade DOUBLE NULL,
  originality_score DOUBLE NULL,
  ai_comments JSON NULL,
  ai_strengths JSON NULL,
  ai_weaknesses JSON NULL,
  rubric_scores JSON NULL,
  professor_comment TEXT NULL,
  validated_by VARCHAR(255) NULL,
  validated_at VARCHAR(40) NULL,
  ai_progress INT DEFAULT 0,
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL,
  CONSTRAINT chk_work_status CHECK (status IN ('depose','correction_ia','note_provisoire','valide','rejete'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_work_student ON work_submissions(student_email);
CREATE INDEX idx_work_prof ON work_submissions(professor_email);
CREATE INDEX idx_work_course ON work_submissions(universite, course_code, semester);
CREATE INDEX idx_work_status ON work_submissions(status);

CREATE TABLE IF NOT EXISTS correction_notifications (
  id VARCHAR(36) PRIMARY KEY,
  recipient_email VARCHAR(255) NOT NULL,
  recipient_role VARCHAR(20) NOT NULL,
  type VARCHAR(40) NOT NULL,
  title VARCHAR(300) NOT NULL,
  message TEXT NOT NULL,
  submission_id VARCHAR(36) NULL,
  universite VARCHAR(80) NULL,
  `read` TINYINT(1) DEFAULT 0,
  created_at VARCHAR(40) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_notif_recipient ON correction_notifications(recipient_email, `read`);

CREATE TABLE IF NOT EXISTS correction_references (
  id VARCHAR(36) PRIMARY KEY,
  professor_email VARCHAR(255) NOT NULL,
  universite VARCHAR(80) NOT NULL,
  course_code VARCHAR(30) NOT NULL,
  course_name VARCHAR(200) NOT NULL,
  assignment_title VARCHAR(300) NOT NULL,
  semester VARCHAR(40) NOT NULL DEFAULT 's1-2025',
  reference_text LONGTEXT NULL,
  criteria_notes TEXT NULL,
  file_url VARCHAR(2000) NULL,
  file_path VARCHAR(500) NULL,
  file_name VARCHAR(300) NULL,
  file_type VARCHAR(40) NULL,
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_ref_prof ON correction_references(professor_email, universite);
CREATE INDEX idx_ref_course ON correction_references(universite, course_code, assignment_title);

CREATE TABLE IF NOT EXISTS faculty_sections (
  id VARCHAR(36) PRIMARY KEY,
  university_id VARCHAR(36) NOT NULL,
  universite VARCHAR(80) NOT NULL,
  name VARCHAR(200) NOT NULL,
  filiere VARCHAR(200) NOT NULL,
  responsable_nom VARCHAR(200) DEFAULT '',
  email VARCHAR(255) DEFAULT '',
  telephone VARCHAR(40) DEFAULT '',
  active TINYINT(1) NOT NULL DEFAULT 1,
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_faculty_sections_uni ON faculty_sections(universite);
CREATE INDEX idx_faculty_sections_univ_id ON faculty_sections(university_id);

CREATE TABLE IF NOT EXISTS reclamations (
  id VARCHAR(36) PRIMARY KEY,
  section_id VARCHAR(36) NOT NULL,
  section_name VARCHAR(200) DEFAULT '',
  student_id VARCHAR(36) NOT NULL,
  student_email VARCHAR(255) NOT NULL,
  student_nom VARCHAR(200) DEFAULT '',
  matricule VARCHAR(80) DEFAULT '',
  universite VARCHAR(80) NOT NULL,
  filiere VARCHAR(200) DEFAULT '',
  niveau VARCHAR(40) DEFAULT '',
  sujet VARCHAR(300) NOT NULL,
  message TEXT NOT NULL,
  categorie VARCHAR(40) NOT NULL DEFAULT 'autre',
  categorie_detail VARCHAR(200) DEFAULT '',
  statut VARCHAR(20) NOT NULL DEFAULT 'ouverte',
  reponse TEXT DEFAULT '',
  traite_par VARCHAR(255) DEFAULT '',
  attachments JSON NOT NULL,
  created_at VARCHAR(40) NOT NULL,
  updated_at VARCHAR(40) NOT NULL,
  CONSTRAINT chk_rec_statut CHECK (statut IN ('ouverte','en_cours','resolue','fermee'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_reclamations_section ON reclamations(section_id);
CREATE INDEX idx_reclamations_student ON reclamations(student_email);
CREATE INDEX idx_reclamations_uni ON reclamations(universite);
CREATE INDEX idx_reclamations_statut ON reclamations(statut);

-- FinDocDRAG Database Schema
-- See: docs/technical-design-document.md Section 5.3

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- Ingestion tracking
-- ============================================================
CREATE TABLE ingestion_log (
    accession_number VARCHAR(30)  PRIMARY KEY,
    ticker           VARCHAR(10)  NOT NULL,
    company_name     VARCHAR(200) NOT NULL,
    filing_date      DATE         NOT NULL,
    filing_type      VARCHAR(10)  NOT NULL DEFAULT '10-K',
    source_url       TEXT         NOT NULL,
    status           VARCHAR(20)  NOT NULL DEFAULT 'PUBLISHED',
    chunk_count      INTEGER,
    ingested_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at     TIMESTAMPTZ
);

CREATE INDEX idx_ingestion_ticker ON ingestion_log(ticker);
CREATE INDEX idx_ingestion_status ON ingestion_log(status);

-- ============================================================
-- Document chunks with embeddings
-- ============================================================
CREATE TABLE document_chunks (
    chunk_id         VARCHAR(64)  PRIMARY KEY,  -- SHA256 hash
    accession_number VARCHAR(30)  NOT NULL REFERENCES ingestion_log(accession_number),
    ticker           VARCHAR(10)  NOT NULL,
    filing_date      DATE         NOT NULL,
    section_name     VARCHAR(100) NOT NULL,
    chunk_index      INTEGER      NOT NULL,
    chunk_text       TEXT         NOT NULL,
    token_count      INTEGER      NOT NULL,
    embedding        vector(384)  NOT NULL,     -- all-MiniLM-L6-v2 output dimension
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- HNSW index for fast approximate nearest neighbor search
CREATE INDEX idx_chunks_embedding ON document_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 200);

CREATE INDEX idx_chunks_ticker ON document_chunks(ticker);
CREATE INDEX idx_chunks_accession ON document_chunks(accession_number);
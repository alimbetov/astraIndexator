# TZ-17C — Enterprise / Legal / Technical Linguistic Corpus v3

## Status

- **System:** AstraIndexator 1.0
- **Parent:** TZ-17 Testing & Verification
- **Related:** TZ-07, TZ-08, TZ-17A, TZ-17B
- **Purpose:** executable evidence that sentence-boundary logic survives enterprise, legal and technical text rather than only general prose.

## Why v3 exists

A production RAG corpus contains many constructs that resemble punctuation boundaries but are not sentence boundaries:

- legal article/section numbering (`1.2.3`, `Section 4.2.1`, `п. 1.2.3`);
- statutory references (`ст. 15`, `Ziff.`, `l’art.`);
- contract numbers and revisions;
- amounts, currencies, decimal separators and dates;
- initials and corporate abbreviations;
- API paths, package/class names, method calls and identifiers;
- semantic versions, IP-like values, UUIDs and hashes;
- bilingual RU/KK/EN enterprise prose;
- table captions and row references;
- Access Zone codes that must retain leading zeroes.

A splitter that is accurate on ordinary prose but corrupts these constructs is not acceptable for AstraIndexator.

## Corpus contract

Canonical fixture:

`tests/fixtures/linguistic/corpus-v3-enterprise-legal-technical.json`

The corpus is versioned and immutable after merge. Gold boundaries SHALL NOT be changed merely to make current implementation pass.

Each case defines:

- stable case id;
- language/profile hint;
- adversarial category;
- source text;
- exact gold boundary offsets;
- exact expected sentence materialization.

## Required categories

The v3 baseline SHALL cover at least:

- `legal-numbering`;
- `legal-identifiers`;
- `regulatory-reference`;
- `money`;
- `enterprise-abbreviation`;
- `names-initials`;
- `date-time`;
- `bilingual`;
- `access-zone`;
- `api-identifiers`;
- `technical-identifiers`;
- `technical-version`;
- `technical-status`;
- `table-context`.

## Quality gates

For core project languages:

- `kk`: precision = 1.0, recall = 1.0;
- `ru`: precision = 1.0, recall = 1.0;
- `en`: precision = 1.0, recall = 1.0.

Global baseline:

- macro F1 >= 0.98;
- micro F1 >= 0.98;
- per-language F1 >= 0.90;
- per-category F1 >= 0.90.

The stricter core-language gate is intentional because RU/KK/EN are first-class AstraIndexator languages.

## Annotation rules

### Legal numbering

Dots inside hierarchical numbering SHALL NOT create sentence boundaries:

`1.2.3`, `4.2.1`, `ст. 15`, `Section 4.2.1`.

A final punctuation mark after the complete legal sentence remains a real boundary.

### Money and dates

Decimal separators, grouped amounts, dates and time SHALL remain intact. Examples:

`1 250 000,50 ₸`, `USD 1,250,000.50`, `31.12.2026`, `18:30`.

### Identifiers

The following SHALL be protected from internal sentence splitting:

- API paths;
- email addresses;
- URLs;
- Java/package/class names;
- method calls;
- semantic versions;
- UUIDs;
- hashes;
- contract/reference identifiers.

### Bilingual text

A language switch does not itself create a sentence boundary. Punctuation and structure determine the gold boundary.

### Access Zone codes

Codes such as `0000`, `0100`, `0200` are ordinary protected tokens. Leading zeroes SHALL be preserved exactly.

## Governance

When v3 reveals a failure:

1. verify the gold annotation;
2. if the gold is correct, fix the splitter/tailoring/protected-span logic;
3. do not lower quality thresholds as the default response;
4. add a regression case for every bug fixed;
5. version the corpus if an approved linguistic policy changes.

## Relationship to RAG quality

v3 proves linguistic segmentation correctness for enterprise/legal/technical text. It does **not** prove retrieval relevance, embedding quality or BGE-M3 token calibration. Those remain separate TZ-17/TZ-11 quality gates.

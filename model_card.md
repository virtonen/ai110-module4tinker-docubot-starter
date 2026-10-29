# DocuBot Model Card

---

## 1. System Overview

**What is DocuBot trying to do?**

> Answer developer questions about a small codebase using only the files in `docs/`. If the docs don't say, it should say so.

**What inputs does DocuBot take?**

> A question typed in the CLI, the `.md` and `.txt` files in `docs/`, and `GEMINI_API_KEY` (from `.env`) for modes 1 and 3.

**What outputs does DocuBot produce?**

> Mode 1: LLM text. Mode 2: raw doc sections with filenames. Mode 3: a short LLM answer that names the files it used. All modes can return "I do not know".

---

## 2. Retrieval Design

**How does your retrieval system work?**

- **Index:** Each doc is split into sections at markdown headings (code fences are ignored). Every section is tokenized: lowercase, split on letters/digits, stopwords dropped, crude suffix stripping (`ion`, `ing`, `ed`, `s`). The index maps each word to the sections that contain it.
- **Scoring:** Score = number of distinct query words found in the section.
- **Top snippets:** Sections sharing a word with the query are scored. Sections below the evidence threshold are dropped (see section 5). The rest are sorted by score, ties broken by filename then position. Top 3 are returned.

**What tradeoffs did you make?**

> Simple and deterministic over smart. Word counting is easy to debug but has no idea of meaning. The stemmer is crude and sometimes wrong. Heading sections give small snippets, but a section loses its parent heading (see failure case 2).

---

## 3. Use of the LLM (Gemini)

**When does DocuBot call the LLM and when does it not?**

- Naive LLM mode: Always calls the LLM. It does **not** send the docs. `naive_answer_over_full_docs` ignores `all_text` and sends only the question, so the model answers from general knowledge.
- Retrieval only mode: Never calls the LLM.
- RAG mode: Calls the LLM only if retrieval found evidence. No snippets means no call.

**What instructions do you give the LLM to keep it grounded?**

> Use only the snippets. Don't invent functions, endpoints or config values. If not enough evidence, reply exactly "I do not know based on the docs I have." Name the files relied on.

---

## 4. Experiments and Comparisons

Same question, same wording, all three modes.

| Query | Naive LLM | Retrieval only | RAG | Notes |
|------|-----------|----------------|-----|-------|
| Where is the auth token generated? | Harmful-ish: no answer, asks for my stack | Helpful: `Token Generation` section is first | Helpful: `generate_access_token` in `auth_utils.py`, cites AUTH.md | Naive has no docs at all |
| How do I connect to the database? | **Harmful:** invents `DB_HOST`, `DB_PORT`, a JS `connectDB` snippet | Correct but buried: 3 sections, answer in the 3rd | **Helpful:** `DATABASE_URL`, `db.py`, SQLite default, cites DATABASE.md | Docs are Python/SQLite, naive said JS/Postgres style |
| Which endpoint lists all users? | Unhelpful: asks me to paste the docs | Right section is 2nd, behind a generic intro | **Helpful:** `GET /api/users` | |
| What is the default token expiry time? | Guess: "typically 1 hour (3600 s)", hedged | Right section found, answer is one line inside it | **Helpful:** 3600 s via `TOKEN_LIFETIME_SECONDS`, cites AUTH.md | Naive got the number by luck |
| What happens if AUTH_SECRET_KEY is missing? | **Harmful:** says app "will refuse to boot" (docs: token creation fails) | Correct text but spread over 3 sections | Helpful: token creation will fail | |
| Which fields are stored in the users table? | Says it has no schema info | Wrong section (`Token Generation`) | **Fails:** "I do not know" | Answer is in DATABASE.md |
| Is there any mention of payment processing? | Says I forgot to paste docs | "I do not know" | "I do not know" | Correct refusal, no LLM call |

**What patterns did you notice?**

- **Naive looks impressive but is untrustworthy:** "How do I connect to the database?" and "What happens if AUTH_SECRET_KEY is missing?" Confident, formatted, specific, and made up. Nothing ties it to our docs.
- **Retrieval only is accurate but hard to read:** "How do I connect to the database?" The right text is there but you read 3 sections (intro, overview, config) to find `DATABASE_URL`. Sections also include long JSON and tables.
- **RAG combines clarity and evidence:** "How do I connect to the database?" and "What is the default token expiry time?" Short, direct, names the file, and every claim is in the retrieved text.
- **RAG still fails:** "Which fields are stored in the users table?" Refused even though DATABASE.md has the full `users` table. Details below.

---

## 5. Failure Cases and Guardrails

**Failure case 1: Naive invents an answer.**

> Question: How do I connect to the database? Naive mode answered with `DB_HOST`/`DB_PORT`/`DB_USER` variables and a JavaScript `connectDB` snippet. None of that is in the docs (real answer: `DATABASE_URL`, Python `db.py`). It should have said it has no docs.

**Failure case 2: RAG refuses when the answer exists.**

> Question: Which fields are stored in the users table? Retrieval returned only `AUTH.md > Token Generation`, so RAG said "I do not know". The answer is in `DATABASE.md > ### users`. Two causes:
> 1. The `### users` section is cut from its parent `## Tables`, so the word "table" is not in it.
> 2. The stemmer turns "stored" into `stor` but "Stores" into `store`, so they don't match.
>
> It should have returned the `users` section and listed `user_id`, `email`, `password_hash`, `joined_at`. This is a retrieval bug, not an LLM bug. The LLM was never given the right text.

**When should DocuBot say "I do not know based on the docs I have"?**

> - The topic is not in the docs at all (payment processing, "capital of France", "Who is the CEO?").
> - Retrieval finds only weak overlap: fewer than 2 query words in any section.
> - The snippets are about the right area but don't contain the specific fact asked for.

**What guardrails did you implement?**

> - Evidence threshold in `retrieve`: a section counts only if it has at least `MIN_MATCHED_WORDS = 2` distinct query words (or all of them if the query has fewer than 2 useful words). Otherwise no snippets.
> - No snippets means "I do not know" in modes 2 and 3, and RAG makes no LLM call.
> - Stopword list so words like "is", "the", "docs" can't create matches.
> - RAG prompt: only use the snippets, exact refusal sentence, cite files.
> - Top 3 snippets max.

---

## 6. Limitations and Future Improvements

**Current limitations**

1. Word matching only. No meaning. "generated" and "generation" only match because of the crude stemmer, and the stemmer has misses (`stored` vs `stores`).
2. Sections lose their parent heading, so child sections like `### users` don't match words from `## Tables`.
3. All matching words weigh the same. A rare word like `AUTH_SECRET_KEY` counts the same as a common one, so ties are broken by filename order, not relevance.
4. The threshold (2 words) is a guess, tested on 8 sample queries only.
5. Mode 1 sends no docs to the LLM, so it is only a baseline for ungrounded answers.

**Future improvements**

1. Prepend the parent heading to each section before indexing.
2. Weight rare words more (TF-IDF style) instead of plain counts.
3. Tune the refusal threshold on a bigger labeled question set that includes unanswerable questions.

---

## 7. Responsible Use

**Where could this system cause real world harm if used carelessly?**

> Over-trusting mode 1: it sounds sure and invents config values, which could send someone to set the wrong variables or trust a wrong failure behaviour. RAG can also be wrong in the other direction: a false "I do not know" may lead someone to think the docs don't cover something they do. And if the docs are outdated, DocuBot repeats outdated facts with confidence.

**What instructions would you give real developers who want to use DocuBot safely?**

- Use mode 3 (or 2). Treat mode 1 as a demo of what not to do.
- Check the cited file when the answer matters. Retrieval can pick the wrong section.
- "I do not know" means "not found by this retriever", not "not in the docs". Search the docs yourself.
- Keep the docs current. DocuBot is only as right as the files.

---

"""
Core DocuBot class responsible for:
- Loading documents from the docs/ folder
- Building a simple retrieval index (Phase 1)
- Retrieving relevant snippets (Phase 1)
- Supporting retrieval only answers
- Supporting RAG answers when paired with Gemini (Phase 2)
"""

import os
import glob
import re

# Common words that match almost every document and add only noise to scoring
STOPWORDS = {
    "a", "an", "and", "any", "are", "as", "at", "be", "by", "do", "does", "for",
    "from", "how", "i", "in", "is", "it", "of", "on", "or", "that", "the",
    "docs", "there", "these", "this", "to", "what", "when", "where", "which", "with",
}

# Refusal guardrail: a snippet counts as evidence only if it contains at least
# this many distinct query words (or all of them, for very short queries)
MIN_MATCHED_WORDS = 2


class DocuBot:
    def __init__(self, docs_folder="docs", llm_client=None):
        """
        docs_folder: directory containing project documentation files
        llm_client: optional Gemini client for LLM based answers
        """
        self.docs_folder = docs_folder
        self.llm_client = llm_client

        # Load documents into memory
        self.documents = self.load_documents()  # List of (filename, text)

        # Split documents into heading sections and index those, so retrieval
        # returns small snippets instead of whole files
        self.chunks = self.split_into_chunks(self.documents)  # List of (filename, section)

        # Build a retrieval index (implemented in Phase 1)
        self.index = self.build_index(self.chunks)

    # -----------------------------------------------------------
    # Document Loading
    # -----------------------------------------------------------

    def load_documents(self):
        """
        Loads all .md and .txt files inside docs_folder.
        Returns a list of tuples: (filename, text)
        """
        docs = []
        pattern = os.path.join(self.docs_folder, "*.*")
        for path in glob.glob(pattern):
            if path.endswith(".md") or path.endswith(".txt"):
                with open(path, "r", encoding="utf8") as f:
                    text = f.read()
                filename = os.path.basename(path)
                docs.append((filename, text))
        return docs

    # -----------------------------------------------------------
    # Index Construction (Phase 1)
    # -----------------------------------------------------------

    @staticmethod
    def split_into_chunks(documents):
        """
        Splits each (filename, text) document into heading sections.
        A section is a markdown heading line plus the lines under it, up to the
        next heading. Text before the first heading is its own section.
        Lines inside ``` fences are never treated as headings.
        Returns a list of (filename, section_text).
        """
        chunks = []
        for filename, text in documents:
            current = []
            in_fence = False
            for line in text.splitlines():
                if line.lstrip().startswith("```"):
                    in_fence = not in_fence
                if not in_fence and line.startswith("#") and current:
                    chunks.append((filename, "\n".join(current).strip()))
                    current = []
                current.append(line)
            chunks.append((filename, "\n".join(current).strip()))
        return [(f, c) for f, c in chunks if c]

    @staticmethod
    def _stem(word):
        """Crude suffix stripping so 'connect' matches 'connection', 'users' matches 'user'."""
        for suffix in ("ion", "ing", "ed", "s"):
            if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                return word[: -len(suffix)]
        return word

    @classmethod
    def _tokenize(cls, text):
        """Lowercase, split into alphanumeric words, drop stopwords, stem the rest."""
        words = re.findall(r"[a-z0-9]+", text.lower())
        return [cls._stem(w) for w in words if w not in STOPWORDS]

    def build_index(self, chunks):
        """
        TODO (Phase 1):
        Build a tiny inverted index mapping lowercase words to the chunks
        they appear in. Each chunk is a (filename, section_text) pair and is
        identified by its position in the chunks list.

        Example structure:
        {
            "token": [0, 3],
            "database": [12]
        }

        Keep this simple: split on whitespace, lowercase tokens,
        ignore punctuation if needed.
        """
        index = {}
        for chunk_id, (_, text) in enumerate(chunks):
            for token in self._tokenize(text):
                ids = index.setdefault(token, [])
                if chunk_id not in ids:
                    ids.append(chunk_id)
        return index

    # -----------------------------------------------------------
    # Scoring and Retrieval (Phase 1)
    # -----------------------------------------------------------

    def score_document(self, query, text):
        """
        TODO (Phase 1):
        Return a simple relevance score for how well the text matches the query.

        Suggested baseline:
        - Convert query into lowercase words
        - Count how many appear in the text
        - Return the count as the score
        """
        text_tokens = set(self._tokenize(text))
        return sum(1 for word in set(self._tokenize(query)) if word in text_tokens)

    def retrieve(self, query, top_k=3):
        """
        TODO (Phase 1):
        Use the index and scoring function to select top_k relevant document snippets
        (heading sections, not whole files).

        Return a list of (filename, text) sorted by score descending.
        """
        # Only chunks sharing at least one word with the query are candidates
        candidates = set()
        for word in self._tokenize(query):
            candidates.update(self.index.get(word, []))

        scored = []
        for chunk_id in candidates:
            filename, text = self.chunks[chunk_id]
            score = self.score_document(query, text)
            if score > 0:
                scored.append((score, filename, chunk_id))

        # Guardrail: with too little overlap the snippets are coincidence, not
        # evidence, so return nothing and let the answer modes say "I do not know"
        query_words = set(self._tokenize(query))
        min_score = min(MIN_MATCHED_WORDS, len(query_words))
        scored = [item for item in scored if item[0] >= min_score]

        # Highest score first; filename then position break ties deterministically
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        results = [self.chunks[chunk_id] for _, _, chunk_id in scored]
        return results[:top_k]

    # -----------------------------------------------------------
    # Answering Modes
    # -----------------------------------------------------------

    def answer_retrieval_only(self, query, top_k=3):
        """
        Phase 1 retrieval only mode.
        Returns raw snippets and filenames with no LLM involved.
        """
        snippets = self.retrieve(query, top_k=top_k)

        if not snippets:
            return "I do not know based on these docs."

        formatted = []
        for filename, text in snippets:
            formatted.append(f"[{filename}]\n{text}\n")

        return "\n---\n".join(formatted)

    def answer_rag(self, query, top_k=3):
        """
        Phase 2 RAG mode.
        Uses student retrieval to select snippets, then asks Gemini
        to generate an answer using only those snippets.
        """
        if self.llm_client is None:
            raise RuntimeError(
                "RAG mode requires an LLM client. Provide a GeminiClient instance."
            )

        snippets = self.retrieve(query, top_k=top_k)

        if not snippets:
            return "I do not know based on these docs."

        return self.llm_client.answer_from_snippets(query, snippets)

    # -----------------------------------------------------------
    # Bonus Helper: concatenated docs for naive generation mode
    # -----------------------------------------------------------

    def full_corpus_text(self):
        """
        Returns all documents concatenated into a single string.
        This is used in Phase 0 for naive 'generation only' baselines.
        """
        return "\n\n".join(text for _, text in self.documents)

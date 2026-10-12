"""In-code default prompts — extracted from existing call sites.

Phase 1: empty package. Phase 2 will populate one module per Hub prompt name:
    corrector.py, punctuation.py, classification.py, crm_extraction.py,
    intelligence.py, pmf.py, faq.py, memoir.py, p5_summary.py, p5_reduce.py

Each module exports DEFAULT_SYSTEM and DEFAULT_USER_TEMPLATE constants that
get_prompt(_sync) returns when LANGCHAIN_ENABLED=false or when the prompt is
absent from Hub.
"""

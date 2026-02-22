"""
services/knowledge/prompts.py
-------------------------------
Prompt templates for LLM knowledge-generation calls.

Two generation modes are supported:

  CONCISE mode  — one LLM call per cluster, returns a JSON object with all
                  five sections at once. Fast (~1 call), less depth per
                  section. Used by default in generate_knowledge_stories().
                  → COMBINED_STORY_PROMPT

  DETAILED mode — five separate LLM calls per cluster, each prompt focused on
                  a single section. Slower (~5× calls), but each section gets
                  the model's full attention token budget.
                  → EXECUTIVE_SUMMARY_PROMPT, CONTEXT_PROMPT,
                    WHY_IT_MATTERS_PROMPT, IMPLICATIONS_PROMPT,
                    TALKING_POINTS_PROMPT

Switch modes in generator.py by calling either:
  _generate_one_concise(cluster)   — uses COMBINED_STORY_PROMPT
  _generate_one_detailed(cluster)  — uses the five individual prompts

Variables available in all prompts:
  {topic_label}   — the cluster's topic label
  {articles_text} — concatenated article content (title + body per article)
"""

# ════════════════════════════════════════════════════════════════════════════
# CONCISE MODE — single JSON call, all five sections in one round-trip
# ════════════════════════════════════════════════════════════════════════════

# Using double braces {{ }} around the JSON template so .format() doesn't
# interpret the JSON keys as format variables.
COMBINED_STORY_PROMPT = """\
You are an expert intelligence analyst writing a structured daily briefing entry.

Topic: {topic_label}

Source material:
{articles_text}

Produce a JSON object with EXACTLY these five keys. No markdown fences, no commentary, output raw JSON only:
{{
  "executive_summary": "2-3 sentences: what happened factually, key actors, understandable without prior knowledge.",
  "context": "3-5 sentences: historical background, prior events, why this is happening now.",
  "why_it_matters": "3-5 sentences: real-world impact on people/institutions/industries, why a globally informed person should care.",
  "implications": "3-5 sentences: what is likely to happen next, second-order effects, key uncertainties.",
  "talking_points": [
    "One specific, fact-grounded sentence useful as a conversation starter.",
    "One specific, fact-grounded sentence useful as a conversation starter.",
    "One specific, fact-grounded sentence useful as a conversation starter.",
    "One specific, fact-grounded sentence useful as a conversation starter.",
    "One specific, fact-grounded sentence useful as a conversation starter."
  ]
}}"""


# ════════════════════════════════════════════════════════════════════════════
# DETAILED MODE — five focused prompts, one per section
# Each gives the model its full token budget for a single section, producing
# richer, more analytically thorough output at the cost of 5× LLM calls.
# ════════════════════════════════════════════════════════════════════════════

EXECUTIVE_SUMMARY_PROMPT = """\
You are an expert intelligence analyst writing for a sophisticated daily briefing.

Topic: {topic_label}

Source material:
{articles_text}

Write an EXECUTIVE SUMMARY of this story in 2–3 sentences. It should:
- State what happened clearly and factually
- Identify the key actors involved
- Be understandable without any prior knowledge

Output ONLY the summary paragraph. No headers, no bullet points."""


CONTEXT_PROMPT = """\
You are an expert intelligence analyst writing for a sophisticated daily briefing.

Topic: {topic_label}

Source material:
{articles_text}

Write a CONTEXT section (3–5 sentences) that explains:
- Historical or prior events that led to this situation
- Background the reader needs to understand why this is happening now
- Relevant relationships, agreements, policies, or trends

Output ONLY the context paragraph. No headers, no bullet points."""


WHY_IT_MATTERS_PROMPT = """\
You are an expert intelligence analyst writing for a sophisticated daily briefing.

Topic: {topic_label}

Source material:
{articles_text}

Write a WHY IT MATTERS section (3–5 sentences) that explains:
- The real-world impact on people, institutions, or industries
- Which domains this touches (politics, technology, economy, public health, etc.)
- Why a globally informed person should care about this story

Output ONLY the paragraph. No headers, no bullet points."""


IMPLICATIONS_PROMPT = """\
You are an expert intelligence analyst writing for a sophisticated daily briefing.

Topic: {topic_label}

Source material:
{articles_text}

Write an IMPLICATIONS section (3–5 sentences) that explains:
- What is likely to happen next based on current trajectories
- Potential second-order effects (economic, political, social, technological)
- Disagreements or uncertainties that make the outcome hard to predict

Output ONLY the paragraph. No headers, no bullet points."""


TALKING_POINTS_PROMPT = """\
You are an expert intelligence analyst writing for a sophisticated daily briefing.

Topic: {topic_label}

Source material:
{articles_text}

Generate 5 TALKING POINTS that would make the reader sound well-informed in a conversation.
Each talking point should be:
- One sentence, specific and substantive
- Grounded in facts from the source material
- Useful as a conversation starter or response

Output ONLY a numbered list (1. ... 2. ... etc.), nothing else."""


# ════════════════════════════════════════════════════════════════════════════
# Shared helper
# ════════════════════════════════════════════════════════════════════════════

def build_articles_text(articles, max_chars_per_article: int = 800, max_articles: int = 4) -> str:
    """
    Concatenate article titles and content into a single block for the prompt.
    Caps each article at max_chars_per_article to keep prompts manageable.
    Only the top max_articles articles are used, preferring the most content-rich
    ones — additional articles in a cluster are near-duplicates with the same facts.

    Tip: for DETAILED mode you can pass max_chars_per_article=1500 and
    max_articles=6 to give each focused prompt more material to work with.
    """
    # Prefer articles with the most content (richest signal first)
    selected = sorted(articles, key=lambda a: len(a.content or ""), reverse=True)[:max_articles]
    parts = []
    for i, article in enumerate(selected, 1):
        body = article.content[:max_chars_per_article] if article.content else ""
        parts.append(f"[Article {i} — {article.source}]\nTitle: {article.title}\n{body}")
    return "\n\n".join(parts)

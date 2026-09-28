"""Web research with Tavily, so replies quote real facts instead of guesses.

Nemotron Nano decides per email whether any lookups are worth doing:
* questions the sender asked that need a correct, current answer
  (tree-removal permits in their town, whether an address is in the service area)
* who the sender is, when they write from a business domain
Tavily runs the searches and the notes go to the writer model.
"""
from __future__ import annotations

import logging

from tavily import TavilyClient

import llm
import settings

log = logging.getLogger("frontdesk.research")

FREE_MAIL = {
    "gmail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com", "yahoo.ca",
    "icloud.com", "me.com", "aol.com", "protonmail.com", "proton.me", "rogers.com",
    "bell.net", "sympatico.ca", "cogeco.ca", "shaw.ca", "telus.net",
}

PLANNER_SYSTEM = """You plan web research for the front desk of a local service business in {region}.

Business profile:
{knowledge}

Search only when the result would change what the reply says. There are exactly two good reasons:
1. The sender asked a question about the rules in their own town, such as whether a permit is needed to remove a tree. Search for the municipality's official rules and put the town, the province and the country in the query. Only search for questions the sender actually asked.
2. The sender writes from a company domain. Do one search to learn what that company does, using the company name and domain.

Never search for anything about this business itself, its prices, discounts, services, availability, scheduling or invoices (the profile and the owner handle those), nor for generic industry guides, nor for private individuals. Most emails need no search at all.

Reply with ONLY a JSON object: {{"queries": [{{"query": "...", "reason": "..."}}]}} with at most {max_queries} queries, or an empty list."""


def sender_domain(from_header: str) -> str:
    address = from_header.split("<")[-1].rstrip(">").strip()
    return address.split("@")[-1].lower() if "@" in address else ""


def plan_queries(em: dict, triage: dict, knowledge: str, max_queries: int = 2) -> list[dict]:
    domain = sender_domain(em["from"])
    if not domain or domain in FREE_MAIL:
        domain_note = "personal webmail, so no company lookup"
    else:
        domain_note = "a business domain"
    user = (
        f"Sender domain: {domain or 'unknown'} ({domain_note})\n"
        f"Subject: {em['subject']}\n"
        f"Intake notes: {triage.get('intake')}\n"
        f"Questions asked: {triage.get('questions')}\n\n"
        f"Email body:\n{em['body'][:2500]}"
    )
    result = llm.chat_json(
        settings.TRIAGE_MODEL,
        PLANNER_SYSTEM.format(knowledge=knowledge, max_queries=max_queries, region=settings.BUSINESS_REGION),
        user,
        temperature=0.1,
        max_tokens=600,
        thinking=False,
    )
    queries = [q for q in result.get("queries", []) if isinstance(q, dict) and q.get("query")]
    return queries[:max_queries]


def run_search(query: str, max_results: int = 3) -> dict:
    client = TavilyClient(api_key=settings.TAVILY_API_KEY)
    kwargs = dict(max_results=max_results, include_answer="basic", search_depth="basic")
    if settings.SEARCH_COUNTRY:
        try:
            return client.search(query, country=settings.SEARCH_COUNTRY, **kwargs)
        except Exception as exc:
            log.warning("Search with country=%r failed (%s); retrying without it", settings.SEARCH_COUNTRY, exc)
    return client.search(query, **kwargs)


def research(em: dict, triage: dict, knowledge: str) -> str | None:
    """Return research notes for the writer, or None when nothing was needed."""
    domain = sender_domain(em["from"])
    has_company_domain = bool(domain) and domain not in FREE_MAIL
    if not triage.get("questions") and not has_company_domain:
        return None  # nothing to look up: no questions asked and a personal sender

    try:
        queries = plan_queries(em, triage, knowledge)
    except Exception as exc:
        log.warning("Research planning failed, replying without research: %s", exc)
        return None
    if not queries:
        return None

    notes = []
    for q in queries:
        try:
            result = run_search(q["query"])
        except Exception as exc:
            log.warning("Tavily search failed for %r: %s", q["query"], exc)
            continue
        block = [f"### Search: {q['query']}", f"Why: {q.get('reason', '')}"]
        if result.get("answer"):
            block.append(f"Summary: {result['answer']}")
        for hit in result.get("results", [])[:3]:
            snippet = (hit.get("content") or "").replace("\n", " ")[:300]
            block.append(f"- {hit.get('title', '')} ({hit.get('url', '')}): {snippet}")
        notes.append("\n".join(block))
    return "\n\n".join(notes) or None

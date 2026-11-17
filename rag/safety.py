"""Input safety checks, run on every message before planning (no LLM, no retrieval).

What it blocks, in this order:
  1. too_long   - over MAX_MESSAGE_CHARS (a long pasted recipe still fits).
  2. self_harm  - a supportive reply with Indian helplines, never a shopping answer.
  3. injection  - attempts to override instructions, change the bot's role or read its prompt.
  4. unsafe     - weapons/explosives, poisoning someone, illegal drugs, sexual content about minors.
  5. abusive    - slurs and strong insults aimed at the bot or people.
  6. medical    - asks for treatment of a symptom or illness ("what should I take for a fever").

The lists are kept narrow on purpose: BigBasket sells knives, "germ kill" cleaners, rat poison and
"cough & cold" balms, so a word alone ("kill", "knife", "poison", "cough") is never enough; each
rule needs the harmful *intent* (kill + a person, poison + someone, take + a symptom).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_MESSAGE_CHARS = 4000

SELF_HARM_REPLY = (
    "I'm really sorry you're feeling this way. I'm only a shopping assistant, but you don't have to go "
    "through this alone. In India you can call **Tele-MANAS on 14416** (free, 24×7) or **KIRAN on "
    "1800-599-0019**. If you're in immediate danger, call **112**."
)


@dataclass(frozen=True)
class SafetyVerdict:
    trigger: str   # too_long | self_harm | injection | unsafe | abusive | medical
    reason: str
    reply: str | None = None   # a full reply that replaces the standard fallback message


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts), re.I)


_SELF_HARM = _rx(
    r"\b(kill|hurt|harm|cut)(ing)? myself\b", r"\bsuicid\w*", r"\bend (my|it all|my own) li(fe|ves)\b",
    r"\b(want|wanna|going) to die\b", r"\bself[- ]?harm\w*", r"\bno reason to live\b",
    r"\bkhudkushi\b", r"\bmar(na)? jaana? (chahta|chahti)\b",
)

_INJECTION = _rx(
    r"\b(ignore|disregard|forget|override|bypass|skip)\b.{0,40}\b(previous|prior|above|earlier|all|your|the|system)\b"
    r".{0,20}\b(instructions?|prompts?|rules?|guidelines?|messages?|context|guardrails?)\b",
    r"\b(system|developer|hidden|initial|original) (prompt|message|instructions?)\b",
    r"\b(reveal|show|print|repeat|leak|tell me|what (is|are)) (me )?(your|the) (system |hidden |initial )?"
    r"(prompt|instructions|rules)\b",
    r"\byou are (now|no longer)\b", r"\b(act|behave|respond|roleplay|role-play) as (a|an|if)\b",
    r"\bpretend (to be|you('| a)re|that you)\b", r"\b(jailbreak|DAN mode|developer mode|god mode)\b",
    r"\bnew (instructions|rules|persona)\b", r"<\s*/?\s*(system|assistant|instructions?)\s*>",
    r"\[\s*(system|INST)\s*\]", r"\bfrom now on,? (you|always|only|never)\b",
    r"\b(say|tell (me|everyone)|claim|announce) (that )?(everything|all (items|products))? ?(is|are) free\b",
)

_UNSAFE = _rx(
    r"\b(make|build|assemble|how to (make|build))\b.{0,30}\b(bomb|explosives?|ied|napalm|molotov|gun|firearm|weapon)\b",
    r"\b(poison|kill|murder|hurt|stab|attack)\b.{0,25}\b(someone|somebody|a person|people|my (wife|husband|"
    r"boss|neighbou?r|friend|family|father|mother|brother|sister|child|kid)|him|her|them)\b",
    r"\b(buy|get|make|cook|grow|sell)\b.{0,20}\b(cocaine|heroin|meth|mdma|lsd|charas|crystal meth)\b",
    r"\b(child|minor|underage)\b.{0,20}\b(sex|nude|porn)\w*",
)

_ABUSIVE = _rx(
    r"\b(fuck(ing|er)?|motherfucker|bitch|bastard|asshole|cunt|madarchod|behenchod|bhenchod|chutiya|"
    r"bhosdi\w*|randi|gaandu)\b",
)

# Asking what to take / use for a symptom or illness: medical advice, not a product question.
_SYMPTOMS = (r"(head ?ache|migraine|fever|vomit\w*|nausea|diarrh?o?ea|loose motions?|constipation|"
             r"infection|rash(es)?|wound|injury|fracture|burns?|period pain|cramps|anxiety|depression|"
             r"insomnia|can'?t sleep|chest pain|toothache|sore throat|acidity|gas trouble|piles|"
             r"high bp|low bp|sugar (level|problem)|thyroid|asthma|covid|dengue|malaria|jaundice|"
             r"(stomach|body|back|joint|knee|ear|tooth) ?(pain|ache)|sar ?dard|pet dard|bukhar|khansi)")
_MEDICAL = _rx(
    rf"\b(what|which|should i|can i|do i|how (much|many)|suggest|recommend|kya)\b.{{0,40}}"
    rf"\b(take|eat|drink|use|apply|give|lena|lu|khau)\b.{{0,40}}\b{_SYMPTOMS}\b",
    rf"\b(medicine|tablet|pill|antibiotic|painkiller|dawai|dawa|syrup|cure|remedy|treatment)\b.{{0,30}}"
    rf"\b(for|to (cure|treat|stop|fix)|ke liye)\b.{{0,25}}\b{_SYMPTOMS}\b",
    rf"\b(i|my (son|daughter|kid|child|baby|mother|father|wife|husband)) (have|has|got|am having|is having)"
    rf"\b.{{0,25}}\b{_SYMPTOMS}\b.{{0,40}}\b(what|which|help|should|suggest|recommend)\b",
    rf"\b(how (do i|to) (cure|treat|get rid of|stop))\b.{{0,25}}\b{_SYMPTOMS}\b",
    # symptom first (Hinglish word order): "pet dard ke liye kya lena chahiye"
    rf"\b{_SYMPTOMS}\b.{{0,20}}\b(ke liye|for)\b.{{0,20}}\b(kya|what|which)\b.{{0,20}}"
    rf"\b(lena|lu|loon|khau|khana|piyu|take|eat|drink|use)\b",
)


def check_input(message: str) -> SafetyVerdict | None:
    """The first rule the message breaks, or None if it is safe to plan and answer."""
    text = message.strip()
    if len(text) > MAX_MESSAGE_CHARS:
        return SafetyVerdict("too_long", f"the message is over {MAX_MESSAGE_CHARS:,} characters; "
                             "send a shorter question or just the recipe's ingredient list")
    if _SELF_HARM.search(text):
        return SafetyVerdict("self_harm", "the message mentions self-harm", reply=SELF_HARM_REPLY)
    if _INJECTION.search(text):
        return SafetyVerdict("injection", "the message tries to change my instructions; I only answer "
                             "questions about BigBasket products, from the catalogue data")
    if _UNSAFE.search(text):
        return SafetyVerdict("unsafe", "I can't help with anything that could hurt people")
    if _ABUSIVE.search(text):
        return SafetyVerdict("abusive", "let's keep it friendly; ask me about a dish, a task or a product")
    if _MEDICAL.search(text):
        return SafetyVerdict("medical", "I can't give medical advice: please ask a doctor or pharmacist about "
                             "symptoms and treatment")
    return None

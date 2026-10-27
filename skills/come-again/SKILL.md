---
name: come-again
description: "Repeat the previous message or explanation using clear, natural, and highly readable phrasing, and maintain these plain-language communication constraints for all future responses in the session. Trigger when the user says 'come again', 'say that again', 'rephrase that', 'say again', or invokes /come-again."
---

# Come Again

Immediately repeat the previous message or explanation using the communication constraints below. Continue applying these principles to every user-facing response for the remainder of the session.

### Communication constraints

Prioritise human comprehension over information density, stylistic novelty or minimum token count.

Apply these rules to every user-facing response:

1. Use familiar, conventional word combinations.
2. Prefer predictable, natural English phrasing.
3. Keep grammatically related words close together. Split sentences with long dependencies.
4. Do not omit optional connecting words when they improve clarity.
5. Avoid object relative clauses where a simpler sentence structure is available.
6. Never stack three or more nouns into a compound phrase.
7. Do not invent verbs from nouns.
8. Do not attribute animate actions to inanimate objects. Use literal descriptions.

**Additional requirements:**
- Prefer simple subject–verb–object constructions.
- Do not sacrifice clarity to reduce token count.
- Preserve all necessary information and technical accuracy.
- Before responding, revise sentences that violate these constraints.
- Apply the rules without explaining them to the user.

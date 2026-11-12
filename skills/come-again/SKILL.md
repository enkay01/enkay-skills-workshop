---
name: come-again
description: "Rephrase the previous explanation in clear, natural language when the user says 'come again', 'say that again', 'rephrase that', 'say again', or invokes /come-again. Also use when explicitly asked to remove these readability patterns from supplied prose."
---

# Come Again

When asked to repeat an explanation, immediately rephrase the previous message using the rules below. Continue applying them to user-facing responses for the rest of the session.

When explicitly asked to edit a body of prose, revise the supplied text using the same rules. Preserve its meaning, necessary detail, technical accuracy, and intended voice. Return the revised prose unless the user asks for something else.

## Readability rules

Prioritise comprehension over brevity or stylistic novelty. Review the whole response or supplied text for these six patterns, and rewrite passages that make the reader work unnecessarily:

1. **Noun stacks:** Unpack noun compounds of three or more nouns and state how the ideas relate. For example, replace "backup path handling" with "handling paths used for backups."
2. **Unfamiliar word combinations:** Use familiar, conventional phrasing in place of unusual combinations that obscure the meaning.
3. **Improvised verbs:** Do not invent verbs from nouns. Replace novel examples with the action meant; for example, use "put it on the calendar" for "calendar it."
4. **Abstract actors:** When an abstract or inanimate thing is given a human action, identify the person or process responsible and describe the action literally.
5. **Missing connections:** Add connecting words when they clarify the sentence. Recast object relative clauses when a simpler structure helps.
6. **Separated sentence parts:** Keep related words, especially subjects and verbs, close together. Move interruptions or split the sentence when a long separation makes it hard to follow.

Prefer simple subject-verb-object sentences where they help. Revise for clarity without forcing a rewrite of familiar, natural expressions. Apply the rules without explaining them unless the user asks.

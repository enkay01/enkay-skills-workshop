---
name: google-developer-docs-style
description: "Draft, edit, and review developer documentation using Google’s technical-writing style: clear task-focused structure, direct and inclusive language, accessible formatting, safe examples, precise code and UI references, and descriptive links. Use for tutorials, procedures, conceptual guides, API or CLI references, code comments, Markdown/HTML documentation, UI help text, or documentation review; preserve project-specific conventions and exact technical text."
---

# Google developer documentation style

Apply this skill when documentation needs to be clearer, more consistent, more accessible, or easier to translate. Treat the Google guide as a context-aware editorial standard, not as a blind forbidden-word list.

## Establish the editorial contract

Before editing, identify:

- the reader, their goal, technical level, and prerequisites;
- the artifact type: procedure, tutorial, conceptual explanation, reference, API reference, CLI guide, code comment, UI copy, release note, or other content;
- the project, product, localization, and language-specific conventions;
- the required output: draft, rewrite, review findings, or a minimal patch.

Resolve conflicts in this order:

1. User requirements, project and product conventions, and localization rules.
2. Exact technical contracts: code, commands, API names, file paths, URLs, output, UI labels, trademarks, quotations, and legal text.
3. This Google-style guidance.
4. Dictionary and general editorial references.

Preserve meaning and technical truth. If a rule conflicts with clarity, accessibility, the product interface, or an exact contract, keep the accurate form and explain the exception. Prefer a consistent exception over an isolated “correction.”

## Use the editing workflow

1. State or infer the audience, goal, scope, prerequisites, and expected result. Ask only when a risky assumption cannot be resolved from the material or project context.
2. Classify the content so the relevant rules apply. Do not apply API-reference or UI rules to ordinary prose.
3. Mark protected spans before changing text: code, commands, output, API identifiers, flags, file paths, URLs, exact UI labels, trademarks, quoted text, and contractual language.
4. Put the reader’s main point and next useful action early. Keep each paragraph focused on one idea.
5. Make structure carry meaning: use descriptive headings, task-oriented sections, appropriate lists or tables, and executable procedures.
6. Revise prose for clarity, directness, inclusion, accessibility, localization, and consistent terminology.
7. Check formatting, links, examples, code samples, UI references, images, tables, notices, and modality.
8. Re-read as the target reader. Verify that the result is accurate, runnable where applicable, and understandable without relying on color, position, images, sound, or a mouse.

When reviewing instead of rewriting, report only material findings. For each finding, include:

```text
rule
applies_if
protected_or_excluded_spans
severity: must | should | review
autofix: safe | unsafe
rationale
suggested_revision
source
```

Use `must` for correctness, accessibility, privacy, or a stated project requirement; `should` for strong style improvements; and `review` when context or an exact contract makes the change uncertain.

## Write for reader success

- Address the reader as **you**. Use the imperative for instructions and third person for software or the software’s end user.
- Prefer active voice, present tense, concrete subjects, and subject–verb–object order. Use passive voice when the actor is irrelevant or the object deserves emphasis.
- Put conditions, context, location, or purpose before the action: “If the file exists, delete it.”
- Begin each procedural step with an imperative. Put the action before its result or justification.
- Prefer short, literal, unambiguous sentences and one idea per paragraph. A sentence near 26 words or fewer is a useful target, not a hard limit.
- Use helper words such as *a*, *the*, *that*, *then*, and *of* when they improve comprehension or translation.
- State essential context on the current page. Link for depth, not as a substitute for a needed definition or instruction.
- Avoid slang, idioms, humor, clichés, metaphors, cultural references, vague claims, double negatives, and unnecessary “please.”
- Define unfamiliar acronyms and necessary jargon at first use. Use the project’s terminology and word list consistently.
- In prescriptive text, distinguish requirements (`must`), ability or optional action (`can`), possibility (`might`), and recommendations (`We recommend`). Avoid ambiguous `should`.
- Avoid unstable terms such as “currently,” “new,” “latest,” and “soon” in timeless product documentation unless anchored to a version or date. Respect the artifact’s purpose for release notes and dated announcements.

## Structure documents and procedures

- Give the page one clear primary purpose and a descriptive title.
- Use one unique H1, logical heading levels, sentence case, and descriptive headings. Do not skip levels, number headings merely to show sequence, put links in headings, or end headings with periods.
- Use a base-form verb for task headings (`Create an instance`) and a noun phrase for conceptual headings (`Instance migration`). Prefix optional sections with `Optional:`.
- Use numbered lists when order matters; bullets for unordered collections; description lists for term–description pairs; and tables only for genuinely multidimensional data.
- Introduce lists, tables, images, and code with enough surrounding context. Keep list items parallel and consistent.
- Avoid layout tables, tables for long one-dimensional lists, and tables inside numbered procedures. Prefer prose or a list when it is clearer.
- For procedures, state prerequisites and the goal, then provide the shortest accessible path. Put context or conditions before each action, keep one main action per step, and state the result afterward.
- Separate substantially different methods into clear alternatives or pages. Do not duplicate a procedure; link to the authoritative version.
- Do not hide required actions or prerequisites in a note. Use `Note`, `Caution`, and `Warning` sparingly and according to the risk.

## Format technical content precisely

- Use code formatting for literal input and technical entities: commands, flags, filenames, paths, identifiers, types, methods, parameters, attributes, values, environment variables, IP addresses, ports, status codes, and command output.
- Qualify code with a noun: “the `config.yaml` file,” “the `--port` flag,” or “a `POST` request.” Do not pluralize, possess, or verbify code identifiers.
- Preserve exact spelling and capitalization for code, APIs, commands, output, paths, URLs, and UI labels. Never style-edit a protected span just to make it look consistent with prose.
- Use code blocks for samples and follow the project or language style guide. Prefer runnable, secure, tested, concise samples with descriptive names.
- Explain prerequisites, dependencies, how to run a sample, and expected output. Explain a difficult concept before the sample and increase complexity gradually.
- Use descriptive uppercase placeholders such as `PROJECT_ID` or `RESOURCE_NAME`; explain each placeholder on first use. Use reserved domains, fictional data, and reserved IP ranges. Never expose real personal data.
- Show omitted code with a language-appropriate comment, not `...` or `…`. Do not make incomplete samples click-to-copy.
- Keep lines near 80 characters when practical, subject to the project’s formatter and language conventions.
- For API references, document every public symbol and its purpose, parameters, return values, exceptions, prerequisites, dependencies, related APIs, defaults, and deprecations. Start with an informative summary; describe boolean results as “True if …; false otherwise.”

## Handle links, UI, and errors

- Use short, unique, descriptive link text that makes sense out of context. Put important words first. Avoid “click here,” “this document,” duplicate links, and bare URLs as ordinary link text.
- Provide context before a link and explain downloads, email links, new tabs, and same-page jumps when the behavior is unexpected. Keep punctuation outside link markup.
- Focus UI instructions on the user’s goal. When naming a visible label, reproduce the actual label in bold: “Click **Save**.”
- Do not use UI labels as ordinary verbs or nouns: “In the **Name** field, enter a name.” Use precise element names such as *field*, *dialog*, *pane*, *menu*, *page*, and *tab*.
- Use `File > New > Document` for menu paths, with accessible text for the separators where the renderer supports it. Use `select` or `clear` for checkboxes and `turn on` or `turn off` for toggles.
- Refer to controls by labels, tooltips, or accessible names, not by position or appearance. Avoid “above,” “below,” “left,” “right,” and color-only directions.
- Write errors so the reader knows what went wrong and how to fix it. Identify the invalid input or cause, state the constraint, give the corrective action, and include an example or link when useful. Keep the tone positive and non-blaming.

## Make content inclusive, accessible, and global

- Use respectful, person-centered language. Avoid unnecessary gendered, ableist, violent, graphic, or dehumanizing language. If an established term is required for searchability or compatibility, explain it once and use the preferred term in prose.
- Use diverse, fictional names, identities, ages, and locations. Avoid stereotypes, culturally specific holidays, sports, seasons, and idioms.
- Use semantic HTML, meaningful heading order, labeled form fields, keyboard-accessible controls, descriptive link text, and useful alt text. Provide captions, transcripts, or equivalent descriptions for audio and video.
- Put all new information in text, not only in an image, screenshot, color, icon, position, size, or sound. Use empty alt text for decorative or redundant images and surrounding descriptions for complex diagrams.
- Prefer clear US English that translates cleanly: short sentences, consistent terms, unambiguous pronouns, complete helper words, and simple words such as “use” instead of “utilize.”
- Use unambiguous dates such as `January 19, 2017` or `2017-04-15`. Identify currencies, time zones, and units; distinguish decimal units such as `GB` from binary units such as `GiB`.

## Finish with a targeted checklist

Check the items relevant to the artifact:

- Can the intended reader identify the goal, scope, prerequisites, and expected result?
- Is the main point early, and does each instruction tell the reader what to do and why or what to expect?
- Are prose, headings, lists, links, terminology, and punctuation consistent?
- Are protected spans unchanged and technically accurate?
- Are examples fictional, privacy-safe, meaningful, and inclusive?
- Are code samples runnable, secure, explained, and consistent with project conventions?
- Do errors explain the cause or invalid input and the fix?
- Are UI labels exact, accessible, and free of directional-only references?
- Can the content be understood without color, images, sound, or a pointer device?
- Is the wording timeless and translatable where the artifact requires it?

If any answer is uncertain because of project context, technical truth, or a protected span, flag it as `review` instead of silently changing it.

## Consult the source map

Use [references/google-style-map.md](references/google-style-map.md) for canonical guide pages when a rule needs a source link, a deeper check, or a current word-list lookup. The guide is updated over time; refresh the relevant source rather than treating this skill as a frozen copy.

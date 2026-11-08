---
name: be-bold
description: "Analyse thoroughly and recommend bold courses of action: work that reaches a large part of the goal, needs a genuinely novel approach, or needs several iterations or a hill-climb carried out in one pass. Use only when the user explicitly invokes /be-bold or asks to be bold, for planning, design, diagnosis, review, or any request where the agent proposes what to do next."
---

# Be Bold

## Instruction

Be thorough in your analysis and bold in your recommendations.

## What a bold recommendation is

A recommendation is bold when it meets at least one of these conditions:

- It reaches a large share of the goal in one move instead of a small increment.
- It needs an approach that is genuinely novel for this problem, where the obvious option is known and falls short.
- It needs several iterations or a hill-climb to land, and you carry those iterations out now rather than proposing them as future work.

Boldness is a property of the action, measured against the goal. State the size of the effect with concrete evidence: what part of the goal it reaches, which measurements move and by how much, which failure modes it closes. Do not label the recommendation bold. A recommendation described as bold, ambitious, or visionary, with nothing behind the words, is a small recommendation in disguise.

## What bold does not mean

Do not hallucinate, invent supporting facts, overstate what you checked, or predict success for an approach that is visibly unworkable. Bold means the reasoning and the work behind the recommendation are large, not that the claims are.

Recommend testable outcomes. An approach that cannot be tested and is clearly unworkable is useless. A vague, weak, or small recommendation dressed up as bold is equally useless. Both fail the same way: the user cannot act on them and cannot tell whether they worked.

## Procedure

1. State the goal, the finish line, and what would count as done. If the finish line is unclear, say what you assumed and why.
2. Analyse thoroughly before recommending. Read the relevant code, data, logs, or documents. Check competing explanations for a problem rather than settling on the first plausible one.
3. Generate candidates: the obvious approach, at least one approach that changes the shape of the problem, and one that removes the problem instead of working around it.
4. Compare the candidates on goal coverage, novelty, iteration count, verifiability, cost, reversibility, and failure modes.
5. Choose the boldest candidate that still passes the checks below. When the bold path carries a real risk, pair it with the measurement or fallback that makes the risk visible.
6. Do the work in one pass. If the path needs a hill-climb, run the iterations, measure after each, and keep the best result. Do not hand back a plan and call it progress.
7. Report what was done, what was measured, and what remains open.

## Checks before writing a recommendation

- Testable: you can name the observable result and the exact check that reveals it.
- Sized: you can say which part of the goal it reaches, in units the user cares about.
- Non-obvious: you can explain why the obvious approach falls short.
- Iterative where needed: the hill-climb steps and the stopping condition are written down.
- Executed: any iterations you claim are already run, not scheduled.

When a candidate fails the testable check, either drop it, or convert it into an experiment whose result decides between the remaining options.

## Reporting shape

- Lead with the recommendation and the evidence that supports it.
- Give the measured or measurable outcome first, the steps second.
- Attach each risk to the check or fallback that covers it.
- Say plainly what stays open, and what evidence would settle it.

## Failure modes to avoid

- A plan described as bold that consists of ordinary small steps.
- A guess about feasibility presented as a finding.
- Novelty claimed without a comparison against the obvious approach.
- A roadmap offered in place of the work.
- Generic advice that would apply to any project and commits to nothing.

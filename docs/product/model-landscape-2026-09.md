# Models, Agent Runtimes and ROVE's Evaluation Boundary

**Research date:** 2026-09-12
**Status:** Product assessment from public primary sources. These are external
results, not benchmarks run by ROVE or guarantees of adapter availability.

ROVE should evaluate configured robotics systems while retaining VLA support.
The evidence below supports a mix of visual reasoning, agent execution and learned
control. It does not establish that one model family has won the robotics market.

## What the Current Evidence Says

| System | What it contributes | Evidence boundary |
| --- | --- | --- |
| GPT-6 Astra | General reasoning, visual interaction, software tools and multistep execution through a runtime | OpenAI's release emphasizes computer use and professional tasks; those benchmarks do not measure manipulation reliability. [Release](https://openai.com/index/gpt-6-astra/) |
| Claude Fable 5.1 | Visual understanding and sustained tool-using agent work, including planning and checking outputs | Anthropic's release focuses on coding, knowledge work and research. Robotics claims require separate task evidence. [Release](https://www.anthropic.com/claude-fable-and-mythos-5-1) and [model overview](https://www.anthropic.com/claude/fable) |
| Physical Intelligence π0.7 | A VLA conditioned by language, metadata and visual subgoals, with higher-level guidance | PI reports stronger generalization, but its air-fryer example needs coaching and then an adapted high-level policy to complete autonomously. This is progress within VLA systems, with adaptation and runtime choices affecting results. [PI report](https://www.pi.website/blog/pi07) |
| Gemini Robotics 2 family | Separate embodied-reasoning and VLA models, including an on-device VLA | Google's current architecture explicitly combines reasoning and learned motor control. Product descriptions do not establish universal robot compatibility or deployment reliability. [Model family](https://deepmind.google/models/gemini-robotics/) |

π0.7 was announced on April 16, 2026. No higher named PI release was verified in
this review. The official OpenPI repository currently lists π0, π0-FAST and π0.5;
π0.7 research publication should not be presented as a publicly downloadable
checkpoint or an already supported ROVE integration. [OpenPI](https://github.com/Physical-Intelligence/openpi)

Robocurve's September 4 comparison reports 19/20 block-to-bowl completions for
Astra versus 8/20 for Fable 5.1, but both achieve 2/20 on puzzle insertion. It links
the counted trials, transcripts and recordings. This is evidence about two tasks
under one setup, with human grading; π0.7 was not in that comparison.
[Experiment and records](https://openai.robocurve.org/gpt-6-astra/)

This review found no shared-protocol Astra/Fable-versus-π0.7 comparison. Ranking
them using unrelated vendor demonstrations would confound tasks, embodiment,
adaptation, controller and grading. Small laboratory samples also do not settle
production reliability or commercial adoption.

## Product Interpretation

A VLM describes vision-language capability; a VLA adds an action-producing model
interface; an agent is the running system around one or more models. A frontier
multimodal agent can therefore use visual reasoning and call a VLA or a conventional
controller. Supporting one does not require excluding another.

The strategic inference is to compare **the complete configured pipeline on the
customer's task**, including its execution runtime. ROVE should preserve:

- The model/checkpoint, prompts, memory/reset policy, tools, action interface and
  controller that produced the result, with unavailable identities explicit.
- Task outcomes, repeatability, constraint evidence, assistance and timing where
  actually recorded, rather than a ranking based on model category.
- Separate scopes for image/plan assessment, grading recorded episodes and fresh
  robot trials. Reusing an episode cannot measure a different system's behavior.
- Both simple optional-stage strategies and complete external agent systems,
  without inventing access to their internal activity.

This retains the promise **“ROVE evaluates robotics agent pipelines on your task.”**
The platform persona is **Platform / AI Infrastructure Team**; Azure is an
integration example alongside local and other hosted environments.

## ROVE's Architecture Direction

ROVE develops its own evaluation and execution harness around the configured
pipeline. The architecture separates customer agent behavior from trial recording,
grading and comparison so each can evolve under explicit contracts. See
[ADR-023](../architecture/ADR-023-evaluation-and-execution-harnesses.md) for the
accepted direction and pending implementation work.

Prioritize customer onboarding, success definitions, SME review, frozen datasets,
telemetry and baseline/ablation inspection. Azure and Microsoft Fabric integration
are future directions; they do not change the meaning of a case, trial or outcome.
These are product requirements, not claims of shipped capability or unique market
coverage.

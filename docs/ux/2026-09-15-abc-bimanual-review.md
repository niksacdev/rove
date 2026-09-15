# ABC bimanual interface review

## Design decisions

The comparison uses ROVE's shared Foundry-inspired tokens, navigation, controls,
strategy progress lanes and trace inspector. The model list is a table with
selection, strategy identity, model type and readiness columns. The large trial
calculator updates as the user changes scene or model seeds.

The task is intentionally fixed to the supported bottles task in this first
integration. The interface does not imply support for arbitrary robots or
customer scenes. Operator-controlled interpreter paths, checkpoints and runtime
configuration are managed through the CLI, not submitted by the browser.

Success has a visible explanation before review. Review displays the exact
choice of goal reached during the episode or goal satisfied at the end. Model
generated text is not presented as measured simulator success or as available
AI improvement advice.

## Accessibility and behavior checks

- Native labels, inputs, tables, buttons and links support keyboard operation.
- Focus moves to review, running and results headings after transitions.
- Readiness and outcomes have text labels; color alone carries no meaning.
- Status updates use live regions, while action controls retain native states.
- Tables can scroll horizontally on narrow screens; form columns stack.
- Reduced-motion preferences disable the panel entrance animation.
- Model labels and error text use text nodes, not executable HTML.
- Review precedes every launch, and unready models cannot be launched.
- Inspecting evidence preserves the progress view instead of replacing it.

## Validation scope

Automated DOM tests exercise empty setup, unavailable model selection, safe label
rendering, the dynamic count, review without execution, edit invalidation,
departure warning, live progress, trace navigation, reload of campaign results
and rejection of changed configuration. API tests check bounded requests,
operator-path isolation, selected-model readiness, configuration confirmation,
shared campaign execution, early durable trial IDs and cancellation.

Browser inspection checked the real missing-runtime page and a separate,
explicitly labelled UI fixture for the configured model table and review
transition. The fixture could not launch models or write evaluation records.
The review panel showed the count, criterion and distinct edit/run actions
without clipping at the inspected desktop viewport.

These checks validate interface behavior and transport contracts. They do not
establish trained-policy performance, task success on real hardware, or a
completed human usability study. GPU execution remains a separate validation
step.

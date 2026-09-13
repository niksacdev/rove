# Trial-first entry and followup review — 2026-09-13

The primary entry now leads to the original image/task composer. Campaigns remains available as a secondary route, and its collection has the same name in its heading and navigation.

A completed trial offers Add to campaign and Inspect saved trial using the server's durable trial ID. Multi-strategy outputs retain separate identities. Reopening a saved result restores this followup. Campaign cards expose Improve only after execution has completed, while preserving their existing result/report links.

The controls are native links with readable labels, visible focus styling, wrapped layouts and sufficient minimum control height. No additional setup form is introduced on the trial output screen. Acceptance and execution completion remain distinct: a rejected output can still become the starting point for a campaign.

Automated checks cover landing priority, preserved composer inputs during navigation, exact encoded trial and campaign links, no execution during followup rendering or reopening, missing/error trial exclusion, and completed-only Improve visibility during polling. These are DOM and flow checks; they do not replace a full accessibility audit or user study. The integrated mock chat-to-campaign-to-baseline-to-Improve journey was browser-verified on the isolated local preview.

## Library buttons and Settings placement

All saved trials and Sample cases are full-width outlined sidebar controls with restrained icons and readable text. They retain native link semantics and the sample action preserves the existing in-app navigation behavior.

Settings now lives once in a shared, right-aligned footer below the workspace. The footer reserves its own space rather than floating over the composer or page controls. Theme selection remains in the header. Opening Settings from the trial runner still preserves the current input and streamed output. Tests verify the single shared Settings destination, sidebar destinations and accessible icon treatment; Desktop dark mode and 390px light mode were visually checked: sidebar controls render as buttons, Settings sits below content at the right edge, and the composer remains unobstructed. The temporary viewport and theme overrides were restored.
